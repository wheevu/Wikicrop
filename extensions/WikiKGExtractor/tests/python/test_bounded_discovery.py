from __future__ import annotations

import hashlib
import json
import os
import signal
import shutil
import sys
import tempfile
import time
import unittest
import subprocess
import copy
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import bounded_discovery as discovery  # noqa: E402


class BoundedDiscoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.out = self.root / "private"
        self.out.mkdir(mode=0o700)
        os.chmod(self.out, 0o700)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir(mode=0o700)
        self.web = self.root / "web"
        self.web.mkdir(mode=0o700)
        os.environ["DOCUMENT_ROOT"] = str(self.web)
        self.source = self.run_dir / "page.json"
        self.inventory = self.run_dir / "inventory.json"
        self.policy = {"heldout_ids": set(), "heldout_titles": set(),
                       "manifest_sha256": discovery.MANIFEST_SHA256}
        # v1 unit cases exercise file and receipt behavior, not host cgroup availability.
        self.memory_budget_patch = patch.object(discovery, "require_linux_memory_budget",
                                                return_value="synthetic-memory-control")
        self.memory_budget_patch.start()
        self.write_page()
        self.write_inventory()

    def tearDown(self) -> None:
        self.memory_budget_patch.stop()
        os.environ.pop("DOCUMENT_ROOT", None)
        self.temp.cleanup()

    def write_page(self, *, page_id: int = 1, revision_id: int = 2,
                   title: str = "Rice", crop: str = "Rice", kind: str = "species",
                   text: str = "Synthetic") -> None:
        doc = {"page": {"title": title, "page_id": page_id, "revision_id": revision_id,
                         "kind": kind, "crop": crop, "wikitext": text,
                         "content_hash": hashlib.sha256(text.encode()).hexdigest()}}
        self.source.write_text(json.dumps(doc), encoding="utf-8")
        os.chmod(self.source, 0o600)

    def entry(self, *, page_id: int = 1, revision_id: int = 2, title: str = "Rice",
              crop: str = "Rice", kind: str = "species", source: Path | None = None,
              text: str = "Synthetic", eligible: bool = True, **extra: object) -> dict:
        source_path = source or self.source
        source_file = source_path.name if source_path.parent == self.run_dir else str(source_path)
        return {"page_id": page_id, "revision_id": revision_id, "title": title, "kind": kind,
                "crop": crop, "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                "namespace": 0, "redirect_target": None, "redirect_namespace": 0, "redirect_external": False,
                "eligible": eligible, "reason": None if eligible else "synthetic_skip",
                "resolved_title": title if eligible else None,
                "resolved_page_id": page_id if eligible else None,
                "resolved_revision_id": revision_id if eligible else None,
                "source_file": source_file, **extra}

    def write_inventory(self, entries: list[dict] | None = None, *, raw: str | None = None) -> None:
        if raw is None:
            doc = {"schema_version": "wikikg.offline-source-inventory.v1",
                   "protocol_sha256": discovery.PROTOCOL_SHA256,
                   "heldout_manifest_sha256": discovery.MANIFEST_SHA256,
                   "source_identity": {"domain_id": "syntheticwiki", "database_name": "synthetic_db", "read_role": "primary"},
                   "actor": "Named reviewer", "pages": entries or [self.entry()]}
            raw = json.dumps(doc)
        self.inventory.write_text(raw, encoding="utf-8")
        os.chmod(self.inventory, 0o600)

    @staticmethod
    def metadata_row(page_id: int, title: str, *, redirect_target: str | None = None) -> dict:
        return {"page_id": page_id, "revision_id": page_id + 1, "title": title, "namespace": 0,
                "redirect_target": redirect_target, "redirect_namespace": 0, "redirect_external": False,
                "eligible": False, "reason": "synthetic_metadata_only", "redirect_chain": []}

    @staticmethod
    def synthetic_worker(command: list[str], **kwargs: object):
        output = Path(command[command.index("--output-dir") + 1])
        raw = json.loads((output / "raw_data.json").read_text(encoding="utf-8"))
        page = raw["pages"][0]
        claims = {"schema_version": "1.0",
                  "metadata": {"extractor_version": "2.3.1", "extraction_method": "rules",
                               "review_state": "pending", "source_pages": [{k: page[k] for k in
                                   ("title", "page_id", "revision_id", "content_hash")}]},
                  "summary": {"claim_count": 0, "traceability_complete_count": 0, "claims_by_predicate": {}},
                  "claims": []}
        (output / "candidate_claims.json").write_text(json.dumps(claims), encoding="utf-8")
        for name in discovery.EXPECTED_ARTIFACTS - {"raw_data.json", "candidate_claims.json"}:
            (output / name).write_text("synthetic artifact", encoding="utf-8")
        return discovery.ProcessResult(0, b"", b"", None, 0.0)

    def run_discovery(self, **kwargs: object) -> dict:
        return discovery.run(self.inventory, self.out, "Named reviewer", exclusion_policy=self.policy, **kwargs)

    def batch_receipt(self, *, limit: int = discovery.MAX_OUTPUT) -> tuple[dict, Path, Path]:
        classifications = self.run_dir / "classifications.json"
        classifications.write_text("{}", encoding="utf-8")
        os.chmod(classifications, 0o600)
        manifest = self.run_dir / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        os.chmod(manifest, 0o600)
        page = {"page_id": 1, "revision_id": 2, "title": "Rice", "kind": "species",
                "crop": "Rice", "namespace": 0, "redirect_target": None,
                "redirect_namespace": 0, "redirect_external": False}
        receipt = {"schema_version": "wikikg.bounded-discovery-receipt.v2",
                   "config": {"actor": "Named reviewer", "classifications_sha256": discovery.sha256_file(classifications, 2 * 1024 * 1024),
                              "manifest_sha256": "synthetic-manifest", "memory_control": "synthetic-cgroup",
                              "runtime_root": str(self.root), "document_root": str(self.web),
                              "source_identity": {"domain_id": "syntheticwiki", "database_name": "synthetic_db", "read_role": "primary"},
                              "limits": {"timeout_seconds": 30, "memory_bytes": 64 * 1024 * 1024,
                                         "max_output_bytes": limit}},
                   "inventory": {"pages": [page]},
                   "prepare": {"status": "completed", "attempts": []},
                   "batches": [{"index": 0, "pages": [page], "status": "pending", "attempts": []}]}
        discovery._write_atomic_receipt(self.out / "receipt.json", receipt, limit)
        return receipt, classifications, manifest

    def execute_synthetic_batch(self, *, retry: bool = False, limit: int = discovery.MAX_OUTPUT):
        receipt_path = self.out / "receipt.json"

        def load_receipt(output: Path):
            return discovery.strict_json((output / "receipt.json").read_bytes())

        patches = [patch.object(discovery, "_load_v2", side_effect=load_receipt),
                   patch.object(discovery, "linux_memory_identity", return_value="synthetic-cgroup"),
                   patch.object(discovery, "read_manifest", return_value=({}, "synthetic-manifest"))]
        for item in patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(patches)])
        _, classifications, manifest = self.batch_receipt(limit=limit) if not receipt_path.exists() else (None, self.run_dir / "classifications.json", self.run_dir / "manifest.json")
        return discovery._execute_batch(self.out, 0, actor="Named reviewer", manifest_path=manifest,
                                       classifications_path=classifications, runtime_root=self.root,
                                       document_root=self.web, timeout=30, memory_bytes=64 * 1024 * 1024,
                                       max_output_bytes=limit, retry=retry)

    def test_excluded_paths_are_never_opened_and_forged_eligibility_refuses(self) -> None:
        bad = self.entry(page_id=90, revision_id=91, title="Held out", source=Path("/absent/heldout.json"),
                         eligible=True, resolved_title="Held out")
        self.policy = {"heldout_ids": {90}, "heldout_titles": {"held out"},
                       "manifest_sha256": discovery.MANIFEST_SHA256}
        self.write_inventory([self.entry(eligible=False, source=Path("/absent/skip.json")), bad])
        with self.assertRaisesRegex(ValueError, "falsely marks"):
            discovery.load_jobs(discovery.strict_json(self.inventory.read_bytes()), self.run_dir,
                                 10, 100_000, self.policy)

    def test_manifest_test_alias_redirect_closure_is_transitive(self) -> None:
        manifest = {"pages": [{"split": "test", "page_id": 80,
                               "requested_title": "Held title", "resolved_title": "Held title"}]}
        rows = [self.metadata_row(80, "Held title"),
                self.metadata_row(81, "Alias two", redirect_target="Held title"),
                self.metadata_row(82, "Alias one", redirect_target="Alias two")]
        policy = discovery.derive_exclusion_policy(manifest, {"pages": rows})
        self.assertIn("alias one", policy["heldout_titles"])
        forged = {"pages": [self.entry(page_id=82, revision_id=83, title="Alias one", source=Path("/absent/alias"),
                                        eligible=True, redirect_target="Alias two")],
                  "source_identity": {"domain_id": "syntheticwiki", "database_name": "synthetic_db", "read_role": "primary"},
                  "schema_version": "wikikg.offline-source-inventory.v1"}
        with self.assertRaisesRegex(ValueError, "unsupported or unresolved redirect eligible"):
            discovery.validate_eligibility(forged, policy)

    def test_held_alias_excludes_terminal_and_siblings_before_source_access(self) -> None:
        manifest = {"pages": [{"split": "test", "page_id": 80, "title": "Held title"}]}
        metadata = [self.metadata_row(80, "Held title", redirect_target="Middle"),
                    self.metadata_row(81, "Middle", redirect_target="Terminal"),
                    self.metadata_row(1, "Terminal"), self.metadata_row(2, "Sibling", redirect_target="Terminal"),
                    self.metadata_row(3, "Unrelated")]
        metadata[0].update(resolved_title="Unrelated", resolved_page_id=3)
        policy = discovery.derive_exclusion_policy(manifest, {"pages": metadata})
        self.assertTrue({"held title", "middle", "terminal", "sibling"} <= policy["heldout_titles"])
        self.assertEqual(policy["heldout_ids"], {80, 81, 1, 2})
        self.assertNotIn("unrelated", policy["heldout_titles"])

        # Redirect raw metadata is authoritative even if the inventory lies about its resolution.
        terminal = self.entry(page_id=1, revision_id=2, title="Terminal", source=Path("/absent/held-terminal"),
                              eligible=True, resolved_title="Terminal", resolved_page_id=1)
        unrelated = self.entry(page_id=3, revision_id=4, title="Unrelated")
        inventory = {"schema_version": "wikikg.offline-source-inventory.v1",
                     "source_identity": {"domain_id": "wiki", "database_name": "db", "read_role": "primary"},
                     "pages": [terminal, unrelated]}
        with patch.object(discovery, "bounded_read", wraps=discovery.bounded_read) as read:
            with self.assertRaisesRegex(ValueError, "falsely marks"):
                discovery.load_jobs(inventory, self.run_dir, 10, 100_000, policy)
        read.assert_not_called()

    def test_terminal_alias_group_closure_is_order_independent_and_identity_based(self) -> None:
        base = [self.metadata_row(80, "Held alias", redirect_target="Terminal"),
                self.metadata_row(1, "Terminal"), self.metadata_row(2, "Sibling", redirect_target="Terminal")]
        group = {"held alias", "terminal", "sibling"}
        cases = [({"split": "test", "page_id": 80, "title": "Held alias"}, group),
                 ({"split": "test", "page_id": 80}, group),
                 ({"split": "test", "title": "HELD_alias"}, group),
                 ({"split": "test", "page_id": 1}, group)]
        for held, expected in cases:
            with self.subTest(held=held):
                for rows in (base, list(reversed(base))):
                     policy = discovery.derive_exclusion_policy({"pages": [held]}, {"pages": rows})
                     self.assertTrue(expected <= policy["heldout_titles"])
                     self.assertEqual(policy["heldout_ids"], {80, 1, 2})
        title_policy = discovery.derive_exclusion_policy(
            {"pages": [{"split": "test", "title": "Terminal"}]}, {"pages": base})
        self.assertTrue({"held alias", "terminal", "sibling"} <= title_policy["heldout_titles"])

    def test_invalid_redirect_components_do_not_contaminate_unrelated_groups(self) -> None:
        rows = [self.metadata_row(80, "Held", redirect_target="Missing"),
                self.metadata_row(81, "Cycle A", redirect_target="Cycle B"),
                self.metadata_row(82, "Cycle B", redirect_target="Cycle A"),
                self.metadata_row(83, "External", redirect_target="Held"),
                self.metadata_row(84, "Other")]
        rows[3]["redirect_external"] = True
        rows[3]["reason"] = "external_redirect"
        policy = discovery.derive_exclusion_policy(
            {"pages": [{"split": "test", "page_id": 80, "title": "Held"}]}, {"pages": rows})
        self.assertIn("held", policy["heldout_titles"])
        self.assertNotIn("other", policy["heldout_titles"])
        self.assertNotIn("cycle a", policy["heldout_titles"])
        self.assertNotIn("cycle b", policy["heldout_titles"])

    def test_private_inventory_strict_json_duplicate_and_nonfinite_refusal(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.write_inventory(raw='{"schema_version":"a","schema_version":"b","pages":[]}')
            discovery._private_inventory(self.inventory)
        with self.assertRaisesRegex(ValueError, "non-finite"):
            discovery.strict_json(b'{"x":NaN}')

    def test_metadata_rejects_casefold_collision_before_source_open(self) -> None:
        first = self.entry(source=Path("/missing/first"))
        second = self.entry(page_id=9, revision_id=10, title="rice", source=Path("/missing/second"))
        inventory = {"schema_version": "wikikg.offline-source-inventory.v1",
                     "source_identity": {"domain_id": "wiki", "database_name": "db", "read_role": "primary"},
                     "pages": [first, second]}
        with self.assertRaisesRegex(ValueError, "ambiguous normalized"):
            discovery.load_jobs(inventory, self.run_dir, 10, discovery.MAX_BYTES, self.policy)

    def test_inventory_requires_real_primary_database_identity(self) -> None:
        inventory = discovery.strict_json(self.inventory.read_bytes())
        inventory["source_identity"]["read_role"] = "replica"
        with self.assertRaisesRegex(ValueError, "primary source database identity"):
            discovery.validate_inventory_metadata(inventory)

    def test_source_identity_hash_and_page_byte_bound(self) -> None:
        self.write_page(crop="Different crop")
        with self.assertRaisesRegex(ValueError, "identity or classification"):
            discovery.load_jobs(discovery.strict_json(self.inventory.read_bytes()), self.run_dir, 10, 100_000, self.policy)
        self.write_page(text="x" * (discovery.MAX_PAGE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "within 5 MiB"):
            discovery.load_jobs(discovery.strict_json(self.inventory.read_bytes()), self.run_dir, 10, discovery.MAX_BYTES, self.policy)

    def test_job_outputs_are_paired_and_worker_is_silent_with_hard_file_cap(self) -> None:
        with patch.object(discovery, "run_bounded", side_effect=self.synthetic_worker) as worker:
            receipt = self.run_discovery()
        self.assertEqual(receipt["jobs"][0]["status"], "completed")
        job_dir = self.out / receipt["jobs"][0]["job_dir"] / "worker"
        self.assertTrue((job_dir / "raw_data.json").is_file())
        self.assertTrue((job_dir / "candidate_claims.json").is_file())
        self.assertEqual({p.name for p in job_dir.iterdir()}, discovery.EXPECTED_ARTIFACTS)
        self.assertEqual(stat_mode(job_dir), 0o700)
        self.assertEqual(stat_mode(job_dir / "raw_data.json"), 0o600)
        kwargs = worker.call_args.kwargs
        self.assertIn("--no-ai", worker.call_args.args[0])
        self.assertEqual(kwargs["env"]["GEMINI_API_KEY"], "")
        self.assertGreater(kwargs["file_bytes"], 0)
        self.assertLess(kwargs["file_bytes"], discovery.MAX_OUTPUT)
        self.assertEqual(kwargs["owned_output_dir"], self.out)
        self.assertEqual(kwargs["total_output_limit"], discovery.MAX_OUTPUT)

    def test_frozen_worker_produces_claim_document_with_paired_raw_artifact(self) -> None:
        receipt = self.run_discovery()
        job_dir = self.out / receipt["jobs"][0]["job_dir"] / "worker"
        self.assertEqual({p.name for p in job_dir.iterdir()}, discovery.EXPECTED_ARTIFACTS)
        raw = discovery.strict_json((job_dir / "raw_data.json").read_bytes())
        claims = discovery.strict_json((job_dir / "candidate_claims.json").read_bytes())
        self.assertEqual(raw["pages"][0]["content_hash"], claims["metadata"]["source_pages"][0]["content_hash"])
        self.assertEqual(receipt["jobs"][0]["status"], "completed")

    def test_receipt_rejects_empty_artifacts_changed_limits_missing_artifact_and_symlink(self) -> None:
        with patch.object(discovery, "run_bounded", side_effect=self.synthetic_worker):
            receipt = self.run_discovery()
            job = receipt["jobs"][0]
            job["artifacts"] = []
            discovery._write_atomic_receipt(self.out / "receipt.json", receipt)
            with self.assertRaisesRegex(ValueError, "artifact set"):
                self.run_discovery()
            receipt["jobs"][0]["artifacts"] = [
                {"name": n, "sha256": hashlib.sha256((self.out / job["job_dir"] / "worker" / n).read_bytes()).hexdigest(),
                 "bytes": (self.out / job["job_dir"] / "worker" / n).stat().st_size}
                for n in sorted(discovery.EXPECTED_ARTIFACTS)]
            discovery._write_atomic_receipt(self.out / "receipt.json", receipt)
            with self.assertRaisesRegex(ValueError, "limit"):
                self.run_discovery(max_bytes=discovery.MAX_BYTES - 1)
            missing = self.out / job["job_dir"] / "worker" / "graph_nodes_edges.json"
            missing.unlink()
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.run_discovery()
        receipt_path = self.out / "receipt.json"
        receipt_path.unlink()
        target = self.root / "outside-receipt"
        target.write_text("synthetic", encoding="utf-8")
        receipt_path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.run_discovery()

    def test_resume_rejects_duplicate_artifact_rows_and_forged_raw_identity(self) -> None:
        with patch.object(discovery, "run_bounded", side_effect=self.synthetic_worker):
            receipt = self.run_discovery()
            job = receipt["jobs"][0]
            job["artifacts"].append(dict(job["artifacts"][0]))
            discovery._write_atomic_receipt(self.out / "receipt.json", receipt)
            with self.assertRaisesRegex(ValueError, "artifact set"):
                self.run_discovery()
            job["artifacts"].pop()
            raw_path = self.out / job["job_dir"] / "worker" / "raw_data.json"
            forged = raw_path.read_bytes().replace(b'"page_id":1', b'"page_id":9')
            raw_path.write_bytes(forged)
            raw_meta = next(a for a in job["artifacts"] if a["name"] == "raw_data.json")
            raw_meta.update(bytes=len(forged), sha256=discovery.sha256_bytes(forged))
            job["raw_sha256"] = discovery.sha256_bytes(forged)
            discovery._write_atomic_receipt(self.out / "receipt.json", receipt)
            with self.assertRaisesRegex(ValueError, "current verified page"):
                self.run_discovery()

    def test_receipt_short_write_and_total_output_budget_fail_closed(self) -> None:
        with patch.object(discovery.os, "write", return_value=0):
            with self.assertRaisesRegex(ValueError, "short receipt write"):
                discovery._write_atomic_receipt(self.out / "receipt.json", {"jobs": []})
        with patch.object(discovery, "run_bounded", side_effect=self.synthetic_worker) as worker:
            with self.assertRaisesRegex(ValueError, "output byte limit|reserved output budget"):
                self.run_discovery(max_output_bytes=128)
            worker.assert_not_called()

    def test_setup_reservation_counts_setup_and_atomic_receipt_headroom_before_mutation(self) -> None:
        receipt = {"schema_version": "wikikg.bounded-discovery-receipt.v2", "jobs": []}
        marker = self.out / "batch-0000-attempt-0001-deadbeef"
        current = discovery._output_bytes(self.out)
        reserve = len(discovery._receipt_payload(receipt)) + discovery.MAX_RECEIPT_BYTES
        with self.assertRaisesRegex(ValueError, "setup exceeds reserved output budget"):
            discovery._reserve_setup(self.out, 64, receipt, current + reserve + 63)
        self.assertFalse(marker.exists())
        discovery._reserve_setup(self.out, 64, receipt, current + reserve + 64)
        self.assertFalse(marker.exists())

    def test_v2_batch_budget_refusal_leaves_receipt_and_attempt_tree_unchanged(self) -> None:
        limit = 1024 * 1024
        before, _, _ = self.batch_receipt(limit=limit)
        original = (self.out / "receipt.json").read_bytes()
        with patch.object(discovery, "_load_v2", side_effect=lambda root: discovery.strict_json((root / "receipt.json").read_bytes())), \
             patch.object(discovery, "linux_memory_identity", return_value="synthetic-cgroup"), \
             patch.object(discovery, "read_manifest", return_value=({}, "synthetic-manifest")), \
             patch.object(discovery, "_run_collector") as collector:
            with self.assertRaisesRegex(ValueError, "setup exceeds reserved output budget"):
                discovery._execute_batch(self.out, 0, actor="Named reviewer", manifest_path=self.run_dir / "manifest.json",
                                         classifications_path=self.run_dir / "classifications.json", runtime_root=self.root,
                                         document_root=self.web, timeout=30, memory_bytes=64 * 1024 * 1024,
                                         max_output_bytes=limit, retry=False)
        collector.assert_not_called()
        self.assertEqual((self.out / "receipt.json").read_bytes(), original)
        self.assertEqual([p.name for p in self.out.iterdir()], ["receipt.json"])
        self.assertEqual(before["batches"][0]["attempts"], [])

    def test_v2_setup_partial_write_is_recorded_and_retry_uses_fresh_attempt_dir(self) -> None:
        self.batch_receipt()
        real_write = discovery._write_private
        def fail_expected(path: Path, raw: bytes) -> None:
            real_write(path, raw)
            if path.name == "expected-metadata.json":
                raise OSError("synthetic setup failure")
        args = dict(actor="Named reviewer", manifest_path=self.run_dir / "manifest.json",
                    classifications_path=self.run_dir / "classifications.json", runtime_root=self.root,
                    document_root=self.web, timeout=30, memory_bytes=64 * 1024 * 1024,
                    max_output_bytes=discovery.MAX_OUTPUT)
        with patch.object(discovery, "_load_v2", side_effect=lambda root: discovery.strict_json((root / "receipt.json").read_bytes())), \
             patch.object(discovery, "linux_memory_identity", return_value="synthetic-cgroup"), \
             patch.object(discovery, "read_manifest", return_value=({}, "synthetic-manifest")), \
             patch.object(discovery, "_write_private", side_effect=fail_expected), \
             patch.object(discovery, "_run_collector") as collector:
            discovery._execute_batch(self.out, 0, retry=False, **args)
        collector.assert_not_called()
        state = discovery.strict_json((self.out / "receipt.json").read_bytes())
        failed = state["batches"][0]["attempts"][0]
        failed_dir = self.out / failed["attempt_dir"]
        self.assertEqual(failed["status"], "failed")
        self.assertTrue((failed_dir / "selected-pages.json").is_file())
        self.assertTrue((failed_dir / "expected-metadata.json").is_file())
        self.assertLessEqual(discovery._output_bytes(self.out), discovery.MAX_OUTPUT)
        with patch.object(discovery, "_load_v2", side_effect=lambda root: discovery.strict_json((root / "receipt.json").read_bytes())), \
             patch.object(discovery, "linux_memory_identity", return_value="synthetic-cgroup"), \
             patch.object(discovery, "read_manifest", return_value=({}, "synthetic-manifest")):
            with self.assertRaisesRegex(ValueError, "explicit --retry"):
                discovery._execute_batch(self.out, 0, retry=False, **args)
        with patch.object(discovery, "_load_v2", side_effect=lambda root: discovery.strict_json((root / "receipt.json").read_bytes())), \
             patch.object(discovery, "linux_memory_identity", return_value="synthetic-cgroup"), \
             patch.object(discovery, "read_manifest", return_value=({}, "synthetic-manifest")), \
             patch.object(discovery, "_run_collector", side_effect=ValueError("synthetic stop after setup")):
            discovery._execute_batch(self.out, 0, retry=True, **args)
        state = discovery.strict_json((self.out / "receipt.json").read_bytes())
        attempts = state["batches"][0]["attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertNotEqual(attempts[0]["attempt_dir"], attempts[1]["attempt_dir"])
        self.assertTrue((self.out / attempts[1]["attempt_dir"] / "selected-pages.json").is_file())

    def test_v2_setup_interrupt_and_restart_recovery_require_explicit_retry(self) -> None:
        self.batch_receipt()
        real_write = discovery._write_private
        def interrupt_selected(path: Path, raw: bytes) -> None:
            real_write(path, raw)
            if path.name == "selected-pages.json":
                raise KeyboardInterrupt()
        args = dict(actor="Named reviewer", manifest_path=self.run_dir / "manifest.json",
                    classifications_path=self.run_dir / "classifications.json", runtime_root=self.root,
                    document_root=self.web, timeout=30, memory_bytes=64 * 1024 * 1024,
                    max_output_bytes=discovery.MAX_OUTPUT)
        seams = (patch.object(discovery, "_load_v2", side_effect=lambda root: discovery.strict_json((root / "receipt.json").read_bytes())),
                 patch.object(discovery, "linux_memory_identity", return_value="synthetic-cgroup"),
                 patch.object(discovery, "read_manifest", return_value=({}, "synthetic-manifest")))
        with seams[0], seams[1], seams[2], patch.object(discovery, "_write_private", side_effect=interrupt_selected):
            with self.assertRaises(KeyboardInterrupt):
                discovery._execute_batch(self.out, 0, retry=False, **args)
        state = discovery.strict_json((self.out / "receipt.json").read_bytes())
        interrupted = state["batches"][0]["attempts"][0]
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertFalse(discovery.summarize_run(state, self.out)["complete"])
        self.assertLessEqual(discovery._output_bytes(self.out), discovery.MAX_OUTPUT)

        # Simulate a hard process stop: only the durable running intent and partial directory remain.
        interrupted["status"] = "running"
        state["batches"][0]["status"] = "running"
        discovery._write_atomic_receipt(self.out / "receipt.json", state, discovery.MAX_OUTPUT)
        discovery._resume_states(state, self.out)
        recovered = discovery.strict_json((self.out / "receipt.json").read_bytes())
        self.assertEqual(recovered["batches"][0]["attempts"][0]["status"], "interrupted")
        with patch.object(discovery, "_load_v2", side_effect=lambda root: discovery.strict_json((root / "receipt.json").read_bytes())), \
             patch.object(discovery, "linux_memory_identity", return_value="synthetic-cgroup"), \
             patch.object(discovery, "read_manifest", return_value=({}, "synthetic-manifest")), \
             patch.object(discovery, "_run_collector", side_effect=ValueError("synthetic stop after setup")):
            with self.assertRaisesRegex(ValueError, "explicit --retry"):
                discovery._execute_batch(self.out, 0, retry=False, **args)
            discovery._execute_batch(self.out, 0, retry=True, **args)
        recovered = discovery.strict_json((self.out / "receipt.json").read_bytes())
        attempts = recovered["batches"][0]["attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertNotEqual(attempts[0]["attempt_dir"], attempts[1]["attempt_dir"])

    def test_signal_after_completed_job_keeps_job_but_interrupts_batch_and_retry_is_append_only(self) -> None:
        """A completed page is not a completed batch; retry preserves its historical attempt."""
        receipt, classifications, manifest = self.batch_receipt()
        page = receipt["batches"][0]["pages"][0]
        prepared_page = {**page, "eligible": False, "reason": "awaiting_collection",
                         "redirect_chain": [], "resolved_page_id": page["page_id"],
                         "resolved_revision_id": page["revision_id"], "resolved_title": page["title"]}
        receipt["inventory"] = {"schema_version": "wikikg.offline-source-inventory.v1",
                                 "protocol_sha256": discovery.PROTOCOL_SHA256,
                                 "heldout_manifest_sha256": "synthetic-manifest",
                                 "source_identity": receipt["config"]["source_identity"],
                                 "actor": "Named reviewer", "pages": [prepared_page]}
        receipt["config"]["inventory_sha256"] = discovery.sha256_bytes(json.dumps(
            receipt["inventory"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        discovery._write_atomic_receipt(self.out / "receipt.json", receipt)

        def collector(runtime_root, output_parent, *args, **kwargs):
            php_dir = output_parent / "offline-synthetic"
            php_dir.mkdir(mode=0o700)
            source = php_dir / "page-1-2.json"
            source.write_text("{}", encoding="utf-8")
            os.chmod(source, 0o600)
            collected_page = {**prepared_page, "eligible": True, "reason": None,
                              "content_hash": "a" * 64, "source_file": "page.json"}
            collected = {"schema_version": "wikikg.offline-source-inventory.v1",
                         "source_identity": receipt["config"]["source_identity"], "pages": [collected_page]}
            attempt_inventory = php_dir / "inventory.json"
            attempt_inventory.write_text(json.dumps(collected), encoding="utf-8")
            os.chmod(attempt_inventory, 0o600)
            return {"inventory_path": str(attempt_inventory), "source_bytes": 8}, discovery.ProcessResult(0, b"", b"", None, 0)

        completed_job = {"page": {"page_id": 1, "revision_id": 2}, "page_id": 1, "revision_id": 2,
                         "status": "completed", "source_bytes": 8,
                         "output_bytes": 64, "claim_count": 0}
        extraction_calls = 0
        def extraction(*args, **kwargs):
            nonlocal extraction_calls
            extraction_calls += 1
            parent, slot = kwargs["run_state"]
            slot["extraction_receipt"] = {"schema_version": "wikikg.bounded-discovery-receipt.v1",
                                         "config": {}, "jobs": [dict(completed_job)],
                                         "source_validation": {"source_bytes": 8}}
            discovery._write_atomic_receipt(self.out / "receipt.json", parent)
            if extraction_calls == 1:
                os.kill(os.getpid(), signal.SIGINT)

        common = dict(actor="Named reviewer", manifest_path=manifest, classifications_path=classifications,
                      runtime_root=self.root, document_root=self.web, timeout=30,
                      memory_bytes=64 * 1024 * 1024, max_output_bytes=discovery.MAX_OUTPUT,
                      lock_fd=None)
        prior_sigint = signal.signal(signal.SIGINT, discovery._interrupted)
        try:
            with patch.object(discovery, "_load_v2", side_effect=lambda root: discovery.strict_json((root / "receipt.json").read_bytes())), \
                 patch.object(discovery, "linux_memory_identity", return_value="synthetic-cgroup"), \
                 patch.object(discovery, "read_manifest", return_value=({}, "synthetic-manifest")), \
                 patch.object(discovery, "_run_collector", side_effect=collector), \
                 patch.object(discovery, "run", side_effect=extraction):
                with self.assertRaises(KeyboardInterrupt):
                    discovery._execute_batch(self.out, 0, retry=False, **common)
        finally:
            signal.signal(signal.SIGINT, prior_sigint)
        state = discovery.strict_json((self.out / "receipt.json").read_bytes())
        first = state["batches"][0]["attempts"][0]
        first_snapshot = copy.deepcopy(first)
        self.assertEqual((first["status"], first["collection_status"], first["extraction_status"]),
                         ("interrupted", "completed", "interrupted"))
        self.assertEqual(first["extraction_receipt"]["jobs"][0]["status"], "completed")
        self.assertFalse(discovery.summarize_run(state, self.out)["complete"])
        with patch.object(discovery, "_load_v2", side_effect=lambda root: discovery.strict_json((root / "receipt.json").read_bytes())), \
             patch.object(discovery, "linux_memory_identity", return_value="synthetic-cgroup"), \
             patch.object(discovery, "read_manifest", return_value=({}, "synthetic-manifest")), \
             patch.object(discovery, "_run_collector", side_effect=collector), \
             patch.object(discovery, "run", side_effect=extraction):
            # No retry is refused before the collector can create another attempt.
            with self.assertRaisesRegex(ValueError, "explicit --retry"):
                discovery._execute_batch(self.out, 0, retry=False, **common)
            retried = discovery._execute_batch(self.out, 0, retry=True, **common)
        attempts = retried["batches"][0]["attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], first_snapshot)
        self.assertNotEqual(attempts[0]["attempt_dir"], attempts[1]["attempt_dir"])
        summary = discovery.summarize_run(retried, self.out)
        self.assertEqual(summary["attempt_count"], 2)
        self.assertEqual(summary["job_attempt_count"], 2)
        self.assertEqual(summary["extraction_states"]["completed"], 1)
        self.assertEqual(summary["extraction_source_bytes"], 16)
        self.assertEqual(summary["collection_source_bytes"], 16)
        self.assertTrue(summary["complete"])

    def test_prepare_persists_running_intent_before_attempt_directories(self) -> None:
        classifications = self.run_dir / "classifications.json"
        classifications.write_text("{}", encoding="utf-8")
        os.chmod(classifications, 0o600)
        manifest = self.run_dir / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        os.chmod(manifest, 0o600)
        protocol = discovery.strict_json(discovery.bounded_read(discovery.PROTOCOL, 1024 * 1024))
        schema_hash = protocol["artifacts"]["schema_sha256"]["candidate-claims.v1.schema.json"]
        real_mkdir = discovery._make_private_dir
        real_digest = discovery.sha256_file
        def verify_intent(path: Path) -> Path:
            receipt = discovery.strict_json((self.out / "receipt.json").read_bytes())
            self.assertEqual(receipt["prepare"]["status"], "running")
            self.assertFalse(path.exists())
            return real_mkdir(path)
        def digest(path: Path, limit: int) -> str:
            if path == discovery.WORKER:
                return discovery.WORKER_SHA256
            if path == discovery.PROTOCOL:
                return discovery.PROTOCOL_SHA256
            if path == discovery.CLAIM_SCHEMA:
                return schema_hash
            if path == classifications:
                return real_digest(path, limit)
            if path == manifest:
                return discovery.MANIFEST_SHA256
            return "a" * 64
        with patch.object(discovery, "require_linux_memory_budget", return_value="synthetic-cgroup"), \
             patch.object(discovery, "sha256_file", side_effect=digest), \
             patch.object(discovery, "read_manifest", return_value=({"pages": []}, discovery.MANIFEST_SHA256)), \
             patch.object(discovery, "_make_private_dir", side_effect=verify_intent), \
             patch.object(discovery, "_run_collector", side_effect=ValueError("synthetic collector stop")):
            with self.assertRaisesRegex(ValueError, "synthetic collector stop"):
                discovery._create_plan(self.out, "Named reviewer", manifest, classifications, self.root,
                                       self.web, timeout=30, memory_bytes=64 * 1024 * 1024,
                                       max_output_bytes=discovery.MAX_OUTPUT)
        state = discovery.strict_json((self.out / "receipt.json").read_bytes())
        self.assertEqual(state["prepare"]["status"], "failed")
        self.assertTrue(state["prepare"]["attempts"])

    def test_output_inventory_rejects_unexpected_root_entry_and_insecure_worker_directory(self) -> None:
        (self.out / "unexpected").write_text("synthetic", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unexpected private output file"):
            discovery._output_bytes(self.out)
        (self.out / "unexpected").unlink()
        job = self.out / ("a" * 20)
        job.mkdir(mode=0o700)
        worker = job / "worker"
        worker.mkdir(mode=0o700)
        os.chmod(worker, 0o755)
        with self.assertRaisesRegex(ValueError, "unsafe owner, mode, or type"):
            discovery._secure_worker_dir(worker, discovery.EXPECTED_ARTIFACTS)

    def test_resume_rejects_unknown_state_and_orphan_job_directory(self) -> None:
        with patch.object(discovery, "run_bounded", side_effect=self.synthetic_worker):
            receipt = self.run_discovery()
            receipt["jobs"][0]["status"] = "almost-done"
            discovery._write_atomic_receipt(self.out / "receipt.json", receipt)
            with self.assertRaisesRegex(ValueError, "malformed job state"):
                self.run_discovery()
        receipt_path = self.out / "receipt.json"
        receipt_path.unlink()
        orphan = self.out / ("f" * 20)
        orphan.mkdir(mode=0o700)
        (orphan / "worker").mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError, "outside the current inventory"):
            self.run_discovery()

    def test_output_rejects_repository_and_symlink_job_directory(self) -> None:
        # The repository can be a read-only bind mount. A temporary stand-in
        # keeps the same valid owner/mode preconditions without writing there.
        with patch.object(discovery, "REPOSITORY_ROOT", self.root):
            with self.assertRaisesRegex(ValueError, "outside the repository"):
                discovery.private_directory(self.out)
        key = hashlib.sha256(b"1:2:Rice").hexdigest()[:20]
        target = self.root / "fake-job"
        target.mkdir(mode=0o700)
        (self.out / key).symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.run_discovery()

    def test_output_reservation_failure_is_receipted_before_worker(self) -> None:
        with patch.object(discovery, "run_bounded", side_effect=self.synthetic_worker) as worker:
            with self.assertRaisesRegex(ValueError, "reserved output budget"):
                self.run_discovery(max_output_bytes=discovery.MAX_PAGE_BYTES + 1)
        worker.assert_not_called()
        receipt = discovery.strict_json((self.out / "receipt.json").read_bytes())
        self.assertEqual(receipt["jobs"][0]["status"], "failed")
        self.assertIn("runtime_seconds", receipt["jobs"][0])

    def test_same_title_in_different_crops_is_separate(self) -> None:
        first_source = self.run_dir / "rice-short.json"
        second_source = self.run_dir / "wheat-short.json"
        second_text = "Synthetic"
        first = {"page": {"title": "Rice Short", "page_id": 8, "revision_id": 9,
                           "kind": "variety", "crop": "Rice", "wikitext": second_text,
                           "content_hash": hashlib.sha256(second_text.encode()).hexdigest()}}
        second = {"page": {"title": "Wheat Short", "page_id": 9, "revision_id": 10,
                            "kind": "variety", "crop": "Wheat", "wikitext": second_text,
                            "content_hash": hashlib.sha256(second_text.encode()).hexdigest()}}
        first_source.write_text(json.dumps(first), encoding="utf-8")
        os.chmod(first_source, 0o600)
        second_source.write_text(json.dumps(second), encoding="utf-8")
        os.chmod(second_source, 0o600)
        self.write_inventory([
            self.entry(page_id=8, revision_id=9, title="Rice Short", crop="Rice", kind="variety", source=first_source),
            self.entry(page_id=9, revision_id=10, title="Wheat Short", crop="Wheat", kind="variety", source=second_source),
        ])
        with patch.object(discovery, "run_bounded", side_effect=self.synthetic_worker) as worker:
            result = self.run_discovery()
        self.assertEqual(worker.call_count, 2)
        self.assertEqual({row["crop"] for row in result["jobs"]}, {"Rice", "Wheat"})

    def test_transferred_private_bundle_keeps_run_relative_sources_consumable(self) -> None:
        bundle = self.root / "transferred-bundle"
        bundle.mkdir(mode=0o700)
        shutil.copy2(self.source, bundle / "page.json")
        shutil.copy2(self.inventory, bundle / "inventory.json")
        os.chmod(bundle, 0o700)
        transferred_output = self.root / "transferred-output"
        transferred_output.mkdir(mode=0o700)
        with patch.object(discovery, "run_bounded", side_effect=self.synthetic_worker):
            receipt = discovery.run(bundle / "inventory.json", transferred_output, "Named reviewer",
                                    exclusion_policy=self.policy)
        artifact_dir = transferred_output / receipt["jobs"][0]["job_dir"] / "worker"
        self.assertEqual({p.name for p in artifact_dir.iterdir()}, discovery.EXPECTED_ARTIFACTS)
        self.assertEqual(discovery.strict_json((artifact_dir / "raw_data.json").read_bytes())["pages"][0]["page_id"], 1)

    @unittest.skipUnless(sys.platform == "linux", "requires Linux inherited lock descriptor and cgroup enforcement")
    def test_cli_lock_is_inherited_by_live_worker_after_parent_is_killed(self) -> None:
        """A killed CLI owner cannot release the receipt lock while its worker lives."""
        try:
            discovery.require_linux_memory_budget(1024 * 1024 * 1024)
        except ValueError as error:
            self.skipTest(str(error))
        ready, release, pid_path = (self.root / name for name in ("worker-ready", "worker-release", "worker.pid"))
        # Become the worker's reaper before its direct parent is killed. This
        # keeps the known child PID owned until waitpid, preventing PID reuse.
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        previous_subreaper = ctypes.c_int()
        self.assertEqual(libc.prctl(37, ctypes.byref(previous_subreaper), 0, 0, 0), 0)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)  # PR_SET_CHILD_SUBREAPER
        worker_code = (
            "import os,time,pathlib\n"
            f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
            f"pathlib.Path({str(ready)!r}).write_text('ready')\n"
            f"while not pathlib.Path({str(release)!r}).exists(): time.sleep(.01)\n"
        )
        adapter = f'''\
import os, sys
from pathlib import Path
sys.path.insert(0, {str(TOOLS)!r})
import bounded_discovery as d
def dispatch(args, output, lock_fd):
    d.run_bounded([sys.executable, '-c', {worker_code!r}], timeout=30, max_output_bytes=1024,
                  memory_bytes=1024*1024*1024, lock_fd=lock_fd)
    return 0
d._dispatch = dispatch
sys.argv = ['bounded_discovery.py', '--operation', 'summary', '--output-dir', {str(self.out)!r}]
raise SystemExit(d.main())
'''
        env = {**os.environ, "DOCUMENT_ROOT": str(self.web)}
        owner = subprocess.Popen([sys.executable, "-c", adapter], env=env, start_new_session=True)
        worker_pid = None
        worker_reaped = False
        try:
            deadline = time.monotonic() + 15
            while not ready.exists() and owner.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "worker did not acquire inherited lock")
            worker_pid = int(pid_path.read_text())
            owner.kill()
            owner.wait(timeout=5)
            for operation in ("summary", "batch"):
                contender = subprocess.run(
                    [sys.executable, str(TOOLS / "bounded_discovery.py"), "--operation", operation,
                     "--output-dir", str(self.out)], env=env, text=True, capture_output=True, timeout=10)
                self.assertEqual(contender.returncode, 2, contender.stderr)
                self.assertIn('"error": "run_in_progress"', contender.stderr)
            release.write_text("release")
            waited, _ = os.waitpid(worker_pid, 0)
            self.assertEqual(waited, worker_pid)
            worker_reaped = True
            contender = subprocess.run(
                [sys.executable, str(TOOLS / "bounded_discovery.py"), "--operation", "summary",
                 "--output-dir", str(self.out)], env=env, text=True, capture_output=True, timeout=10)
            self.assertNotIn('"error": "run_in_progress"', contender.stderr)
        finally:
            release.write_text("release")
            if owner.poll() is None:
                owner.kill()
                owner.wait(timeout=5)
            if worker_pid is not None and not worker_reaped:
                os.kill(worker_pid, signal.SIGKILL)
                try:
                    os.waitpid(worker_pid, 0)
                except ChildProcessError:
                    pass
            self.assertEqual(libc.prctl(36, previous_subreaper.value, 0, 0, 0), 0)

    def test_cli_refuses_unsupported_receipt_storage_before_dispatch_or_lock_file(self):
        import bounded_process
        import io
        from contextlib import redirect_stderr

        stderr = io.StringIO()
        with patch.object(sys, "argv", ["bounded_discovery.py", "--operation", "batch",
                                       "--output-dir", str(self.out)]), \
             patch.object(bounded_process, "require_inherited_lock_filesystem",
                          side_effect=ValueError("receipt_filesystem_unsupported")), \
             patch.object(discovery, "_dispatch") as dispatch, redirect_stderr(stderr):
            self.assertEqual(discovery.main(), 2)
        dispatch.assert_not_called()
        self.assertEqual(json.loads(stderr.getvalue())["error"], "receipt_filesystem_unsupported")
        self.assertFalse((self.out / ".receipt.lock").exists())


def stat_mode(path: Path) -> int:
    import stat

    return stat.S_IMODE(path.stat().st_mode)


if __name__ == "__main__":
    unittest.main()
