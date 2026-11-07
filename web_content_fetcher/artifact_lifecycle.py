"""Thin, plan-only lifecycle adapter for WCF evidence archives.

The generic state machine lives in artifact-lifecycle-core.  This module only
projects WCF archives into that contract and deliberately exposes no production
quarantine or purge command.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable


PROFILE_ID = "research_evidence"
PRODUCER_TOOL_ID = "web-content-fetcher"
DEFAULT_OWNER_LANE = "web-content-fetcher"
CORE_ROOT_ENV = "WEB_CONTENT_FETCHER_ARTIFACT_LIFECYCLE_CORE"
SYNTHETIC_MARKER = ".wcf-lifecycle-synthetic-root.json"
HINTS_FILE = ".wcf-artifact-lifecycle-hints.json"
CONTROL_FILES = {SYNTHETIC_MARKER, HINTS_FILE}
MINIMUM_FREE_BYTES = 512 * 1024**2
WARNING_FREE_BYTES = 5 * 1024**3
OUTPUT_HEADROOM_MULTIPLIER = 1.25
SMALL_KNOWN_OUTPUT_ESTIMATES = {
    "browser_or_image_capture": 512 * 1024**2,
    "document_browser_or_visual_capture": 1024 * 1024**2,
}
ARCHIVE_PROTECTED_NAMES = {
    "raw_snapshot.html",
    "extracted_text.md",
    "structured.json",
    "metadata.json",
    "native_document_manifest.json",
    "native_manifest_report.json",
    "integrity_receipt.json",
    "provenance.json",
    "request.json",
    "source_order.json",
}


class LifecycleAdapterError(RuntimeError):
    """A local adapter gate failed before any filesystem mutation."""


def capture_disk_preflight(
    target_path: str | Path,
    *,
    operation: str,
    estimated_output_bytes: int | None = None,
) -> dict[str, Any]:
    """Block durable high-volume capture before a browser or batch run starts.

    This is intentionally only a small admission check.  Lifecycle planning and
    every destructive-operation decision remain in artifact-lifecycle-core.
    """

    if estimated_output_bytes is None:
        estimated_output_bytes = SMALL_KNOWN_OUTPUT_ESTIMATES.get(operation)
    if estimated_output_bytes is None or estimated_output_bytes <= 0:
        raise LifecycleAdapterError(f"blocked_output_estimate_required:{operation}")

    requested = Path(target_path).expanduser()
    anchor = requested if requested.exists() else requested.parent
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent
    if not anchor.exists():
        raise LifecycleAdapterError(f"capture_disk_target_unavailable:{requested}")
    if anchor.is_file():
        anchor = anchor.parent
    try:
        usage = shutil.disk_usage(anchor)
    except OSError as exc:
        raise LifecycleAdapterError(f"capture_disk_check_failed:{anchor}:{exc}") from exc
    free_ratio = usage.free / usage.total if usage.total else 0.0
    output_reserve_bytes = (int(estimated_output_bytes) * 5 + 3) // 4
    required_free_bytes = MINIMUM_FREE_BYTES + output_reserve_bytes
    if usage.free < required_free_bytes:
        status = "blocked_insufficient_estimated_space"
    elif usage.free < WARNING_FREE_BYTES:
        status = "low_disk_allowed"
    else:
        status = "ready"
    result = {
        "status": status,
        "operation": operation,
        "target_path": str(requested),
        "checked_path": str(anchor),
        "free_bytes": int(usage.free),
        "total_bytes": int(usage.total),
        "free_ratio": free_ratio,
        "minimum_free_bytes": MINIMUM_FREE_BYTES,
        "warning_free_bytes": WARNING_FREE_BYTES,
        "estimated_output_bytes": int(estimated_output_bytes),
        "output_headroom_multiplier": OUTPUT_HEADROOM_MULTIPLIER,
        "output_reserve_bytes": output_reserve_bytes,
        "required_free_bytes": required_free_bytes,
        "free_after_estimated_output_bytes": int(usage.free) - output_reserve_bytes,
        "warning_only": status == "low_disk_allowed",
        "policy_mode": "absolute_reserve_plus_estimated_output",
        "filesystem_mutation_performed": False,
    }
    if status == "blocked_insufficient_estimated_space":
        raise LifecycleAdapterError(
            f"blocked_insufficient_estimated_space:{operation}:free={usage.free}:required={required_free_bytes}"
        )
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_core(core_root: str | Path = "") -> tuple[Any, Path]:
    configured = str(core_root or os.environ.get(CORE_ROOT_ENV, "")).strip()
    if not configured:
        raise LifecycleAdapterError(f"artifact_lifecycle_core_not_configured:{CORE_ROOT_ENV}")
    root = Path(configured).expanduser().resolve(strict=True)
    source = root / "artifact_lifecycle_core.py"
    if not source.is_file():
        raise LifecycleAdapterError(f"artifact_lifecycle_core_missing:{source}")
    module_name = "web_content_fetcher_external_artifact_lifecycle_core"
    cached = sys.modules.get(module_name)
    if cached is not None and Path(str(getattr(cached, "__file__", ""))).resolve() == source:
        return cached, root
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise LifecycleAdapterError(f"artifact_lifecycle_core_import_failed:{source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module, root


def _load_profile(core: Any, core_root: Path) -> tuple[dict[str, Any], str]:
    profiles_path = core_root / "profiles" / "artifact-lifecycle-profiles.v1.json"
    try:
        profiles = json.loads(profiles_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleAdapterError(f"artifact_lifecycle_profile_unreadable:{exc}") from exc
    errors = list(core.validate_profiles(profiles))
    if errors:
        raise LifecycleAdapterError("artifact_lifecycle_profile_invalid:" + ";".join(errors))
    profile = next((item for item in profiles.get("profiles", []) if item.get("profile_id") == PROFILE_ID), None)
    if not isinstance(profile, dict):
        raise LifecycleAdapterError(f"artifact_lifecycle_profile_missing:{PROFILE_ID}")
    if profile.get("cleanup_mode") != "plan_only" or profile.get("automatic_purge") is not False:
        raise LifecycleAdapterError("artifact_lifecycle_profile_not_plan_only")
    required_disk_fields = {"minimum_free_bytes", "warning_free_bytes", "output_headroom_multiplier"}
    if not required_disk_fields.issubset(profile):
        raise LifecycleAdapterError("artifact_lifecycle_profile_missing_output_aware_disk_policy")
    if "minimum_free_ratio" in profile:
        raise LifecycleAdapterError("artifact_lifecycle_profile_legacy_ratio_rejected")
    return profile, _sha256_file(profiles_path)


def contract_hashes(core_root: str | Path = "") -> dict[str, str]:
    _, root = _load_core(core_root)
    package_root = Path(__file__).resolve().parent
    paths = {
        "core": root / "artifact_lifecycle_core.py",
        "profile": root / "profiles" / "artifact-lifecycle-profiles.v1.json",
        "wcf_archive": package_root / "archive.py",
        "wcf_weixin_native_manifest": package_root / "weixin_native_manifest.py",
    }
    return {name: _sha256_file(path) for name, path in paths.items() if path.is_file()}


def doctor(*, core_root: str | Path = "", target_root: str | Path = "") -> dict[str, Any]:
    core, root = _load_core(core_root)
    profile, profile_hash = _load_profile(core, root)
    target = Path(target_root).expanduser().resolve(strict=True) if target_root else None
    core_result = core.doctor(root, target, PROFILE_ID)
    disk = core.free_space_status(target) if target is not None else None
    reason_codes = list(core_result.get("reason_codes") or [])
    if disk and disk.get("status") == "blocked_low_disk":
        reason_codes.append("blocked_low_disk")
    return {
        "status": "blocked" if reason_codes else "ready",
        "contract_ready": not reason_codes,
        "runtime_ready": not reason_codes,
        "fresh_e2e_pass": False,
        "real_usage_proof": False,
        "current_online": False,
        "lifecycle_profile": PROFILE_ID,
        "cleanup_mode": "plan_only",
        "automatic_purge": False,
        "production_operations": ["doctor", "scan", "explain", "plan_gc", "verify"],
        "restore_scope": "synthetic_marker_root_only",
        "disk": disk,
        "profile": profile,
        "profile_sha256": profile_hash,
        "contract_hashes": contract_hashes(root),
        "reason_codes": sorted(set(reason_codes)),
        "filesystem_mutation_performed": False,
    }


def _resolve_root(core: Any, value: str | Path) -> Path:
    try:
        root = core.resolve_root(value)
    except Exception as exc:
        raise LifecycleAdapterError(f"root_preflight_failed:{exc}") from exc
    return root


def _assert_outside(core: Any, path: Path, root: Path, label: str) -> None:
    if path == root or core.is_under(path, root):
        raise LifecycleAdapterError(f"{label}_must_be_outside_artifact_root")


def _walk_bounded(core: Any, root: Path, *, max_items: int, max_depth: int) -> Iterable[Path]:
    stack: list[tuple[Path, int]] = [(root, 0)]
    count = 0
    while stack:
        directory, depth = stack.pop()
        try:
            core.assert_no_reparse(directory, root)
            entries = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except Exception as exc:
            raise LifecycleAdapterError(f"path_access_or_reparse_blocked:{directory}:{exc}") from exc
        for entry in entries:
            try:
                core.assert_no_reparse(entry, root)
            except Exception as exc:
                raise LifecycleAdapterError(f"path_access_or_reparse_blocked:{entry}:{exc}") from exc
            if entry.is_dir():
                if depth >= max_depth:
                    raise LifecycleAdapterError(f"scan_depth_limit_exceeded:{entry}")
                stack.append((entry, depth + 1))
                continue
            if not entry.is_file():
                raise LifecycleAdapterError(f"unsupported_artifact_entry:{entry}")
            count += 1
            if count > max_items:
                raise LifecycleAdapterError(f"scan_item_limit_exceeded:{max_items}")
            yield entry


def _read_hints(root: Path) -> dict[str, dict[str, Any]]:
    path = root / HINTS_FILE
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleAdapterError(f"lifecycle_hints_invalid:{exc}") from exc
    files = value.get("files") if isinstance(value, dict) else None
    if not isinstance(files, dict):
        raise LifecycleAdapterError("lifecycle_hints_files_object_required")
    result: dict[str, dict[str, Any]] = {}
    for key, item in files.items():
        if not isinstance(key, str) or not isinstance(item, dict):
            raise LifecycleAdapterError("lifecycle_hints_entry_invalid")
        normalized = Path(key).as_posix()
        if normalized.startswith("../") or Path(normalized).is_absolute():
            raise LifecycleAdapterError(f"lifecycle_hints_path_escape:{key}")
        result[normalized] = dict(item)
    return result


def _archive_references(root: Path, files: list[Path]) -> dict[Path, str]:
    references: dict[Path, str] = {}
    for path in files:
        if path.name not in {"structured.json", "metadata.json"}:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        archive = data.get("evidence_archive") or data.get("archive") or {}
        item_id = str(archive.get("item_id") or path.parent.name)
        if data.get("status") in {"failed", "error"}:
            continue
        references[path.parent.resolve()] = f"archive:{item_id}"
    return references


def _parent_archive(path: Path, root: Path, archives: dict[Path, str]) -> str:
    current = path.parent.resolve()
    while current != root.parent:
        reference = archives.get(current)
        if reference:
            return reference
        if current == root:
            break
        current = current.parent
    return ""


def _contains_component(relative: Path, names: set[str]) -> bool:
    return any(part.casefold() in names for part in relative.parts[:-1])


def _is_conversion(relative: Path) -> bool:
    return _contains_component(relative, {"converted", "conversion", "derived", "rebuild", "render", "temporary"})


def _classification(relative: Path, *, referenced: bool, duplicate: bool, hint: dict[str, Any]) -> tuple[str, str, bool, list[str], dict[str, Any]]:
    if referenced:
        name = relative.name.casefold()
        role = "evidence_receipt_manifest" if name in {"structured.json", "metadata.json", "integrity_receipt.json", "provenance.json"} else "authoritative_input"
        if name.startswith("preferred_") or name in {"readable.html", "index.html"}:
            role = "final_deliverable"
        return role, "verified", True, ["successful_archive_reference"], {
            "status": "verified",
            "command": "wcf archive integrity preservation",
            "input_hashes": ["archive_reference"],
            "dependency_lock": "wcf-archive-contract",
            "verification_ref": "structured.json",
        }
    if relative.name in CONTROL_FILES:
        return "evidence_receipt_manifest", "verified", True, ["adapter_control_file"], {
            "status": "verified",
            "command": "wcf artifact lifecycle adapter",
            "input_hashes": ["adapter_control"],
            "dependency_lock": "artifact-lifecycle-core",
            "verification_ref": relative.name,
        }
    if duplicate:
        return "duplicate_or_obsolete", "superseded", False, [], {}
    if _contains_component(relative, {"cache", ".cache", "http-cache", "browser-cache"}):
        return "cache_build_test_temp", "expired", False, [], {}
    if _contains_component(relative, {"failed", "failed_runs", "failure"}):
        proof = dict(hint.get("rebuild_proof") or {})
        return "reproducible_derived", "failed", False, [], proof
    if _is_conversion(relative) and relative.suffix.casefold() in {".html", ".md", ".json"}:
        proof = dict(hint.get("rebuild_proof") or {})
        return "reproducible_derived", "superseded", False, [], proof
    return "unknown", "created", True, ["unknown_path_protected"], {}


def _file_identity(path: Path) -> tuple[tuple[int, int], dict[str, int]]:
    info = path.stat()
    key = (int(info.st_dev), int(info.st_ino))
    return key, {
        "volume_id": key[0],
        "file_id": key[1],
        "hardlink_count": int(info.st_nlink),
        "observed_links": 0,
    }


def scan(
    artifact_root: str | Path,
    *,
    output_dir: str | Path,
    closure_run_id: str,
    owner_lane: str = DEFAULT_OWNER_LANE,
    core_root: str | Path = "",
    max_items: int = 200,
    max_depth: int = 6,
) -> dict[str, Any]:
    if not closure_run_id.strip():
        raise LifecycleAdapterError("closure_run_id_required")
    if max_items < 1 or max_depth < 0:
        raise LifecycleAdapterError("scan_bounds_invalid")
    core, core_path = _load_core(core_root)
    _load_profile(core, core_path)
    root = _resolve_root(core, artifact_root)
    disk = core.free_space_status(root)
    if disk.get("status") == "blocked_low_disk":
        raise LifecycleAdapterError("blocked_low_disk")
    target = Path(output_dir).expanduser().resolve(strict=False)
    _assert_outside(core, target, root, "lifecycle_output")
    target.mkdir(parents=True, exist_ok=True)
    core.assert_no_reparse(target, target)

    files = list(_walk_bounded(core, root, max_items=max_items, max_depth=max_depth))
    hints = _read_hints(root)
    archive_refs = _archive_references(root, files)
    records: list[dict[str, Any]] = []
    digest_groups: dict[str, list[int]] = {}
    link_counts: dict[tuple[int, int], int] = {}
    for path in files:
        relative = path.relative_to(root)
        key, identity = _file_identity(path)
        link_counts[key] = link_counts.get(key, 0) + 1
        record = {
            "path": path,
            "relative": relative,
            "relative_text": relative.as_posix(),
            "identity_key": key,
            "identity": identity,
            "digest": _sha256_file(path),
            "bytes": path.stat().st_size,
            "reference": _parent_archive(path, root, archive_refs),
        }
        records.append(record)
        digest_groups.setdefault(record["digest"], []).append(len(records) - 1)

    duplicate_indexes: set[int] = set()
    for indexes in digest_groups.values():
        conversion_indexes = [index for index in indexes if _is_conversion(records[index]["relative"])]
        duplicate_indexes.update(conversion_indexes[1:])

    artifacts: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        identity = dict(record["identity"])
        identity["observed_links"] = link_counts[record["identity_key"]]
        relative_text = record["relative_text"]
        hint = hints.get(relative_text, {})
        role, state, pin, references, rebuild_proof = _classification(
            record["relative"],
            referenced=bool(record["reference"]),
            duplicate=index in duplicate_indexes,
            hint=hint,
        )
        if record["reference"]:
            references = [record["reference"]]
        if hint.get("referenced_by"):
            references = list(hint["referenced_by"])
            pin = True
        artifact_seed = f"{relative_text}:{record['digest']}"
        artifacts.append(
            {
                "artifact_id": "wcf-" + hashlib.sha256(artifact_seed.encode("utf-8")).hexdigest()[:20],
                "closure_run_id": closure_run_id,
                "producer_tool_id": PRODUCER_TOOL_ID,
                "owner_lane": owner_lane,
                "path": relative_text,
                "artifact_role": role,
                "digest": {"algorithm": "sha256", "value": record["digest"], "status": "verified"},
                "bytes": int(record["bytes"]),
                "lifecycle_state": state,
                "storage_state": "present",
                "depends_on": list(hint.get("depends_on") or []),
                "referenced_by": references,
                "sensitivity": str(hint.get("sensitivity") or ("potentially_private" if pin else "unknown")),
                "rebuild_proof": rebuild_proof,
                "minimum_keep_until": hint.get("minimum_keep_until"),
                "pin": bool(pin),
                "file_identity": identity,
            }
        )
    manifest_seed = {"root": str(root), "closure_run_id": closure_run_id, "artifacts": [item["artifact_id"] for item in artifacts]}
    manifest = {
        "schema_version": "artifact-lifecycle-manifest.v1",
        "manifest_id": "wcf-evidence-" + core.canonical_sha256(manifest_seed)[:20],
        "closure_run_id": closure_run_id,
        "producer_tool_id": PRODUCER_TOOL_ID,
        "owner_lane": owner_lane,
        "root_identity": core.directory_identity(root),
        "profile_id": PROFILE_ID,
        "cleanup_mode": "plan_only",
        "automatic_purge": False,
        "scan": {
            "checked_at": core.utc_now(),
            "max_items": max_items,
            "max_depth": max_depth,
            "errors": [],
            "adapter": "wcf_artifact_lifecycle.v1",
        },
        "artifacts": artifacts,
    }
    manifest = core.attach_hash(manifest, "manifest_sha256")
    errors = core.validate_manifest(manifest)
    if errors:
        raise LifecycleAdapterError("manifest_validation_failed:" + ";".join(errors))
    manifest_path = target / "wcf_research_evidence_manifest.json"
    checkpoint_path = target / "wcf_research_evidence_checkpoint.json"
    core.atomic_write_json(manifest_path, manifest)
    core.atomic_write_json(
        checkpoint_path,
        {
            "schema_version": "wcf-artifact-lifecycle-checkpoint.v1",
            "closure_run_id": closure_run_id,
            "manifest_sha256": manifest["manifest_sha256"],
            "root_identity": manifest["root_identity"],
            "profile_id": PROFILE_ID,
            "cleanup_mode": "plan_only",
            "filesystem_mutation_scope": "adapter_manifest_and_checkpoint_only",
        },
    )
    return {
        "status": "ok",
        "artifact_root": str(root),
        "manifest_path": str(manifest_path),
        "checkpoint_path": str(checkpoint_path),
        "artifact_count": len(artifacts),
        "protected_count": sum(bool(item["pin"]) for item in artifacts),
        "candidate_count": sum(not item["pin"] and item["lifecycle_state"] in {"failed", "superseded", "expired"} for item in artifacts),
        "manifest_sha256": manifest["manifest_sha256"],
        "lifecycle_profile": PROFILE_ID,
        "disk": disk,
        "contract_hashes": contract_hashes(core_path),
        "filesystem_mutation_scope": "adapter_manifest_and_checkpoint_only",
    }


def _load_manifest(core: Any, manifest_path: str | Path) -> dict[str, Any]:
    try:
        manifest = core.load_manifest(Path(manifest_path).expanduser().resolve(strict=True))
    except Exception as exc:
        raise LifecycleAdapterError(f"manifest_load_failed:{exc}") from exc
    if manifest.get("profile_id") != PROFILE_ID:
        raise LifecycleAdapterError("manifest_profile_mismatch")
    return manifest


def explain(manifest_path: str | Path, artifact_id: str, *, core_root: str | Path = "") -> dict[str, Any]:
    core, _ = _load_core(core_root)
    manifest = _load_manifest(core, manifest_path)
    item = next((value for value in manifest["artifacts"] if value.get("artifact_id") == artifact_id), None)
    if not item:
        raise LifecycleAdapterError("artifact_not_found")
    blockers: list[str] = []
    if item.get("pin"):
        blockers.append("artifact_pinned")
    if item.get("artifact_role") in core.PROTECTED_ROLES:
        blockers.append(f"protected_role:{item.get('artifact_role')}")
    if item.get("referenced_by"):
        blockers.append("authoritative_reference_present")
    if item.get("artifact_role") not in {"cache_build_test_temp", "duplicate_or_obsolete"} and not core._rebuild_complete(item):
        blockers.append("rebuild_proof_incomplete")
    return {
        "status": "ok",
        "artifact": item,
        "blocking_gates": sorted(set(blockers)),
        "candidate_kind": item.get("artifact_role"),
        "authorizes_operation": False,
    }


def plan_gc(
    manifest_path: str | Path,
    *,
    artifact_root: str | Path,
    quarantine_root: str | Path,
    output_path: str | Path,
    owner_state: str = "unknown",
    process_state: str = "unknown",
    dispatch_state: str = "unknown",
    core_root: str | Path = "",
) -> dict[str, Any]:
    core, core_path = _load_core(core_root)
    _load_profile(core, core_path)
    root = _resolve_root(core, artifact_root)
    quarantine = _resolve_root(core, quarantine_root)
    _assert_outside(core, quarantine, root, "quarantine_root")
    disk = core.free_space_status(root)
    if disk.get("status") == "blocked_low_disk":
        raise LifecycleAdapterError("blocked_low_disk")
    manifest = _load_manifest(core, manifest_path)
    if manifest.get("root_identity") != core.directory_identity(root):
        raise LifecycleAdapterError("manifest_root_identity_drift")
    try:
        plan = core.plan_operation(
            manifest,
            root,
            quarantine,
            "quarantine",
            owner_state=owner_state,
            process_state=process_state,
            dispatch_state=dispatch_state,
        )
    except Exception as exc:
        raise LifecycleAdapterError(f"plan_gc_blocked:{exc}") from exc
    target = Path(output_path).expanduser().resolve(strict=False)
    _assert_outside(core, target, root, "plan_output")
    target.parent.mkdir(parents=True, exist_ok=True)
    core.atomic_write_json(target, plan)
    return {
        "status": "ok",
        "plan_path": str(target),
        "plan_sha256": plan["plan_sha256"],
        "eligible": sum(bool(item["eligible"]) for item in plan["entries"]),
        "blocked": sum(not bool(item["eligible"]) for item in plan["entries"]),
        "authorization_state": "not_authorized",
        "cleanup_mode": "plan_only",
        "filesystem_mutation_scope": "plan_file_only",
        "disk": disk,
    }


def verify(
    plan_path: str | Path,
    manifest_path: str | Path,
    *,
    artifact_root: str | Path,
    quarantine_root: str | Path,
    core_root: str | Path = "",
) -> dict[str, Any]:
    core, _ = _load_core(core_root)
    root = _resolve_root(core, artifact_root)
    quarantine = _resolve_root(core, quarantine_root)
    manifest = _load_manifest(core, manifest_path)
    try:
        plan = core.read_json(Path(plan_path).expanduser().resolve(strict=True))
        return core.verify_plan(plan, manifest, root, quarantine)
    except Exception as exc:
        raise LifecycleAdapterError(f"plan_verify_failed:{exc}") from exc


def restore_synthetic(
    plan_path: str | Path,
    receipt_path: str | Path,
    *,
    artifact_root: str | Path,
    quarantine_root: str | Path,
    output_path: str | Path,
    execute: bool = False,
    core_root: str | Path = "",
) -> dict[str, Any]:
    if not execute:
        raise LifecycleAdapterError("synthetic_restore_execute_flag_required")
    core, _ = _load_core(core_root)
    root = _resolve_root(core, artifact_root)
    marker = root / SYNTHETIC_MARKER
    try:
        marker_value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleAdapterError(f"synthetic_restore_marker_required:{exc}") from exc
    if marker_value.get("synthetic") is not True or marker_value.get("schema_version") != "wcf-artifact-lifecycle-synthetic-root.v1":
        raise LifecycleAdapterError("synthetic_restore_marker_invalid")
    quarantine = _resolve_root(core, quarantine_root)
    _assert_outside(core, quarantine, root, "quarantine_root")
    try:
        plan = core.read_json(Path(plan_path).expanduser().resolve(strict=True))
        receipt = core.read_json(Path(receipt_path).expanduser().resolve(strict=True))
        target = Path(output_path).expanduser().resolve(strict=False)
        _assert_outside(core, target, root, "restore_output")
        target.parent.mkdir(parents=True, exist_ok=True)
        return core.restore_from_receipt(receipt, plan, root, quarantine, target)
    except Exception as exc:
        raise LifecycleAdapterError(f"synthetic_restore_blocked:{exc}") from exc
