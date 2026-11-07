from __future__ import annotations

import hashlib
import json
import os
import uuid
import zipfile
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .feishu_export import compare_feishu_captures, export_feishu_docx, is_feishu_doc_url, markdown_capture_stats, parse_docx_export


def sha256_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: str | Path, data: Any) -> None:
    atomic_write_bytes(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value or "unknown"))[:120]


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def write_archive(
    archive_dir: str | Path,
    item_id: str,
    *,
    url: str,
    title: str = "",
    raw_snapshot: str = "",
    extracted_text: str = "",
    metadata: dict[str, Any] | None = None,
    raw_bytes: bytes | None = None,
    binary_extension: str = "",
) -> dict[str, Any]:
    root = Path(archive_dir) / safe_id(item_id)
    root.mkdir(parents=True, exist_ok=True)
    raw_path = root / ("raw_snapshot" + (binary_extension or ".html"))
    extracted_path = root / "extracted_text.md"
    metadata_path = root / "metadata.json"
    structured_path = root / "structured.json"

    atomic_write_bytes(raw_path, raw_bytes if raw_bytes is not None else (raw_snapshot or "").encode("utf-8"))
    extracted_path.write_text(extracted_text or "", encoding="utf-8", newline="\n")

    raw_hash = hashlib.sha256(raw_bytes).hexdigest() if raw_bytes is not None else sha256_text(raw_snapshot)
    extracted_hash = sha256_text(extracted_text)
    archive = {
        "item_id": item_id,
        "url": url,
        "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "raw_snapshot_path": str(raw_path),
        "raw_snapshot_sha256": raw_hash,
        "extracted_text_path": str(extracted_path),
        "extracted_text_sha256": extracted_hash,
        "metadata_path": str(metadata_path),
        "structured_path": str(structured_path),
        "archive_path": str(root),
        "archive_hash": sha256_text(
            json.dumps(
                {
                    "url": url,
                    "title": title,
                    "raw_snapshot_sha256": raw_hash,
                    "extracted_text_sha256": extracted_hash,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        ),
    }
    meta = dict(metadata or {})
    meta["evidence_archive"] = archive
    metadata_path.write_text(json.dumps(_jsonable(meta), ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    structured_path.write_text(json.dumps(_jsonable(meta), ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return archive


def archive_child_path(archive: dict[str, Any] | None, *parts: str) -> Path | None:
    if not archive:
        return None
    archive_path = archive.get("archive_path")
    if not archive_path:
        return None
    path = Path(str(archive_path)).joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_view(item: dict[str, Any]) -> dict[str, Any]:
    keys = ("role", "path", "media_type", "label", "source", "description", "sha256", "bytes", "external_path")
    view = {key: item[key] for key in keys if item.get(key) not in (None, "")}
    view.setdefault("label", str(item.get("role") or "Artifact").replace("_", " ").title())
    return view


def enrich_archive(
    archive: dict[str, Any],
    *,
    artifacts: list[dict[str, Any]],
    title: str = "",
    url: str = "",
    transport: str = "",
) -> dict[str, Any]:
    """Add external bridge artifacts to the standard structured archive index."""
    root = Path(str(archive.get("archive_path") or ""))
    if not root.exists():
        raise FileNotFoundError(f"archive directory not found: {root}")
    data = read_archive_index(root)
    evidence = data.get("evidence_archive") or archive
    index = data.setdefault("archive_index", {})
    index.setdefault("version", 1)
    index.setdefault("title", title or evidence.get("title") or data.get("title") or "")
    index.setdefault("final_url", url or evidence.get("url") or data.get("final_url") or "")
    index.setdefault("transport", transport or data.get("transport") or "")
    files = index.setdefault("files", [])
    for key, role, media_type, hash_key in (
        ("raw_snapshot_path", "raw_snapshot", "text/html", "raw_snapshot_sha256"),
        ("extracted_text_path", "extracted_text", "text/markdown", "extracted_text_sha256"),
        ("metadata_path", "metadata", "application/json", ""),
        ("structured_path", "structured", "application/json", ""),
    ):
        path = str(evidence.get(key) or "")
        if path:
            item = {"role": role, "path": path, "media_type": media_type, "source": "archive"}
            if hash_key and evidence.get(hash_key):
                item["sha256"] = evidence[hash_key]
            _append_unique_role(files, item)

    preferred = index.setdefault("preferred_views", {"version": 1})
    preferred.setdefault("version", 1)
    for category in ("human_readable", "agent_readable", "media", "attachments", "evidence"):
        preferred.setdefault(category, [])
    for artifact in artifacts:
        item = {key: value for key, value in artifact.items() if key != "view"}
        if not item.get("path"):
            continue
        _append_unique_role(files, item)
        category = str(artifact.get("view") or "evidence")
        if category not in preferred:
            category = "evidence"
        _append_unique_role(preferred[category], _artifact_view(item))

    preferred["all"] = [
        item
        for category in ("human_readable", "agent_readable", "media", "attachments", "evidence")
        for item in preferred[category]
    ]
    primary = preferred.setdefault("primary", {})
    primary["human"] = preferred["human_readable"][0] if preferred["human_readable"] else primary.get("human", {})
    primary["agent"] = preferred["agent_readable"][0] if preferred["agent_readable"] else primary.get("agent", {})
    primary["images"] = preferred["media"][0] if preferred["media"] else primary.get("images", {})
    primary["attachments"] = preferred["attachments"][0] if preferred["attachments"] else primary.get("attachments", {})
    primary["evidence"] = preferred["evidence"][0] if preferred["evidence"] else primary.get("evidence", {})
    artifact_hashes = [
        {"role": str(item.get("role") or ""), "sha256": str(item.get("sha256") or "")}
        for item in files
        if item.get("sha256")
    ]
    evidence["artifact_hashes"] = artifact_hashes
    evidence["archive_hash"] = sha256_text(
        json.dumps(
            {
                "url": evidence.get("url") or url,
                "title": evidence.get("title") or title,
                "raw_snapshot_sha256": evidence.get("raw_snapshot_sha256", ""),
                "extracted_text_sha256": evidence.get("extracted_text_sha256", ""),
                "artifact_hashes": artifact_hashes,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    archive["archive_hash"] = evidence["archive_hash"]
    archive["artifact_hashes"] = artifact_hashes

    for name in ("structured.json", "metadata.json"):
        path = root / name
        if path.exists():
            path.write_text(json.dumps(_jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return index

def read_archive_index(archive_path: str | Path) -> dict[str, Any]:
    path = Path(archive_path)
    if path.is_dir():
        structured = path / "structured.json"
        metadata = path / "metadata.json"
        if structured.exists():
            path = structured
        elif metadata.exists():
            path = metadata
        else:
            raise FileNotFoundError(f"archive has no structured.json or metadata.json: {archive_path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_archive_file(root: Path, value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    candidates = [path]
    if not path.is_absolute():
        candidates.extend([root / path, root.parent / path])
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _append_unique_role(items: list[dict[str, Any]], item: dict[str, Any]) -> None:
    role = str(item.get("role") or "")
    path = str(item.get("path") or "")
    if any(str(existing.get("role") or "") == role and str(existing.get("path") or "") == path for existing in items):
        return
    items.append(item)


def compare_feishu_export_for_archive(archive_path: str | Path, *, timeout_seconds: int = 60) -> dict[str, Any]:
    root = Path(archive_path)
    if not root.is_dir():
        raise FileNotFoundError(f"archive directory not found: {archive_path}")
    data = read_archive_index(root)
    archive_index = data.setdefault("archive_index", {})
    evidence = data.get("evidence_archive") or data.get("archive") or {}
    url = archive_index.get("final_url") or data.get("final_url") or data.get("requested_url") or evidence.get("url") or ""
    if not is_feishu_doc_url(str(url)):
        raise ValueError("archive is not a Feishu/Lark doc/wiki URL")

    export_dir = root / "feishu_export"
    assets_dir = root / "image_assets"
    exported = export_feishu_docx(str(url), output_dir=str(export_dir), timeout=max(timeout_seconds, 60))
    parsed = parse_docx_export(str(exported.get("docx_path") or ""), assets_dir=str(assets_dir))
    export_stats = dict(parsed.get("markdown_stats") or {})

    preferred_markdown = archive_index.get("preferred_markdown") or {}
    browser_visible = archive_index.get("browser_visible") or {}
    markdown_path = (
        preferred_markdown.get("path")
        or browser_visible.get("path")
        or evidence.get("preferred_markdown_path")
        or evidence.get("extracted_text_path")
        or ""
    )
    markdown_file = _resolve_archive_file(root, str(markdown_path))
    browser_markdown = markdown_file.read_text(encoding="utf-8", errors="ignore") if markdown_file else str(data.get("extracted_text") or "")
    browser_source = "preferred_markdown" if preferred_markdown.get("path") else "browser_visible_markdown" if browser_visible.get("path") else "extracted_text"
    comparison = compare_feishu_captures(
        browser_markdown=browser_markdown,
        browser_stats=markdown_capture_stats(browser_markdown),
        export_stats=export_stats,
    )
    comparison.update(
        {
            "browser_source": browser_source,
            "browser_markdown_path": str(markdown_file or markdown_path or ""),
            "export_source": "feishu_docx_export",
            "docx_path": str(exported.get("docx_path") or ""),
        }
    )
    export_data = {
        "doc_token": exported.get("doc_token", ""),
        "ticket": exported.get("ticket", ""),
        "file_token": exported.get("file_token", ""),
        "file_name": exported.get("file_name", ""),
        "docx_path": exported.get("docx_path", ""),
        "bytes": exported.get("bytes", 0),
        "image_count": parsed.get("image_count", 0),
        "markdown_stats": export_stats,
        "text_chars": parsed.get("text_chars", 0),
        "heading_count": parsed.get("heading_count", 0),
        "table_count": parsed.get("table_count", 0),
        "mode": "archive_comparison",
    }
    archive_index["feishu_comparison"] = comparison
    archive_index.setdefault("site_data", {})["feishu_export"] = export_data
    data.setdefault("site_data", {})["feishu_export"] = export_data
    data.setdefault("extracted_metadata", {})["feishu_export"] = {
        "docx_path": exported.get("docx_path", ""),
        "image_count": parsed.get("image_count", 0),
        "asset_dir": parsed.get("asset_dir", ""),
        "markdown_stats": export_stats,
        "mode": "archive_comparison",
    }
    files = archive_index.setdefault("files", [])
    _append_unique_role(
        files,
        {
            "role": "docx_export",
            "path": exported.get("docx_path", ""),
            "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "source": "feishu_export",
            "bytes": exported.get("bytes", 0),
        },
    )
    preferred_views = archive_index.setdefault("preferred_views", {})
    human = preferred_views.setdefault("human_readable", [])
    view = {
        "role": "docx_export",
        "path": exported.get("docx_path", ""),
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "label": "DOCX export",
        "source": "feishu_export",
        "description": "official Feishu/Lark export preserved for human review and reprocessing",
        "image_count": parsed.get("image_count", 0),
        "bytes": exported.get("bytes", 0),
    }
    _append_unique_role(human, view)
    all_views = preferred_views.setdefault("all", [])
    _append_unique_role(all_views, view)

    for name in ("structured.json", "metadata.json"):
        path = root / name
        if path.exists():
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return {
        "status": "ok",
        "archive_path": str(root),
        "structured_path": str(root / "structured.json") if (root / "structured.json").exists() else "",
        "docx_path": str(exported.get("docx_path") or ""),
        "comparison": comparison,
    }


def list_archives(archive_dir: str | Path, *, limit: int = 50) -> list[dict[str, Any]]:
    root = Path(archive_dir)
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            data = read_archive_index(child)
        except Exception:
            continue
        archive_index = data.get("archive_index") or {}
        evidence = data.get("evidence_archive") or data.get("archive") or {}
        readable_path = evidence.get("readable_html_path") or ""
        preferred_markdown = archive_index.get("preferred_markdown") or {}
        preferred_markdown_path = evidence.get("preferred_markdown_path") or preferred_markdown.get("path") or ""
        browser_visible = archive_index.get("browser_visible") or {}
        reading_report_path = evidence.get("reading_report_path") or ""
        completeness = archive_index.get("completeness_check") or {}
        content_completeness = archive_index.get("content_completeness") or {}
        asset_completeness = archive_index.get("asset_completeness_check") or {}
        feishu_comparison = archive_index.get("feishu_comparison") or {}
        preferred_readable = archive_index.get("preferred_readable") or {}
        preferred_views = archive_index.get("preferred_views") or {}
        primary_views = preferred_views.get("primary") or {}
        primary_human = primary_views.get("human") or {}
        primary_agent = primary_views.get("agent") or {}
        review_recommendation = archive_index.get("review_recommendation") or {}
        browser_visible_path = evidence.get("browser_visible_path") or browser_visible.get("path") or ""
        browser_visible_html_path = evidence.get("browser_visible_html_path") or browser_visible.get("html_path") or ""
        browser_visible_structure = browser_visible.get("markdown_structure") or {}
        if not readable_path:
            for file_item in archive_index.get("files") or []:
                if file_item.get("role") == "readable_html":
                    readable_path = file_item.get("path") or ""
                    break
        if not browser_visible_path:
            for file_item in archive_index.get("files") or []:
                if file_item.get("role") == "browser_visible":
                    browser_visible_path = file_item.get("path") or ""
                    break
        if not browser_visible_html_path:
            for file_item in archive_index.get("files") or []:
                if file_item.get("role") == "browser_visible_html":
                    browser_visible_html_path = file_item.get("path") or ""
                    break
        if not preferred_markdown_path:
            for file_item in archive_index.get("files") or []:
                if file_item.get("role") == "preferred_markdown":
                    preferred_markdown_path = file_item.get("path") or ""
                    break
        if not reading_report_path:
            for file_item in archive_index.get("files") or []:
                if file_item.get("role") == "reading_report":
                    reading_report_path = file_item.get("path") or ""
                    break
        preferred_readable_path = primary_human.get("path") or preferred_readable.get("html_path") or readable_path or browser_visible_html_path
        preferred_agent_path = primary_agent.get("path") or preferred_markdown_path
        items.append(
            {
                "archive_path": str(child),
                "structured_path": str(child / "structured.json") if (child / "structured.json").exists() else "",
                "metadata_path": str(child / "metadata.json") if (child / "metadata.json").exists() else "",
                "readable_html_path": str(readable_path),
                "preferred_markdown_path": str(preferred_agent_path),
                "reading_report_path": str(reading_report_path),
                "preferred_markdown_source": str(preferred_markdown.get("source") or evidence.get("preferred_markdown_source") or ""),
                "browser_visible_path": str(browser_visible_path),
                "browser_visible_html_path": str(browser_visible_html_path),
                "preferred_readable_path": str(preferred_readable_path),
                "preferred_readable_role": str(primary_human.get("role") or preferred_readable.get("role") or ""),
                "preferred_readable_source": str(primary_human.get("source") or preferred_readable.get("source") or ""),
                "preferred_agent_path": str(preferred_agent_path),
                "preferred_agent_role": str(primary_agent.get("role") or ""),
                "preferred_images_path": str((primary_views.get("images") or {}).get("path") or ""),
                "preferred_attachments_path": str((primary_views.get("attachments") or {}).get("path") or ""),
                "review_action": str(review_recommendation.get("action") or ""),
                "review_recommendation": str(review_recommendation.get("reason") or ""),
                "title": archive_index.get("title") or data.get("title") or "",
                "url": archive_index.get("final_url") or data.get("final_url") or evidence.get("url") or "",
                "transport": archive_index.get("transport") or data.get("transport") or "",
                "created_at": evidence.get("created_at") or data.get("fetched_at") or archive_index.get("fetched_at") or "",
                "file_count": len(archive_index.get("files") or []),
                "image_asset_count": len(archive_index.get("image_assets") or data.get("image_assets") or []),
                "attachment_count": len(archive_index.get("attachments") or []),
                "browser_visible_block_count": int(browser_visible_structure.get("block_count") or 0),
                "browser_visible_heading_count": int(browser_visible_structure.get("dom_heading_count") or browser_visible_structure.get("heading_count") or 0),
                "browser_visible_image_count": int(browser_visible_structure.get("image_count") or 0),
                "completeness_status": completeness.get("status", ""),
                "content_completeness_status": content_completeness.get("overall_status", ""),
                "content_text_score": content_completeness.get("text_score", 0),
                "content_image_score": content_completeness.get("image_score", 0),
                "content_structure_score": content_completeness.get("structure_score", 0),
                "content_recommended_next_action": content_completeness.get("recommended_next_action", ""),
                "completeness_char_ratio": completeness.get("char_ratio", 0),
                "completeness_block_ratio": completeness.get("block_ratio", 0),
                "asset_completeness_status": asset_completeness.get("status", ""),
                "downloaded_asset_count": asset_completeness.get("downloaded_asset_count", 0),
                "downloaded_only_image_count": asset_completeness.get("downloaded_only_count", 0),
                "browser_visible_only_image_count": asset_completeness.get("browser_visible_only_count", 0),
                "failed_image_asset_count": asset_completeness.get("failed_count", 0),
                "feishu_comparison_status": feishu_comparison.get("status", ""),
                "feishu_comparison_reasons": feishu_comparison.get("reasons", []),
                "feishu_comparison_docx_path": feishu_comparison.get("docx_path", ""),
                "status": data.get("status") or "",
            }
        )
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return items[: max(1, limit)]


def pack_archive(archive_path: str | Path, output_path: str | Path = "") -> dict[str, Any]:
    root = Path(archive_path)
    if not root.is_dir():
        raise FileNotFoundError(f"archive directory not found: {archive_path}")
    if not (root / "structured.json").exists() and not (root / "metadata.json").exists():
        raise FileNotFoundError(f"archive has no structured.json or metadata.json: {archive_path}")
    zip_path = Path(output_path) if output_path else root.with_suffix(".zip")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    file_count = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            zf.write(path, arcname=str(path.relative_to(root.parent)))
            file_count += 1
    data = zip_path.read_bytes()
    return {
        "archive_path": str(root),
        "zip_path": str(zip_path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "file_count": file_count,
    }
