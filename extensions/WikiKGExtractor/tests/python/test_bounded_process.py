"""Actual child-process controls, exercised only on a finite Linux cgroup."""
from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import bounded_process as supervisor


@unittest.skipUnless(sys.platform == "linux", "Linux resource-control contract")
class BoundedProcessTest(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.memory_control = supervisor.require_linux_memory_budget(1024 * 1024 * 1024)
            supervisor.require_owned_group_support()
        except ValueError as error:
            self.skipTest(str(error))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_child(self, code: str, **limits):
        options = {"timeout": 5, "max_output_bytes": 4096, "memory_bytes": 1024 * 1024 * 1024,
                   **limits}
        return supervisor.run_bounded([sys.executable, "-c", code], **options)

    def test_stdout_and_stderr_share_one_capture_budget(self):
        result = self.run_child("import os; os.write(1,b'a'*800); os.write(2,b'b'*800)",
                                max_output_bytes=1000)
        self.assertEqual(result.reason, "output_limit")
        self.assertLessEqual(len(result.stdout) + len(result.stderr), 1000)

    def test_shared_host_filesystem_is_refused_before_taking_receipt_lock(self):
        mountinfo = f"1 0 0:1 / / rw - overlay overlay rw\n2 1 0:2 / {self.root} rw - virtiofs shared rw\n"
        with patch.object(Path, "read_text", return_value=mountinfo):
            with self.assertRaisesRegex(ValueError, "receipt_filesystem_unsupported"):
                supervisor.require_inherited_lock_filesystem(self.root)
            supervisor.require_inherited_lock_filesystem(Path("/").resolve())

    def test_unavailable_mount_identity_fails_closed(self):
        with patch.object(Path, "read_text", side_effect=OSError("unavailable")):
            with self.assertRaisesRegex(ValueError, "receipt_filesystem_unsupported"):
                supervisor.require_inherited_lock_filesystem(self.root)

    def test_address_space_limit_and_finite_cgroup_are_real(self):
        result = self.run_child("""import json, resource
limits = resource.getrlimit(resource.RLIMIT_AS)
try:
    bytearray(2 * 1024 * 1024 * 1024)
except MemoryError:
    print(json.dumps({'limits': limits, 'allocation_refused': True}))
else:
    raise SystemExit('allocation incorrectly allowed')
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads(result.stdout)
        self.assertTrue(evidence["allocation_refused"])
        self.assertEqual(evidence["limits"], [1024 * 1024 * 1024] * 2)
        self.assertGreater(self.memory_control["memory_max_bytes"], 0)

    def test_file_write_cap_reports_limit_without_a_large_artifact(self):
        output = self.root / "limited.bin"
        result = self.run_child(f"""import os, signal
signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
fd = os.open({str(output)!r}, os.O_WRONLY | os.O_CREAT, 0o600)
os.write(fd, b'x'*4096)
os.write(fd, b'x')
""", file_bytes=512)
        self.assertEqual(result.reason, "output_limit")
        self.assertLessEqual(output.stat().st_size, 512)

    def test_deadline_stops_descendants_but_not_an_independent_process(self):
        pid_file = self.root / "descendant.pid"
        sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                    start_new_session=True)
        try:
            result = self.run_child(f"""import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))
time.sleep(30)
""", timeout=0.5)
            self.assertEqual(result.reason, "deadline")
            self.assertLess(result.elapsed_seconds, 5)
            self.assertIsNone(sentinel.poll())
            descendant = Path(f"/proc/{int(pid_file.read_text())}/stat")
            if descendant.exists():
                self.assertEqual(descendant.read_text().split()[2], "Z")
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=5)

    def test_interrupt_reaps_the_owned_leader(self):
        selector = selectors.DefaultSelector()
        real_popen = subprocess.Popen
        children = []

        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child

        with patch.object(supervisor.selectors, "DefaultSelector", return_value=selector), \
             patch.object(selector, "select", side_effect=KeyboardInterrupt), \
             patch.object(supervisor.subprocess, "Popen", side_effect=launch):
            with self.assertRaises(KeyboardInterrupt):
                self.run_child("import time; time.sleep(30)")
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].returncode)

    def test_sigint_signal_reaps_owned_process_group(self):
        """Exercise OS-delivered SIGINT, not only an injected Python exception."""
        ready = self.root / "ready"
        code = f"""import json, os, pathlib, subprocess, sys, time
descendant = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
identities = []
for pid in (os.getpid(), descendant.pid):
    identities.append({{'pid': pid, 'started': pathlib.Path('/proc/' + str(pid) + '/stat').read_text().split()[21]}})
pending = pathlib.Path({str(ready.with_suffix('.pending'))!r})
pending.write_text(json.dumps(identities))
pending.replace({str(ready)!r})
time.sleep(30)
"""
        harness = f"""import sys
sys.path.insert(0, {str(Path(__file__).resolve().parents[2] / 'tools')!r})
import bounded_process
bounded_process.run_bounded([sys.executable, '-c', {code!r}], timeout=20,
  max_output_bytes=1024, memory_bytes=1024*1024*1024)
"""
        parent = subprocess.Popen([sys.executable, "-c", harness], start_new_session=True)
        try:
            # Sentinel file is a deterministic child-start handshake.
            import time
            deadline = time.monotonic() + 10
            while not ready.exists() and parent.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "supervised child never reached ready state")
            identities = json.loads(ready.read_text())
            parent.send_signal(signal.SIGINT)
            self.assertEqual(parent.wait(timeout=10), -signal.SIGINT)
            for identity in identities:
                status = Path(f"/proc/{identity['pid']}/stat")
                if status.exists():
                    fields = status.read_text().split()
                    if fields[21] == identity["started"]:
                        self.assertEqual(fields[2], "Z", "owned child survived SIGINT cleanup")
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
