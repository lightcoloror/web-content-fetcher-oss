from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "weixin-native-document.v1"
CAPTURE_SCHEMA_VERSION = "weixin-native-capture.v1"
TEXT_NODE_TYPES = {"paragraph", "heading", "bullet"}
MARKER_NODE_TYPES = {"code_marker", "table_marker", "separator"}
ALLOWED_NODE_TYPES = TEXT_NODE_TYPES | MARKER_NODE_TYPES | {"image"}


def read_json_object(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected_json_object:{source}")
    return value


def write_json_object(path: str | Path, value: dict[str, Any]) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(target)


def merge_accessibility_segments(
    segments: Iterable[dict[str, Any]],
    *,
    minimum_overlap: int = 8,
) -> tuple[str, dict[str, Any]]:
    """Merge virtual-scroll text windows without duplicating their overlap."""
    ordered = sorted(
        (dict(item) for item in segments),
        key=lambda item: (
            int(item.get("anchor_index") or item.get("sequence") or 0),
            int(item.get("sequence") or 0),
        ),
    )
    merged = ""
    overlaps: list[int] = []
    truncated_segments: list[int] = []
    anchors: list[int] = []
    for index, segment in enumerate(ordered):
        text = str(segment.get("text") or "").replace("\r\n", "\n").replace("\r", "\n")
        anchor = int(segment.get("anchor_index") or segment.get("sequence") or index)
        anchors.append(anchor)
        if segment.get("selection_truncated") is True:
            truncated_segments.append(anchor)
        if not text:
            overlaps.append(0)
            continue
        overlap = _largest_suffix_prefix_overlap(merged, text, minimum_overlap)
        overlaps.append(overlap)
        if not merged:
            merged = text
        elif overlap:
            merged += text[overlap:]
        elif merged.endswith("\n") or text.startswith("\n"):
            merged += text
        else:
            merged += "\n" + text
    return merged, {
        "segment_count": len(ordered),
        "anchor_indexes": anchors,
        "overlap_chars": overlaps,
        "selection_truncation_detected": bool(truncated_segments),
        "truncated_anchor_indexes": truncated_segments,
        "merged_text_chars": len(merged),
        "merge_policy": "ordered_anchor_windows_with_longest_exact_suffix_prefix_overlap",
    }


def _largest_suffix_prefix_overlap(left: str, right: str, minimum: int) -> int:
    upper = min(len(left), len(right))
    for size in range(upper, max(0, minimum - 1), -1):
        if left[-size:] == right[:size]:
            return size
    return 0


def build_native_manifest(capture: dict[str, Any]) -> dict[str, Any]:
    """Build the neutral node/resource contract from an offline capture payload."""
    schema = str(capture.get("schema_version") or "")
    if schema != CAPTURE_SCHEMA_VERSION:
        raise ValueError(f"unsupported_capture_schema:{schema}")
    segments = capture.get("segments")
    resources_input = capture.get("resources")
    if not isinstance(segments, list) or not isinstance(resources_input, list):
        raise ValueError("capture_requires_segments_and_resources")

    _, segment_report = merge_accessibility_segments(segments)
    nodes = _merge_segment_nodes(segments)
    resources = _deduplicate_resources(resources_input)
    nodes, placement_degradations = _place_resources(nodes, resources)
    for index, node in enumerate(nodes, start=1):
        node.setdefault("node_id", f"node-{index:06d}")
    degradations = [
        dict(item)
        for item in capture.get("degradations", [])
        if isinstance(item, dict)
    ]
    degradations.extend(placement_degradations)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source": dict(capture.get("source") or {}),
        "capture": {
            "mode": "native_accessibility_dom",
            "segment_merge": segment_report,
            "live_capture_verified": bool(capture.get("live_capture_verified", False)),
        },
        "nodes": nodes,
        "resources": resources,
        "degradations": degradations,
    }
    manifest["integrity"] = validate_native_manifest(
        manifest,
        check_local_resources=False,
    )
    return manifest


def _merge_segment_nodes(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for segment_index, segment in enumerate(
        sorted(
            segments,
            key=lambda item: (
                int(item.get("anchor_index") or item.get("sequence") or 0),
                int(item.get("sequence") or 0),
            ),
        )
    ):
        segment_nodes = segment.get("nodes") or []
        if not isinstance(segment_nodes, list):
            raise ValueError(f"segment_nodes_must_be_array:{segment_index}")
        for node_index, raw in enumerate(segment_nodes):
            if not isinstance(raw, dict):
                continue
            node = dict(raw)
            node_type = str(node.get("type") or "")
            if node_type not in ALLOWED_NODE_TYPES:
                raise ValueError(f"unsupported_native_node_type:{node_type}")
            key = _node_dedup_key(node, segment_index, node_index)
            if key in seen:
                continue
            seen.add(key)
            nodes.append(node)
    return nodes


def _node_dedup_key(node: dict[str, Any], segment_index: int, node_index: int) -> str:
    node_id = str(node.get("node_id") or node.get("id") or "").strip()
    if node_id:
        return f"id:{node_id}"
    if node.get("source_order") is not None:
        payload = {
            "source_order": node.get("source_order"),
            "type": node.get("type"),
            "text": node.get("text"),
            "level": node.get("level"),
        }
        return "ordered:" + hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
    return f"local:{segment_index}:{node_index}"


def _deduplicate_resources(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(resources, start=1):
        if not isinstance(raw, dict):
            continue
        resource = dict(raw)
        resource_id = str(resource.get("id") or f"image-{index:04d}")
        resource["id"] = resource_id
        key = str(
            resource.get("sha256")
            or resource.get("source_fingerprint")
            or resource_id
        )
        if key in seen:
            continue
        seen.add(key)
        resource["sequence"] = len(output) + 1
        output.append(resource)
    return output


def _place_resources(
    nodes: list[dict[str, Any]],
    resources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    represented = {
        str(node.get("id") or "")
        for node in nodes
        if node.get("type") == "image"
    }
    additions: list[dict[str, Any]] = []
    degradations: list[dict[str, Any]] = []
    for resource in resources:
        resource_id = str(resource["id"])
        if resource_id in represented:
            continue
        if resource.get("source_order") is None:
            degradations.append(
                {
                    "scope": f"resource:{resource_id}",
                    "reason": "native_source_order_missing",
                    "fallback": "resource_not_inserted_pending_local_review",
                }
            )
            continue
        additions.append(
            {
                "type": "image",
                "id": resource_id,
                "sequence": resource["sequence"],
                "source_order": resource["source_order"],
                "local_src": resource.get("local_src"),
            }
        )
    combined = list(nodes) + additions
    if combined and all(item.get("source_order") is not None for item in combined):
        combined.sort(key=lambda item: float(item["source_order"]))
    return combined, degradations


def validate_native_manifest(
    manifest: dict[str, Any],
    *,
    artifact_root: str | Path | None = None,
    expected_text_chars: int | None = None,
    expected_images: int | None = None,
    check_local_resources: bool = True,
) -> dict[str, Any]:
    nodes = manifest.get("nodes")
    resources = manifest.get("resources")
    if not isinstance(nodes, list) or not isinstance(resources, list):
        raise ValueError("manifest_requires_nodes_and_resources")

    text_chars = sum(
        len(str(node.get("text") or ""))
        for node in nodes
        if isinstance(node, dict) and node.get("type") in TEXT_NODE_TYPES
    )
    resource_ids = [str(item.get("id") or "") for item in resources if isinstance(item, dict)]
    image_nodes = [
        node for node in nodes if isinstance(node, dict) and node.get("type") == "image"
    ]
    image_ids = [str(node.get("id") or "") for node in image_nodes]
    sequence = [int(node.get("sequence") or 0) for node in image_nodes]
    content_keys = [
        str(item.get("sha256") or item.get("source_fingerprint") or item.get("id") or "")
        for item in resources
        if isinstance(item, dict)
    ]
    local_refs = [
        str(item.get("local_src") or "")
        for item in resources
        if isinstance(item, dict) and str(item.get("local_src") or "")
    ]
    missing: list[str] = []
    unsafe: list[str] = []
    if check_local_resources:
        if artifact_root is None:
            raise ValueError("artifact_root_required_for_local_resource_check")
        root = Path(artifact_root).resolve()
        for relative in local_refs:
            try:
                candidate = _resolve_local_resource(root, relative)
            except ValueError:
                unsafe.append(relative)
                continue
            if not candidate.is_file():
                missing.append(relative)

    categories = [
        "image" if node.get("type") == "image" else "text"
        for node in nodes
        if isinstance(node, dict)
        and (node.get("type") == "image" or node.get("type") in TEXT_NODE_TYPES)
    ]
    transitions = sum(left != right for left, right in zip(categories, categories[1:]))
    first_image = next((i for i, value in enumerate(categories) if value == "image"), None)
    last_text = next(
        (i for i, value in reversed(list(enumerate(categories))) if value == "text"),
        None,
    )
    no_gallery_nodes = not any(
        isinstance(node, dict) and node.get("type") == "gallery" for node in nodes
    )
    checks = {
        "schema_valid": manifest.get("schema_version") == SCHEMA_VERSION,
        "direct_text_chars_match": (
            expected_text_chars is None or text_chars == expected_text_chars
        ),
        "image_count_match": (
            expected_images is None
            or (len(resources) == expected_images and len(image_nodes) == expected_images)
        ),
        "resource_ids_unique": len(set(resource_ids)) == len(resource_ids),
        "resource_content_unique": len(set(content_keys)) == len(content_keys),
        "image_resource_refs_complete": image_ids == resource_ids,
        "local_reference_count_match": len(local_refs) == len(resources),
        "local_reference_missing_zero": not missing and not unsafe,
        "source_order_monotonic": (
            sequence == sorted(sequence) and image_ids == resource_ids
        ),
        "gallery_forbidden": no_gallery_nodes,
        "mixed_order_not_body_plus_gallery": (
            not image_nodes
            or (
                first_image is not None
                and last_text is not None
                and first_image < last_text
                and transitions >= 2
            )
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if all(checks.values()) else "degraded",
        "direct_text_chars": text_chars,
        "unique_images": len(set(content_keys)),
        "image_nodes": len(image_nodes),
        "local_resource_references": len(local_refs),
        "local_missing_count": len(missing),
        "local_missing": missing,
        "unsafe_local_references": unsafe,
        "text_image_transitions": transitions,
        "checks": checks,
    }


def _resolve_local_resource(root: Path, relative: str) -> Path:
    rel = Path(relative.replace("\\", "/"))
    if rel.is_absolute():
        raise ValueError("absolute_local_resource_not_allowed")
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("local_resource_path_escape")
    return candidate


def materialize_native_manifest(
    manifest: dict[str, Any],
    *,
    artifact_root: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Copy the manifest and its original resources into a portable WCF archive."""
    source_root = Path(artifact_root).resolve()
    target_root = Path(output_dir).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    preflight = validate_native_manifest(manifest, artifact_root=source_root)
    if preflight["status"] != "ok":
        raise ValueError("native_manifest_preflight_failed")
    for resource in manifest["resources"]:
        relative = str(resource["local_src"])
        source = _resolve_local_resource(source_root, relative)
        target = _resolve_local_resource(target_root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    manifest_path = target_root / "native_document_manifest.json"
    write_json_object(manifest_path, manifest)
    report = validate_native_manifest(manifest, artifact_root=target_root)
    report_path = target_root / "native_manifest_report.json"
    write_json_object(report_path, report)
    return {
        "status": report["status"],
        "manifest_path": str(manifest_path),
        "report_path": str(report_path),
        "artifact_root": str(target_root),
        "integrity": report,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m web_content_fetcher.weixin_native_manifest",
        description="Build, inspect, or materialize a neutral Weixin/Tencent native document manifest.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="Validate an existing native manifest.")
    inspect.add_argument("manifest")
    inspect.add_argument("--artifact-root", required=True)
    inspect.add_argument("--expected-text-chars", type=int)
    inspect.add_argument("--expected-images", type=int)

    build = sub.add_parser("build", help="Build a manifest from an offline capture JSON.")
    build.add_argument("capture")
    build.add_argument("--output", required=True)
    build.add_argument("--artifact-root")
    build.add_argument("--expected-text-chars", type=int)
    build.add_argument("--expected-images", type=int)

    materialize = sub.add_parser(
        "materialize",
        help="Copy a manifest and original resources into a portable WCF archive.",
    )
    materialize.add_argument("manifest")
    materialize.add_argument("--artifact-root", required=True)
    materialize.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "inspect":
        manifest = read_json_object(args.manifest)
        report = validate_native_manifest(
            manifest,
            artifact_root=args.artifact_root,
            expected_text_chars=args.expected_text_chars,
            expected_images=args.expected_images,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "ok" else 2
    if args.command == "build":
        capture = read_json_object(args.capture)
        manifest = build_native_manifest(capture)
        write_json_object(args.output, manifest)
        report = validate_native_manifest(
            manifest,
            artifact_root=args.artifact_root,
            expected_text_chars=args.expected_text_chars,
            expected_images=args.expected_images,
            check_local_resources=bool(args.artifact_root),
        )
        write_json_object(str(args.output) + ".report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "ok" else 2
    manifest = read_json_object(args.manifest)
    result = materialize_native_manifest(
        manifest,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
