"""Portable synthetic lifecycle acceptance tests for the public WCF adapter.

This suite is a portability adaptation of the original WCF lifecycle test
suite.  It binds the EXACT supplied public ``web-content-fetcher`` adapter to
the EXACT supplied public ``artifact-lifecycle-core`` snapshot through an
explicit operator-supplied root contract.

There is no private default root and no system-temp fallback.  Missing or
incomplete configuration fails the suite with a clear error; it is never
turned into a silent whole-class pass.  All fixture data is synthetic and is
created only under the explicitly configured test-owned temp root.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from web_content_fetcher import artifact_lifecycle


CORE_ROOT_ENV = "WCF_ARTIFACT_LIFECYCLE_CORE_TEST_ROOT"
TEST_TMPDIR_ENV = "WCF_TEST_TMPDIR"
CORE_MODULE_RELATIVE = Path("artifact_lifecycle_core.py")
PROFILE_RELATIVE = Path("profiles") / "artifact-lifecycle-profiles.v1.json"


def _explicit_directory(env_name: str, purpose: str) -> Path:
    """Return an operator-supplied absolute directory or fail clearly.

    The portable suite deliberately has no private-host default and no system
    temp fallback, so an unset or unusable contract fails tests instead of
    silently passing them.
    """

    raw = os.environ.get(env_name, "").strip()
    if not raw:
        raise RuntimeError(
            f"{env_name} must name the explicit {purpose}; "
            "this portable suite has no private default root and no system-temp fallback"
        )
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"{env_name} must be an absolute operator-supplied path: {raw}")
    if not path.is_dir():
        raise RuntimeError(f"{env_name} is not a directory: {path}")
    return path.resolve()


def _require_core_root() -> Path:
    root = _explicit_directory(CORE_ROOT_ENV, "public artifact-lifecycle-core snapshot root")
    required = (root / CORE_MODULE_RELATIVE, root / PROFILE_RELATIVE)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(
            "public artifact-lifecycle-core snapshot is incomplete; missing: "
            + ", ".join(missing)
        )
    return root


class ArtifactLifecycleAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core_root = _require_core_root()
        cls.test_temp_root = _explicit_directory(
            TEST_TMPDIR_ENV, "test-owned temporary fixture root"
        )

    def setUp(self):
        self.base = self.test_temp_root / f"artifact-lifecycle-{uuid.uuid4().hex}"
        self.base.mkdir()
        # Register cleanup immediately after the owned fixture exists so a
        # later setUp failure still removes it.  No ignore-errors mode: a
        # genuine cleanup denial is reported instead of hidden.
        self.addCleanup(self._remove_owned_fixture)
        self.root = self.base / "evidence"
        self.root.mkdir()
        self.output = self.base / "lifecycle-output"
        self.quarantine = self.base / "quarantine"
        self.quarantine.mkdir()
        self.core, _ = artifact_lifecycle._load_core(self.core_root)
        # Synthetic disk state: scan/plan disk probes return a healthy report so
        # the fixture never depends on host free space.  The dedicated capture
        # tests below still exercise the real disk-gate arithmetic through a
        # patched shutil.disk_usage.
        self.disk_patch = mock.patch.object(
            self.core,
            "free_space_status",
            return_value={"status": "ok", "free_ratio": 0.5, "minimum_free_ratio": 0.15},
        )
        self.disk_patch.start()
        self.addCleanup(self.disk_patch.stop)
        # Synthetic Git state: the public Core helper shells out to `git` in
        # production.  This suite represents Git state with a mock, not real
        # Git integration, so a clean state is pinned by default and the dirty
        # subcase below overrides it explicitly.
        self.git_patch = mock.patch.object(self.core, "git_status", return_value="clean")
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)

    def _remove_owned_fixture(self):
        """Remove only this run's fresh, verified-owned fixture directory."""

        expected_parent = self.test_temp_root.resolve()
        target = self.base.resolve(strict=False)
        if (
            self.base.is_symlink()
            or target.parent != expected_parent
            or not target.name.startswith("artifact-lifecycle-")
            or target == expected_parent
        ):
            raise RuntimeError(f"refusing to remove unverified fixture path: {target}")
        # Deliberately no ignore-errors mode: a real cleanup denial must surface and
        # stop the test instead of being hidden.  Only this run's own child is
        # removed; inputs, public snapshots, receipts and parents are untouched.
        shutil.rmtree(target)

    def _write(self, relative: str, value: bytes | str):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(value, encoding="utf-8")
        return path

    def _scan(self, closure_run_id: str = "synthetic-run-1"):
        return artifact_lifecycle.scan(
            self.root,
            output_dir=self.output,
            closure_run_id=closure_run_id,
            core_root=self.core_root,
            max_items=100,
            max_depth=6,
        )

    def _read_manifest(self, result):
        return json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    def test_successful_referenced_archive_is_protected_and_only_known_ephemera_become_candidates(self):
        self._write("success/structured.json", json.dumps({"status": "ok", "evidence_archive": {"item_id": "archive-1"}}))
        self._write("success/raw_snapshot.html", "<article>evidence</article>")
        self._write("success/image_assets/original.png", b"image")
        self._write("failed_runs/broken.html", "failed page")
        self._write("cache/http-cache.bin", b"cache")
        self._write("conversion/one.html", "same converted content")
        self._write("conversion/two.html", "same converted content")
        self._write("unknown/keep.bin", b"unknown")

        result = self._scan()
        manifest = self._read_manifest(result)
        by_path = {item["path"]: item for item in manifest["artifacts"]}
        self.assertEqual(by_path["success/raw_snapshot.html"]["artifact_role"], "authoritative_input")
        self.assertTrue(by_path["success/raw_snapshot.html"]["pin"])
        self.assertEqual(by_path["success/raw_snapshot.html"]["referenced_by"], ["archive:archive-1"])
        self.assertEqual(by_path["cache/http-cache.bin"]["artifact_role"], "cache_build_test_temp")
        self.assertFalse(by_path["cache/http-cache.bin"]["pin"])
        self.assertEqual(by_path["conversion/two.html"]["artifact_role"], "duplicate_or_obsolete")
        self.assertEqual(by_path["unknown/keep.bin"]["artifact_role"], "unknown")
        self.assertTrue(by_path["unknown/keep.bin"]["pin"])

        plan_path = self.base / "plan.json"
        plan_result = artifact_lifecycle.plan_gc(
            result["manifest_path"],
            artifact_root=self.root,
            quarantine_root=self.quarantine,
            output_path=plan_path,
            owner_state="idle_confirmed",
            process_state="idle_confirmed",
            dispatch_state="none_active",
            core_root=self.core_root,
        )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        entries = {item["artifact_id"]: item for item in plan["entries"]}
        protected = entries[by_path["success/raw_snapshot.html"]["artifact_id"]]
        self.assertFalse(protected["eligible"])
        self.assertIn("authoritative_reference_present", protected["blocking_gates"])
        cache = entries[by_path["cache/http-cache.bin"]["artifact_id"]]
        self.assertTrue(cache["eligible"])
        self.assertEqual(plan_result["eligible"], 2)

    def test_plan_fails_closed_for_active_owner_dispatch_dirty_git_and_hardlink_drift(self):
        self._write("cache/item.bin", b"cache")
        os.link(self.root / "cache/item.bin", self.root / "cache/item-copy.bin")
        result = self._scan()
        manifest = self._read_manifest(result)
        cache_items = [item for item in manifest["artifacts"] if item["artifact_role"] == "cache_build_test_temp"]
        self.assertEqual(len(cache_items), 2)
        self.assertTrue(all(item["file_identity"]["hardlink_count"] == item["file_identity"]["observed_links"] for item in cache_items))

        plan_path = self.base / "active-plan.json"
        active = artifact_lifecycle.plan_gc(
            result["manifest_path"],
            artifact_root=self.root,
            quarantine_root=self.quarantine,
            output_path=plan_path,
            owner_state="active",
            process_state="active",
            dispatch_state="active",
            core_root=self.core_root,
        )
        self.assertEqual(active["eligible"], 0)
        active_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertIn("owner_not_idle_confirmed", active_plan["entries"][0]["blocking_gates"])
        self.assertIn("active_process_not_cleared", active_plan["entries"][0]["blocking_gates"])
        self.assertIn("dispatch_not_cleared", active_plan["entries"][0]["blocking_gates"])

        mutated = dict(manifest)
        mutated["artifacts"] = [dict(item) for item in manifest["artifacts"]]
        mutated["artifacts"][0]["file_identity"] = dict(mutated["artifacts"][0]["file_identity"])
        mutated["artifacts"][0]["file_identity"]["observed_links"] = 0
        mutated = self.core.attach_hash(mutated, "manifest_sha256")
        mutated_path = self.base / "hardlink-drift.json"
        self.core.atomic_write_json(mutated_path, mutated)
        hardlink_plan_path = self.base / "hardlink-plan.json"
        hardlink_plan = artifact_lifecycle.plan_gc(
            mutated_path,
            artifact_root=self.root,
            quarantine_root=self.quarantine,
            output_path=hardlink_plan_path,
            owner_state="idle_confirmed",
            process_state="idle_confirmed",
            dispatch_state="none_active",
            core_root=self.core_root,
        )
        self.assertEqual(hardlink_plan["eligible"], 1)
        entries = json.loads(hardlink_plan_path.read_text(encoding="utf-8"))["entries"]
        self.assertTrue(any("hardlink_coverage_incomplete" in item["blocking_gates"] for item in entries))

        # Synthetic dirty-Git override; no real Git subprocess is launched.
        with mock.patch.object(self.core, "git_status", return_value="dirty"):
            dirty_path = self.base / "dirty-plan.json"
            dirty = artifact_lifecycle.plan_gc(
                result["manifest_path"],
                artifact_root=self.root,
                quarantine_root=self.quarantine,
                output_path=dirty_path,
                owner_state="idle_confirmed",
                process_state="idle_confirmed",
                dispatch_state="none_active",
                core_root=self.core_root,
            )
        self.assertEqual(dirty["eligible"], 0)
        self.assertTrue(all("git_dirty" in item["blocking_gates"] for item in json.loads(dirty_path.read_text(encoding="utf-8"))["entries"]))

    def test_verify_detects_candidate_drift_and_cross_volume_is_blocked(self):
        cache = self._write("cache/item.bin", b"initial")
        result = self._scan()
        plan_path = self.base / "plan.json"
        artifact_lifecycle.plan_gc(
            result["manifest_path"],
            artifact_root=self.root,
            quarantine_root=self.quarantine,
            output_path=plan_path,
            owner_state="idle_confirmed",
            process_state="idle_confirmed",
            dispatch_state="none_active",
            core_root=self.core_root,
        )
        self.assertTrue(
            artifact_lifecycle.verify(
                plan_path,
                result["manifest_path"],
                artifact_root=self.root,
                quarantine_root=self.quarantine,
                core_root=self.core_root,
            )["ok"]
        )
        cache.write_bytes(b"changed")
        verified = artifact_lifecycle.verify(
            plan_path,
            result["manifest_path"],
            artifact_root=self.root,
            quarantine_root=self.quarantine,
            core_root=self.core_root,
        )
        self.assertFalse(verified["ok"])
        self.assertTrue(any(value.startswith("artifact_digest_drift") for value in verified["errors"]))

        # Synthetic cross-volume device identities; no second volume is mounted.
        with mock.patch.object(self.core, "volume_identity", side_effect=[1, 2]):
            with self.assertRaisesRegex(artifact_lifecycle.LifecycleAdapterError, "CROSS_VOLUME_BLOCKED"):
                artifact_lifecycle.plan_gc(
                    result["manifest_path"],
                    artifact_root=self.root,
                    quarantine_root=self.quarantine,
                    output_path=self.base / "cross-volume.json",
                    owner_state="idle_confirmed",
                    process_state="idle_confirmed",
                    dispatch_state="none_active",
                    core_root=self.core_root,
                )

    def test_reparse_low_disk_and_unknown_paths_fail_closed_or_protect(self):
        self._write("unknown/keep.bin", b"keep")
        with mock.patch.object(self.core, "free_space_status", return_value={"status": "blocked_low_disk", "free_ratio": 0.01}):
            with self.assertRaisesRegex(artifact_lifecycle.LifecycleAdapterError, "blocked_low_disk"):
                self._scan()

        original = self.core.assert_no_reparse

        def reject_target(path, root):
            if Path(path).name == "blocked.bin":
                raise self.core.LifecycleError("REPARSE_POINT_BLOCKED", str(path))
            return original(path, root)

        self._write("unknown/blocked.bin", b"blocked")
        # Targeted synthesis of one reparse-point denial; every other path goes
        # through the real public Core helper unchanged.
        with mock.patch.object(self.core, "assert_no_reparse", side_effect=reject_target):
            with self.assertRaisesRegex(artifact_lifecycle.LifecycleAdapterError, "reparse"):
                self._scan("synthetic-run-reparse")

    def test_large_capture_disk_gate_blocks_before_browser_or_image_work(self):
        low_disk = shutil._ntuple_diskusage(total=1000, used=900, free=100)
        with mock.patch.object(artifact_lifecycle.shutil, "disk_usage", return_value=low_disk):
            with self.assertRaisesRegex(
                artifact_lifecycle.LifecycleAdapterError,
                "blocked_insufficient_estimated_space",
            ):
                artifact_lifecycle.capture_disk_preflight(self.root, operation="browser_or_image_capture")

        from web_content_fetcher import cli

        args = cli.build_parser().parse_args(
            ["fetch", "https://example.test", "--browser", "--archive-dir", str(self.root)]
        )
        expected = {"status": "ready", "operation": "browser_or_image_capture"}
        with mock.patch.object(cli, "capture_disk_preflight", return_value=expected) as preflight:
            self.assertEqual(cli._large_artifact_disk_gate(args), expected)
        preflight.assert_called_once_with(
            str(self.root), operation="browser_or_image_capture", estimated_output_bytes=None
        )

    def test_small_known_capture_warns_but_runs_with_two_gib_free(self):
        two_gib = 2 * 1024**3
        usage = shutil._ntuple_diskusage(total=100 * 1024**3, used=98 * 1024**3, free=two_gib)
        with mock.patch.object(artifact_lifecycle.shutil, "disk_usage", return_value=usage):
            result = artifact_lifecycle.capture_disk_preflight(
                self.root, operation="browser_or_image_capture"
            )
        self.assertEqual("low_disk_allowed", result["status"])
        self.assertTrue(result["warning_only"])
        self.assertEqual(512 * 1024**2, result["minimum_free_bytes"])
        self.assertEqual(512 * 1024**2, result["estimated_output_bytes"])
        self.assertNotIn("minimum_free_ratio", result)

    def test_capture_policy_matches_shared_profile(self):
        profile, _ = artifact_lifecycle._load_profile(self.core, Path(self.core_root))
        self.assertEqual(profile["minimum_free_bytes"], artifact_lifecycle.MINIMUM_FREE_BYTES)
        self.assertEqual(profile["warning_free_bytes"], artifact_lifecycle.WARNING_FREE_BYTES)
        self.assertEqual(
            profile["output_headroom_multiplier"],
            artifact_lifecycle.OUTPUT_HEADROOM_MULTIPLIER,
        )
        self.assertNotIn("minimum_free_ratio", profile)

    def test_batch_capture_requires_estimate_and_blocks_when_it_will_not_fit(self):
        with self.assertRaisesRegex(
            artifact_lifecycle.LifecycleAdapterError, "blocked_output_estimate_required"
        ):
            artifact_lifecycle.capture_disk_preflight(
                self.root, operation="batch_browser_archive"
            )

        free_bytes = int(2.5 * 1024**3)
        usage = shutil._ntuple_diskusage(
            total=100 * 1024**3,
            used=100 * 1024**3 - free_bytes,
            free=free_bytes,
        )
        with mock.patch.object(artifact_lifecycle.shutil, "disk_usage", return_value=usage):
            with self.assertRaisesRegex(
                artifact_lifecycle.LifecycleAdapterError,
                "blocked_insufficient_estimated_space",
            ):
                artifact_lifecycle.capture_disk_preflight(
                    self.root,
                    operation="batch_browser_archive",
                    estimated_output_bytes=2 * 1024**3,
                )

    def test_synthetic_restore_requires_marker_and_production_cli_has_no_quarantine_or_purge(self):
        marker = self._write(
            artifact_lifecycle.SYNTHETIC_MARKER,
            json.dumps({"schema_version": "wcf-artifact-lifecycle-synthetic-root.v1", "synthetic": True}),
        )
        cache = self._write("cache/item.bin", b"cache")
        result = self._scan()
        manifest = self._read_manifest(result)
        plan_path = self.base / "plan.json"
        artifact_lifecycle.plan_gc(
            result["manifest_path"],
            artifact_root=self.root,
            quarantine_root=self.quarantine,
            output_path=plan_path,
            owner_state="idle_confirmed",
            process_state="idle_confirmed",
            dispatch_state="none_active",
            core_root=self.core_root,
        )
        plan = self.core.read_json(plan_path)
        approval = self.core.attach_hash(
            {
                "schema_version": "artifact-lifecycle-approval.v1",
                "approved": True,
                "operation": "quarantine",
                "plan_sha256": plan["plan_sha256"],
                "approved_by": "synthetic-test",
                "approved_at": self.core.utc_now(),
                "expires_at": "2099-01-01T00:00:00Z",
            },
            "approval_sha256",
        )
        receipt_path = self.base / "synthetic-quarantine-receipt.json"
        self.core.apply_quarantine(plan, manifest, self.root, self.quarantine, approval, receipt_path)
        self.assertFalse(cache.exists())

        def assert_no_restore_mutation(output_path):
            """A rejected restore must not move the quarantined file or write output."""

            self.assertFalse(cache.exists())
            quarantined = list(self.quarantine.rglob("item.bin"))
            self.assertEqual(len(quarantined), 1)
            self.assertTrue(quarantined[0].is_file())
            self.assertFalse(output_path.exists())

        # execute=False must reject before any restore action or file mutation.
        rejected_flag = self.base / "restore-rejected-execute-flag.json"
        with self.assertRaisesRegex(
            artifact_lifecycle.LifecycleAdapterError, "synthetic_restore_execute_flag_required"
        ):
            artifact_lifecycle.restore_synthetic(
                plan_path,
                receipt_path,
                artifact_root=self.root,
                quarantine_root=self.quarantine,
                output_path=rejected_flag,
                execute=False,
                core_root=self.core_root,
            )
        assert_no_restore_mutation(rejected_flag)

        # A missing synthetic marker must reject and leave the quarantined file.
        marker.unlink()
        rejected_missing_marker = self.base / "restore-rejected-missing-marker.json"
        with self.assertRaisesRegex(
            artifact_lifecycle.LifecycleAdapterError, "synthetic_restore_marker_required"
        ):
            artifact_lifecycle.restore_synthetic(
                plan_path,
                receipt_path,
                artifact_root=self.root,
                quarantine_root=self.quarantine,
                output_path=rejected_missing_marker,
                execute=True,
                core_root=self.core_root,
            )
        assert_no_restore_mutation(rejected_missing_marker)

        # An invalid synthetic marker must reject and leave the quarantined file.
        marker.write_text(
            json.dumps({"schema_version": "not-a-synthetic-root", "synthetic": False}),
            encoding="utf-8",
        )
        rejected_invalid_marker = self.base / "restore-rejected-invalid-marker.json"
        with self.assertRaisesRegex(
            artifact_lifecycle.LifecycleAdapterError, "synthetic_restore_marker_invalid"
        ):
            artifact_lifecycle.restore_synthetic(
                plan_path,
                receipt_path,
                artifact_root=self.root,
                quarantine_root=self.quarantine,
                output_path=rejected_invalid_marker,
                execute=True,
                core_root=self.core_root,
            )
        assert_no_restore_mutation(rejected_invalid_marker)

        # Restore the valid marker and retain the positive marked round-trip.
        marker.write_text(
            json.dumps({"schema_version": "wcf-artifact-lifecycle-synthetic-root.v1", "synthetic": True}),
            encoding="utf-8",
        )
        restored = artifact_lifecycle.restore_synthetic(
            plan_path,
            receipt_path,
            artifact_root=self.root,
            quarantine_root=self.quarantine,
            output_path=self.base / "restore-receipt.json",
            execute=True,
            core_root=self.core_root,
        )
        self.assertEqual(restored["status"], "completed")
        self.assertTrue(cache.is_file())
        self.assertEqual(list(self.quarantine.rglob("item.bin")), [])

        from web_content_fetcher import cli

        parser = cli.build_parser()
        archive_action = next(action for action in parser._actions if getattr(action, "dest", "") == "command")
        archive_parser = archive_action.choices["archive"]
        archive_sub = next(action for action in archive_parser._actions if getattr(action, "dest", "") == "archive_command")
        lifecycle_parser = archive_sub.choices["lifecycle"]
        lifecycle_sub = next(action for action in lifecycle_parser._actions if getattr(action, "dest", "") == "lifecycle_command")
        self.assertNotIn("quarantine", lifecycle_sub.choices)
        self.assertNotIn("purge", lifecycle_sub.choices)

    def test_cli_scan_returns_machine_readable_manifest(self):
        self._write("cache/item.bin", b"cache")
        from web_content_fetcher import cli

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.main(
                [
                    "archive",
                    "lifecycle",
                    "scan",
                    str(self.root),
                    "--output-dir",
                    str(self.output),
                    "--closure-run-id",
                    "cli-synthetic-run",
                    "--core-root",
                    str(self.core_root),
                ]
            )
        data = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(data["lifecycle_profile"], "research_evidence")
        self.assertTrue(Path(data["manifest_path"]).is_file())
        manifest = json.loads(Path(data["manifest_path"]).read_text(encoding="utf-8"))
        indexed = {item["path"]: item for item in manifest["artifacts"]}
        self.assertIn("cache/item.bin", indexed)
        self.assertEqual(indexed["cache/item.bin"]["artifact_role"], "cache_build_test_temp")


if __name__ == "__main__":
    unittest.main()
