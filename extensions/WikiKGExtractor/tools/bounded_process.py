"""Linux process-tree supervision for the bounded offline runner."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Sequence


def require_inherited_lock_filesystem(path: Path) -> None:
    """Refuse Linux mounts that may translate flock to parent-owned host locks.

    Docker Desktop virtiofs releases such locks on parent death despite a live
    child holding the descriptor. Native Linux volumes and tmpfs preserve it.
    """
    import sys

    if sys.platform != "linux":
        return
    native = {"ext2", "ext3", "ext4", "xfs", "btrfs", "tmpfs", "ramfs", "overlay"}
    resolved = path.resolve(strict=True)
    matches = []
    try:
        for row in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
            fields, details = row.split(" - ", 1)
            target = fields.split()[4]
            for escaped, value in ((r"\040", " "), (r"\011", "\t"), (r"\012", "\n"), (r"\134", "\\")):
                target = target.replace(escaped, value)
            mount = Path(target)
            if mount == resolved or mount in resolved.parents:
                matches.append((len(mount.parts), details.split()[0]))
    except (OSError, ValueError, IndexError) as error:
        raise ValueError("receipt_filesystem_unsupported") from error
    if not matches or max(matches)[1] not in native:
        raise ValueError("receipt_filesystem_unsupported")


@dataclass
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    reason: str | None
    elapsed_seconds: float


def linux_memory_identity() -> dict[str, int | str]:
    """Return current cgroup-v2 path and finite aggregate memory.max value."""
    if not os.path.exists("/proc/self/cgroup"):
        raise ValueError("enforceable Linux memory budget is unavailable")
    try:
        with open("/proc/self/cgroup", "rt", encoding="ascii") as stream:
            rows = stream.read(65536).splitlines()
        rel = next(row.split("::", 1)[1] for row in rows if row.startswith("0::"))
        root = os.path.realpath("/sys/fs/cgroup")
        group = os.path.realpath(os.path.join(root, rel.lstrip("/")))
        if os.path.commonpath((root, group)) != root:
            raise ValueError
        with open(os.path.join(group, "memory.max"), "rt", encoding="ascii") as stream:
            value = stream.read(128).strip()
        if value == "max" or int(value) <= 0:
            raise ValueError
        return {"cgroup_path": rel, "memory_max_bytes": int(value)}
    except (OSError, StopIteration, ValueError):
        raise ValueError("finite cgroup-v2 memory.max is required") from None


def require_linux_memory_budget(memory_bytes: int) -> dict[str, int | str]:
    """Require Linux RLIMIT_AS plus finite cgroup-v2 aggregate memory control."""
    if not memory_bytes or memory_bytes < 1 or not os.path.exists("/proc/self/cgroup"):
        raise ValueError("enforceable Linux memory budget is unavailable")
    return linux_memory_identity()


def require_owned_group_support() -> None:
    if (os.name != "posix" or not all(hasattr(os, name) for name in
            ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT"))
            or not hasattr(signal, "pthread_sigmask")):
        raise ValueError("non-reaping POSIX process-group supervision is unavailable")


def child_has_exited(child: subprocess.Popen) -> bool:
    """Observe an owned child without releasing its PID for reuse."""
    if child.returncode is not None:
        raise ValueError("owned child was already reaped")
    try:
        return os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except ChildProcessError:
        raise ValueError("owned child is no longer waitable") from None


def stop_owned_group(child: subprocess.Popen, *, graceful: bool = False) -> None:
    """Signal only a still-owned, unreaped session leader, then reap it."""
    child_has_exited(child)
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
    try:
        if graceful:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            time.sleep(2)
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def run_bounded(argv: Sequence[str], *, timeout: float, max_output_bytes: int,
                memory_bytes: int, file_bytes: int | None = None,
                owned_output_dir: Path | None = None,
                total_output_limit: int | None = None,
                lock_fd: int | None = None,
                env: dict[str, str] | None = None) -> ProcessResult:
    """Run one owned process group; bound output, deadline, and each process VA."""
    require_owned_group_support()
    if timeout <= 0 or max_output_bytes < 1 or memory_bytes < 1:
        raise ValueError("process limits must be positive")
    require_linux_memory_budget(memory_bytes)
    import resource

    started = time.monotonic()

    def limits() -> None:
        os.umask(0o077)
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        if file_bytes is not None:
            resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))

    # Drain concurrently without retaining unbounded output.
    selector = selectors.DefaultSelector()
    child = None
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    reason = None
    total = 0
    deadline = started + timeout

    def output_reason() -> str | None:
        if owned_output_dir is None or total_output_limit is None:
            return None
        retained = 0
        for root, dirs, files in os.walk(owned_output_dir, followlinks=False):
            for name in dirs + files:
                item = Path(root) / name
                if item.is_symlink():
                    return "unsafe_output_path"
                if item.is_file():
                    retained += item.stat().st_size
        return "output_limit" if retained > total_output_limit else None

    try:
        child = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True, close_fds=True,
                                 pass_fds=() if lock_fd is None else (lock_fd,),
                                 env=env, preexec_fn=limits)
        assert child.stdout is not None and child.stderr is not None
        selector.register(child.stdout, selectors.EVENT_READ, "stdout")
        selector.register(child.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map() or not child_has_exited(child):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = "deadline"
                break
            reason = output_reason()
            if reason:
                break
            for key, _ in selector.select(min(remaining, 0.2)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                total += len(chunk)
                available = max(0, max_output_bytes - sum(len(buffer) for buffer in buffers.values()))
                buffers[key.data].extend(chunk[:available])
                if total > max_output_bytes:
                    reason = "output_limit"
                    break
            if reason:
                break
    except KeyboardInterrupt:
        reason = "cancelled"
        raise
    except BaseException:
        reason = "supervisor_error"
        raise
    finally:
        try:
            # Keep the leader unreaped until group cleanup, even after normal exit.
            if child is not None:
                stop_owned_group(child, graceful=reason is not None)
        finally:
            selector.close()
            if child is not None:
                for stream in (child.stdout, child.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()
    reason = reason or output_reason()
    if child.returncode == -signal.SIGXFSZ:
        reason = reason or "output_limit"
    return ProcessResult(child.returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"]),
                         reason, time.monotonic() - started)
