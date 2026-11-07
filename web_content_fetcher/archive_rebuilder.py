from __future__ import annotations

import json
import re
import shutil
from html import escape
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .archive import read_archive_index, sha256_text
from .core import markdown_to_readable_html


REBUILD_SCHEMA_VERSION = 1
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


@dataclass
class RebuildArchiveRequest:
    archive_path: str
    with_visual_check: bool = False
    visual_dir: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_json(path: Path) -> Any:
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _first_existing_path(*values: str) -> Path | None:
    for value in values:
        if not value:
            continue
        path = Path(value)
        if path.exists():
            return path
    return None


def _copy_if_exists(src: Path | None, dst: Path) -> str:
    if not src or not src.exists() or not src.is_file():
        return ""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dst.resolve():
        shutil.copyfile(src, dst)
    return str(dst)


def _archive_file_by_role(archive_index: dict[str, Any], role: str) -> str:
    for item in archive_index.get("files") or []:
        if item.get("role") == role and item.get("path"):
            return str(item.get("path"))
    return ""


def _archive_paths(data: dict[str, Any], archive_root: Path) -> dict[str, str]:
    archive_index = data.get("archive_index") or {}
    evidence = data.get("evidence_archive") or data.get("archive") or {}
    preferred_markdown = archive_index.get("preferred_markdown") or {}
    page_snapshots = archive_index.get("page_snapshots") or {}
    browser_visible = archive_index.get("browser_visible") or {}
    return {
        "structured": str(archive_root / "structured.json"),
        "source_html": (
            page_snapshots.get("page_html_path")
            or _archive_file_by_role(archive_index, "page_html")
            or _archive_file_by_role(archive_index, "browser_visible_html")
            or _archive_file_by_role(archive_index, "raw_snapshot")
            or evidence.get("raw_snapshot_path")
            or str(archive_root / "raw_snapshot.html")
        ),
        "source_markdown": (
            preferred_markdown.get("path")
            or evidence.get("preferred_markdown_path")
            or _archive_file_by_role(archive_index, "preferred_markdown")
            or browser_visible.get("path")
            or _archive_file_by_role(archive_index, "browser_visible")
            or evidence.get("extracted_text_path")
            or str(archive_root / "extracted_text.md")
        ),
        "screenshot": page_snapshots.get("screenshot_path") or _archive_file_by_role(archive_index, "screenshot") or evidence.get("screenshot_path") or "",
        "image_gallery": (archive_index.get("image_gallery") or {}).get("path") or evidence.get("image_gallery_path") or str(archive_root / "image_gallery.html"),
    }


def _copy_rebuild_input(archive_root: Path, data: dict[str, Any]) -> dict[str, Any]:
    archive_index = data.get("archive_index") or {}
    paths = _archive_paths(data, archive_root)
    rebuild_input = archive_root / "rebuild_input"
    images_dir = rebuild_input / "images"

    source_html = _copy_if_exists(_first_existing_path(paths["source_html"]), rebuild_input / "source.html")
    source_markdown = _copy_if_exists(_first_existing_path(paths["source_markdown"]), rebuild_input / "source.md")
    screenshot = _copy_if_exists(_first_existing_path(paths["screenshot"]), rebuild_input / "screenshot.png")
    structured = _copy_if_exists(_first_existing_path(paths["structured"]), rebuild_input / "structured.json")

    image_assets = []
    for index, item in enumerate(archive_index.get("image_assets") or data.get("image_assets") or [], start=1):
        asset_path = str(item.get("asset_path") or "")
        src = _first_existing_path(asset_path)
        copied = ""
        if src:
            copied = _copy_if_exists(src, images_dir / src.name)
        image_assets.append({**dict(item), "index": index, "rebuild_input_path": copied})

    manifest = {
        "schema_version": REBUILD_SCHEMA_VERSION,
        "created_at": _now_iso(),
        "archive_path": str(archive_root),
        "source_url": archive_index.get("final_url") or data.get("final_url") or data.get("requested_url") or "",
        "title": archive_index.get("title") or data.get("title") or "",
        "inputs": {
            "source_html": source_html,
            "source_markdown": source_markdown,
            "screenshot": screenshot,
            "structured": structured,
            "images_dir": str(images_dir) if image_assets else "",
            "image_gallery": paths["image_gallery"] if Path(paths["image_gallery"]).exists() else "",
        },
        "expected_visual_outputs": {
            "layout_ocr_md": str(archive_root / "visual_check" / "layout_ocr.md"),
            "visual_blocks_json": str(archive_root / "visual_check" / "visual_blocks.json"),
            "table_candidates_json": str(archive_root / "visual_check" / "table_candidates.json"),
            "image_positions_json": str(archive_root / "visual_check" / "image_positions.json"),
        },
        "image_assets": image_assets,
        "instructions": [
            "Use source HTML/Markdown as evidence, not as the only truth.",
            "Use screenshot and image assets for layout, table, and image-position verification.",
            "Write visual outputs only; do not overwrite source.md or structured.json.",
        ],
    }
    _write_json(rebuild_input / "manifest.json", manifest)
    return manifest


def _load_visual_layer(archive_root: Path, visual_dir: str = "") -> dict[str, Any]:
    root = Path(visual_dir) if visual_dir else archive_root / "visual_check"
    layout_ocr_path = root / "layout_ocr.md"
    visual_blocks_path = root / "visual_blocks.json"
    table_candidates_path = root / "table_candidates.json"
    image_positions_path = root / "image_positions.json"
    return {
        "root": str(root),
        "layout_ocr_path": str(layout_ocr_path) if layout_ocr_path.exists() else "",
        "layout_ocr": _read_text(layout_ocr_path),
        "visual_blocks_path": str(visual_blocks_path) if visual_blocks_path.exists() else "",
        "visual_blocks": _read_json(visual_blocks_path) or [],
        "table_candidates_path": str(table_candidates_path) if table_candidates_path.exists() else "",
        "table_candidates": _read_json(table_candidates_path) or [],
        "image_positions_path": str(image_positions_path) if image_positions_path.exists() else "",
        "image_positions": _read_json(image_positions_path) or [],
    }


def _markdown_sections(markdown: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line_no, line in enumerate((markdown or "").splitlines(), start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            current = {"level": len(match.group(1)), "title": match.group(2).strip(), "line": line_no, "text": ""}
            sections.append(current)
        elif current is not None:
            text = line.strip()
            if text:
                current["text"] = (str(current.get("text") or "") + "\n" + text).strip()
    if not sections and markdown.strip():
        sections.append({"level": 1, "title": "正文", "line": 1, "text": markdown.strip()})
    return sections


def _markdown_images(markdown: str) -> list[dict[str, Any]]:
    images = []
    for line_no, line in enumerate((markdown or "").splitlines(), start=1):
        for match in IMAGE_RE.finditer(line):
            images.append({"index": len(images) + 1, "line": line_no, "alt": match.group(1), "src": match.group(2)})
    return images


def _markdown_table_count(markdown: str) -> int:
    count = 0
    lines = (markdown or "").splitlines()
    for idx in range(len(lines) - 1):
        if "|" in lines[idx] and re.search(r"\|\s*:?-{3,}:?\s*\|", lines[idx + 1]):
            count += 1
    return count


def _quality_report(data: dict[str, Any], markdown: str, visual: dict[str, Any]) -> dict[str, Any]:
    archive_index = data.get("archive_index") or {}
    browser_visible = archive_index.get("browser_visible") or {}
    feishu_comparison = archive_index.get("feishu_comparison") or {}
    dom_text_chars = len(markdown or "")
    ocr_text = str(visual.get("layout_ocr") or "")
    ocr_text_chars = len(ocr_text)
    dom_images = len(_markdown_images(markdown))
    gallery_counts = (archive_index.get("image_gallery") or {}).get("counts") or {}
    gallery_images = int(gallery_counts.get("total") or gallery_counts.get("extracted_text") or 0)
    image_positions = visual.get("image_positions") or []
    ocr_image_regions = len(
        [
            item
            for item in image_positions
            if isinstance(item, dict)
            and (
                item.get("type") in {"screenshot_image_region", "ocr_image_region"}
                or bool(item.get("bbox"))
            )
        ]
    )
    table_count = _markdown_table_count(markdown)
    visual_table_count = len(visual.get("table_candidates") or [])
    source_quality = data.get("extraction_quality") or {}
    status = "ok"
    missing_or_uncertain: list[dict[str, Any]] = []
    if source_quality.get("status") in {"empty", "too_short", "suspect_shell", "suspect_incomplete"}:
        status = "needs_review"
        missing_or_uncertain.append({"type": "source_quality", "message": f"source extraction quality is {source_quality.get('status')}"})
    if visual.get("layout_ocr") and ocr_text_chars > dom_text_chars * 1.25:
        status = "needs_review"
        missing_or_uncertain.append({"type": "ocr_more_text", "message": "OCR text is substantially longer than DOM/Markdown text"})
    if ocr_image_regions and dom_images < ocr_image_regions:
        status = "needs_review"
        missing_or_uncertain.append({"type": "possible_missing_images", "message": "visual image regions exceed Markdown image references"})
    if visual_table_count and table_count < visual_table_count:
        status = "needs_review"
        missing_or_uncertain.append({"type": "possible_missing_tables", "message": "visual table candidates exceed Markdown tables"})
    if feishu_comparison.get("status") == "needs_review":
        status = "needs_review"
        reasons = "; ".join(str(item) for item in feishu_comparison.get("reasons", []))
        missing_or_uncertain.append(
            {
                "type": "feishu_export_comparison",
                "message": f"Feishu/Lark DOCX export differs from browser capture{': ' + reasons if reasons else ''}",
            }
        )
    if not markdown.strip():
        status = "empty"
        missing_or_uncertain.append({"type": "empty_markdown", "message": "no source Markdown text is available"})
    return {
        "status": status,
        "dom_text_chars": dom_text_chars,
        "ocr_text_chars": ocr_text_chars,
        "dom_image_count": dom_images,
        "gallery_image_count": gallery_images,
        "ocr_image_region_count": ocr_image_regions,
        "table_count": table_count,
        "visual_table_candidate_count": visual_table_count,
        "browser_visible_block_count": int(browser_visible.get("block_count") or 0),
        "feishu_comparison": feishu_comparison,
        "source_quality": source_quality,
        "missing_or_uncertain": missing_or_uncertain,
        "recommendation": _recommendation(status, source_quality, bool(visual.get("layout_ocr"))),
    }


def _recommendation(status: str, source_quality: dict[str, Any], has_visual: bool) -> str:
    if status == "ok":
        return "final artifacts are suitable for human and agent review"
    if not has_visual:
        return "run ebook_markdown_pipeline process-web-archive on rebuild_input or screenshot, then rebuild again"
    if source_quality.get("status") in {"empty", "suspect_shell"}:
        return "retry capture with authenticated browser/export/API; use OCR only as a weak fallback"
    return "compare final_readable.html with layout_ocr.md and resolve flagged gaps"


def _compose_final_agent(markdown: str, data: dict[str, Any], visual: dict[str, Any], quality: dict[str, Any]) -> str:
    archive_index = data.get("archive_index") or {}
    feishu_comparison = quality.get("feishu_comparison") or {}
    lines = [
        f"# {archive_index.get('title') or data.get('title') or archive_index.get('final_url') or 'Web archive'}",
        "",
        f"- Source URL: {archive_index.get('final_url') or data.get('final_url') or data.get('requested_url') or ''}",
        f"- Fetched at: {archive_index.get('fetched_at') or data.get('fetched_at') or ''}",
        f"- Transport: {archive_index.get('transport') or data.get('transport') or ''}",
        f"- Extraction engine: {data.get('extraction_engine') or ''}",
        f"- Rebuild quality: {quality.get('status') or ''}",
        "",
        "## Content",
        "",
        markdown.strip(),
    ]
    if visual.get("layout_ocr"):
        lines.extend(["", "## Visual OCR Check", "", str(visual.get("layout_ocr") or "").strip()])
    if quality.get("missing_or_uncertain"):
        lines.extend(["", "## Missing Or Uncertain", ""])
        for item in quality.get("missing_or_uncertain") or []:
            lines.append(f"- {item.get('type')}: {item.get('message')}")
    if feishu_comparison:
        lines.extend(
            [
                "",
                "## Feishu Export Comparison",
                "",
                f"- Status: {feishu_comparison.get('status') or ''}",
                f"- DOCX: {feishu_comparison.get('docx_path') or ''}",
                f"- Reasons: {'; '.join(str(item) for item in feishu_comparison.get('reasons', []))}",
            ]
        )
    return "\n".join(line for line in lines if line is not None).rstrip() + "\n"


def _toc_html(markdown: str) -> str:
    items: list[str] = []
    for line in (markdown or "").splitlines():
        match = re.match(r"^(#{1,4})\s+(.+?)\s*$", line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        if not title:
            continue
        items.append(f"<li class=\"wcf-toc-level-{level}\">{escape(title)}</li>")
    if not items:
        return ""
    return (
        "<nav class=\"wcf-rebuild-toc\">"
        "<h2>目录</h2>"
        "<ol>"
        + "".join(items)
        + "</ol>"
        "</nav>"
    )


def _final_html(markdown: str, data: dict[str, Any], quality: dict[str, Any]) -> str:
    archive_index = data.get("archive_index") or {}
    title = archive_index.get("title") or data.get("title") or "Final readable"
    body = markdown_to_readable_html(markdown, title=str(title))
    feishu_comparison = quality.get("feishu_comparison") or {}
    feishu_html = ""
    if feishu_comparison:
        feishu_html = (
            "<br>"
            f"<strong>Feishu comparison:</strong> {escape(str(feishu_comparison.get('status') or ''))}"
            f" {escape(str('; '.join(str(item) for item in feishu_comparison.get('reasons', []))))}"
        )
    banner = (
        "<section class=\"wcf-rebuild-banner\">"
        f"<strong>Source:</strong> {archive_index.get('final_url') or data.get('final_url') or data.get('requested_url') or ''}<br>"
        f"<strong>Quality:</strong> {quality.get('status') or ''}<br>"
        f"<strong>Recommendation:</strong> {quality.get('recommendation') or ''}"
        f"{feishu_html}"
        "</section>"
    )
    toc = _toc_html(markdown)
    return body.replace("<body>", f"<body>\n{banner}\n{toc}", 1)


def _view(role: str, path: str, media_type: str, label: str) -> dict[str, str]:
    return {"role": role, "path": path, "media_type": media_type, "label": label}


def _visual_artifact_views(visual: dict[str, Any]) -> list[dict[str, str]]:
    candidates = [
        ("layout_ocr_path", "layout_ocr", "text/markdown", "Visual OCR Markdown"),
        ("visual_blocks_path", "visual_blocks", "application/json", "Visual blocks JSON"),
        ("table_candidates_path", "table_candidates", "application/json", "Table candidates JSON"),
        ("image_positions_path", "image_positions", "application/json", "Image positions JSON"),
    ]
    views: list[dict[str, str]] = []
    for key, role, media_type, label in candidates:
        path = str(visual.get(key) or "")
        if path:
            views.append(_view(role, path, media_type, label))
    return views


def _append_rebuild_views(data: dict[str, Any], result: dict[str, Any], visual: dict[str, Any]) -> dict[str, Any]:
    updated = dict(data)
    archive_index = dict(updated.get("archive_index") or {})
    preferred_views = dict(archive_index.get("preferred_views") or {})
    human = list(preferred_views.get("human_readable") or [])
    agent = list(preferred_views.get("agent_readable") or [])
    evidence = list(preferred_views.get("evidence") or [])
    visual_views = _visual_artifact_views(visual)
    human.insert(0, _view("final_readable", result["final_readable_html_path"], "text/html", "Final readable HTML"))
    agent.insert(0, _view("final_agent", result["final_agent_markdown_path"], "text/markdown", "Final Agent Markdown"))
    agent.insert(1, _view("final_structured", result["final_structured_json_path"], "application/json", "Final structured JSON"))
    for visual_view in reversed(visual_views):
        agent.insert(2, visual_view)
    evidence.insert(0, _view("completeness_report", result["completeness_report_path"], "application/json", "Completeness report"))
    for visual_view in reversed(visual_views):
        evidence.insert(1, visual_view)
    preferred_views["human_readable"] = human
    preferred_views["agent_readable"] = agent
    preferred_views["evidence"] = evidence
    preferred_views["visual_check"] = visual_views
    preferred_views["primary"] = {
        **dict(preferred_views.get("primary") or {}),
        "human": human[0] if human else {},
        "agent": agent[0] if agent else {},
        "evidence": evidence[0] if evidence else {},
        "visual": visual_views[0] if visual_views else {},
    }
    all_views = []
    seen_views = set()
    for key in ("human_readable", "agent_readable", "visual_check", "media", "attachments", "evidence"):
        for item in preferred_views.get(key) or []:
            marker = (str(item.get("role") or ""), str(item.get("path") or ""))
            if marker in seen_views:
                continue
            seen_views.add(marker)
            all_views.append(item)
    preferred_views["all"] = all_views
    archive_index["preferred_views"] = preferred_views
    archive_index["visual_check"] = {
        "root": visual.get("root") or "",
        "outputs": {
            "layout_ocr": visual.get("layout_ocr_path") or "",
            "visual_blocks": visual.get("visual_blocks_path") or "",
            "table_candidates": visual.get("table_candidates_path") or "",
            "image_positions": visual.get("image_positions_path") or "",
        },
        "artifact_count": len(visual_views),
    }
    archive_index["archive_rebuild"] = result
    updated["archive_index"] = archive_index
    updated["archive_rebuild"] = result
    return updated


def rebuild_archive(request: RebuildArchiveRequest) -> dict[str, Any]:
    archive_root = Path(request.archive_path)
    if not archive_root.is_dir():
        raise FileNotFoundError(f"archive directory not found: {request.archive_path}")
    data = read_archive_index(archive_root)
    manifest = _copy_rebuild_input(archive_root, data)
    source_md_path = Path(str(manifest.get("inputs", {}).get("source_markdown") or ""))
    markdown = _read_text(source_md_path)
    visual = _load_visual_layer(archive_root, request.visual_dir)
    if request.with_visual_check:
        _write_json(
            archive_root / "visual_check_request.json",
            {
                "schema_version": REBUILD_SCHEMA_VERSION,
                "created_at": _now_iso(),
                "status": "ready_for_visual_check" if not visual.get("layout_ocr") else "visual_outputs_detected",
                "archive_path": str(archive_root),
                "rebuild_input_manifest": str(archive_root / "rebuild_input" / "manifest.json"),
                "expected_outputs": manifest.get("expected_visual_outputs") or {},
                "suggested_command": f"process-web-archive {archive_root}",
            },
        )
    quality = _quality_report(data, markdown, visual)
    final_agent = _compose_final_agent(markdown, data, visual, quality)
    final_readable = _final_html(final_agent, data, quality)
    final_structured = {
        "schema_version": REBUILD_SCHEMA_VERSION,
        "created_at": _now_iso(),
        "archive_path": str(archive_root),
        "source_url": (data.get("archive_index") or {}).get("final_url") or data.get("final_url") or data.get("requested_url") or "",
        "title": (data.get("archive_index") or {}).get("title") or data.get("title") or "",
        "sections": _markdown_sections(markdown),
        "tables": {"markdown_table_count": _markdown_table_count(markdown), "visual_candidates": visual.get("table_candidates") or []},
        "images": {"markdown_images": _markdown_images(markdown), "visual_positions": visual.get("image_positions") or [], "archive_assets": (data.get("archive_index") or {}).get("image_assets") or data.get("image_assets") or []},
        "attachments": (data.get("archive_index") or {}).get("attachments") or [],
        "source_files": manifest.get("inputs") or {},
        "visual_layer": {k: v for k, v in visual.items() if k not in {"layout_ocr"}},
        "quality": quality,
        "feishu_comparison": quality.get("feishu_comparison") or {},
        "missing_or_uncertain": quality.get("missing_or_uncertain") or [],
    }
    completeness_report = {
        "schema_version": REBUILD_SCHEMA_VERSION,
        "created_at": _now_iso(),
        **quality,
        "source_files": manifest.get("inputs") or {},
        "visual_outputs": {
            "layout_ocr_path": visual.get("layout_ocr_path") or "",
            "visual_blocks_path": visual.get("visual_blocks_path") or "",
            "table_candidates_path": visual.get("table_candidates_path") or "",
            "image_positions_path": visual.get("image_positions_path") or "",
        },
    }

    final_agent_path = archive_root / "final_agent.md"
    final_readable_path = archive_root / "final_readable.html"
    final_structured_path = archive_root / "final_structured.json"
    completeness_path = archive_root / "completeness_report.json"
    result = {
        "schema_version": REBUILD_SCHEMA_VERSION,
        "status": "ok",
        "archive_path": str(archive_root),
        "rebuild_input_manifest_path": str(archive_root / "rebuild_input" / "manifest.json"),
        "visual_check_request_path": str(archive_root / "visual_check_request.json") if request.with_visual_check else "",
        "final_readable_html_path": str(final_readable_path),
        "final_agent_markdown_path": str(final_agent_path),
        "final_structured_json_path": str(final_structured_path),
        "completeness_report_path": str(completeness_path),
        "quality": quality,
    }
    updated = _append_rebuild_views(data, result, visual)
    final_structured["preferred_views"] = (updated.get("archive_index") or {}).get("preferred_views") or {}

    final_agent_path.write_text(final_agent, encoding="utf-8", newline="\n")
    final_readable_path.write_text(final_readable, encoding="utf-8", newline="\n")
    _write_json(final_structured_path, final_structured)
    _write_json(completeness_path, completeness_report)

    for name in ("structured.json", "metadata.json"):
        path = archive_root / name
        if path.exists():
            _write_json(path, updated)
    return result
