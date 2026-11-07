from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import FetchRequest, FetchResult, build_archive_index, fetch_content, markdown_structure_index
from .document_router import fetch_document_link
from .archive import atomic_write_json, sha256_file


@dataclass
class BatchFetchRequest:
    urls: list[str]
    archive_dir: str = ""
    timeout_seconds: int = 30
    backend: str = "auto"
    extractor: str = "auto"
    as_documents: bool = True
    browser_profile: str = ""
    browser_user_data_dir: str = ""
    browser_executable_path: str = ""
    browser_storage_state: str = ""
    headless: bool = False
    browser_dismiss_obstructive_overlays: bool = True
    check_robots: bool = True
    compare_feishu_export: bool = False
    wechat_api_base_url: str = ""
    include_results: bool = False
    save_report: bool = True
    resume_from: str = ""
    max_attempts: int = 100
    max_batch_seconds: float = 600
    same_error_limit: int = 3


def read_url_list(path: str) -> list[str]:
    urls: list[str] = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            urls.append(stripped)
    return urls


def read_batch_summary(batch_path: str | Path) -> dict[str, Any]:
    path = Path(batch_path)
    if path.is_dir():
        path = path / "summary.json"
    return json.loads(path.read_text(encoding="utf-8"))


def list_batch_reports(archive_dir: str | Path, *, limit: int = 20) -> list[dict[str, Any]]:
    root = Path(archive_dir) / "batches"
    if not root.exists():
        return []
    reports: list[dict[str, Any]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        summary_path = child / "summary.json"
        if not summary_path.exists():
            continue
        try:
            data = read_batch_summary(summary_path)
        except Exception:
            continue
        report = data.get("batch_report") or {}
        batch_review_path = child / "batch_review.json"
        needs_login = 0
        needs_export = 0
        if batch_review_path.exists():
            try:
                review = json.loads(batch_review_path.read_text(encoding="utf-8"))
                for review_item in review.get("items", []):
                    action_text = str(review_item.get("recommended_next_action") or "").lower()
                    reasons_text = " ".join(str(reason).lower() for reason in review_item.get("errors", []))
                    combined = f"{action_text} {reasons_text}"
                    if "login" in combined or "profile" in combined or "browser" in combined:
                        needs_login += 1
                    if "export" in combined or "api" in combined or "feishu_export" in combined:
                        needs_export += 1
            except Exception:
                pass
        reports.append(
            {
                "batch_path": str(child),
                "summary_path": str(summary_path),
                "batch_review_json_path": str(batch_review_path) if batch_review_path.exists() else report.get("batch_review_json_path", ""),
                "items_jsonl_path": str(child / "items.jsonl") if (child / "items.jsonl").exists() else report.get("items_jsonl_path", ""),
                "low_quality_jsonl_path": str(child / "low-quality.jsonl") if (child / "low-quality.jsonl").exists() else report.get("low_quality_jsonl_path", ""),
                "failed_jsonl_path": str(child / "failed.jsonl") if (child / "failed.jsonl").exists() else report.get("failed_jsonl_path", ""),
                "rerun_urls_path": str(child / "rerun-urls.txt") if (child / "rerun-urls.txt").exists() else report.get("rerun_urls_path", ""),
                "review_markdown_path": str(child / "review.md") if (child / "review.md").exists() else report.get("review_markdown_path", ""),
                "status": data.get("status", ""),
                "total": int(data.get("total") or 0),
                "ok": int(data.get("ok") or 0),
                "failed": int(data.get("failed") or 0),
                "low_quality": int(data.get("low_quality") or 0),
                "needs_login": needs_login,
                "needs_export_or_api": needs_export,
                "created_at": child.name,
            }
        )
    reports.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return reports[: max(1, limit)]


def resolve_rerun_urls_path(batch_path: str | Path = "", rerun_urls_path: str | Path = "") -> Path:
    if rerun_urls_path:
        return Path(rerun_urls_path)
    if not batch_path:
        raise ValueError("batch_path or rerun_urls_path is required")
    path = Path(batch_path)
    if path.is_dir():
        summary_path = path / "summary.json"
        if summary_path.exists():
            try:
                report = (read_batch_summary(summary_path).get("batch_report") or {})
                if report.get("rerun_urls_path"):
                    return Path(str(report["rerun_urls_path"]))
            except Exception:
                pass
        return path / "rerun-urls.txt"
    data = read_batch_summary(path)
    report = data.get("batch_report") or {}
    if not report.get("rerun_urls_path"):
        raise ValueError("summary does not contain batch_report.rerun_urls_path")
    return Path(str(report["rerun_urls_path"]))


def infer_archive_dir_from_rerun_path(path: Path) -> str:
    parts = path.parts
    if len(parts) >= 3 and parts[-3] == "batches":
        return str(Path(*parts[:-3]))
    return ""


def markdown_table_count(markdown: str) -> int:
    count = 0
    lines = str(markdown or "").splitlines()
    for index, line in enumerate(lines[1:], start=1):
        stripped = line.strip()
        previous = lines[index - 1].strip()
        if "|" in previous and "|" in stripped and set(stripped.replace("|", "").replace(":", "").replace("-", "").strip()) <= set():
            count += 1
    return count


def _compact_preferred_views(preferred_views: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    primary = preferred_views.get("primary") or {}
    for key, value in primary.items():
        if isinstance(value, dict):
            compact[key] = {
                "role": value.get("role", ""),
                "label": value.get("label", ""),
                "path": value.get("path") or value.get("html_path") or value.get("markdown_path") or "",
                "format": value.get("format", ""),
            }
    if compact:
        return {"primary": compact}
    return {}


def summarize_result(index: int, result: FetchResult) -> dict[str, Any]:
    result.assess_validity()
    archive = result.archive or {}
    quality = result.extraction_quality or {}
    markdown_index = markdown_structure_index(result.extracted_text or "")
    table_count = markdown_table_count(result.extracted_text or "")
    archive_index = build_archive_index(result)
    completeness = archive_index.get("completeness_check") or {}
    asset_completeness = archive_index.get("asset_completeness_check") or {}
    content_completeness = archive_index.get("content_completeness") or {}
    browser_visible = archive_index.get("browser_visible") or {}
    browser_visible_structure = browser_visible.get("markdown_structure") or {}
    browser_visible_metrics = browser_visible.get("metrics") or {}
    scroll_metrics = browser_visible_metrics.get("scroll") or {}
    preferred_markdown = archive_index.get("preferred_markdown") or {}
    preferred_markdown_structure = preferred_markdown.get("structure") or {}
    preferred_views = archive_index.get("preferred_views") or {}
    review_recommendation = archive_index.get("review_recommendation") or {}
    feishu_comparison = archive_index.get("feishu_comparison") or {}
    site_strategy = ((archive_index.get("site_data") or {}).get("site_strategy") or {})
    recommended_next_action = content_completeness.get("recommended_next_action") or review_recommendation.get("action", "")
    recommended_path = content_completeness.get("recommended_path") or review_recommendation.get("path", "")
    return {
        "index": index,
        "requested_url": result.requested_url,
        "final_url": result.final_url,
        "status": result.status,
        "transport": result.transport,
        "transport_ok": result.transport_ok,
        "content_ok": result.content_ok,
        "http_status": result.http_status,
        "title": result.title,
        "strategy": site_strategy.get("site_key", ""),
        "route": site_strategy.get("kind", ""),
        "extraction_engine": result.extraction_engine,
        "quality_status": quality.get("status", ""),
        "completeness_status": completeness.get("status", ""),
        "content_completeness_status": content_completeness.get("overall_status", ""),
        "content_text_score": content_completeness.get("text_score", 0),
        "content_image_score": content_completeness.get("image_score", 0),
        "content_table_score": content_completeness.get("table_score", 0),
        "content_structure_score": content_completeness.get("structure_score", 0),
        "content_attachment_score": content_completeness.get("attachment_score", 0),
        "content_completeness_reasons": content_completeness.get("reasons", []),
        "recommended_next_action": recommended_next_action,
        "recommended_path": recommended_path,
        "completeness_char_ratio": completeness.get("char_ratio", 0),
        "completeness_block_ratio": completeness.get("block_ratio", 0),
        "completeness_reasons": completeness.get("reasons", []),
        "asset_completeness_status": asset_completeness.get("status", ""),
        "asset_completeness_reasons": asset_completeness.get("reasons", []),
        "downloaded_asset_count": asset_completeness.get("downloaded_asset_count", 0),
        "downloaded_only_image_count": asset_completeness.get("downloaded_only_count", 0),
        "browser_visible_only_image_count": asset_completeness.get("browser_visible_only_count", 0),
        "failed_image_asset_count": asset_completeness.get("failed_count", 0),
        "missing_heading_count": len(completeness.get("missing_headings") or []),
        "missing_image_count": len(completeness.get("missing_images") or []),
        "missing_headings": completeness.get("missing_headings", [])[:5],
        "missing_images": completeness.get("missing_images", [])[:5],
        "review_action": review_recommendation.get("action", ""),
        "review_recommendation": review_recommendation.get("reason", ""),
        "review_path": review_recommendation.get("path", ""),
        "feishu_comparison_status": feishu_comparison.get("status", ""),
        "feishu_comparison_reasons": feishu_comparison.get("reasons", []),
        "feishu_comparison_docx_path": feishu_comparison.get("docx_path", ""),
        "preferred_markdown_path": preferred_markdown.get("path", ""),
        "preferred_markdown_chars": preferred_markdown.get("chars", 0),
        "preferred_markdown_heading_count": preferred_markdown_structure.get("heading_count", 0),
        "preferred_markdown_image_count": preferred_markdown_structure.get("image_count", 0),
        "preferred_markdown_block_count": preferred_markdown_structure.get("block_count", 0),
        "preferred_views": _compact_preferred_views(preferred_views),
        "text_chars": quality.get("text_chars", len(result.extracted_text or "")),
        "markdown_heading_count": markdown_index["heading_count"],
        "markdown_image_count": markdown_index["image_count"],
        "table_count": table_count,
        "browser_visible_heading_count": browser_visible_structure.get("heading_count", 0),
        "browser_visible_image_count": browser_visible_structure.get("image_count", 0),
        "browser_visible_block_count": browser_visible_structure.get("block_count", 0),
        "browser_visible_leaf_text_block_count": browser_visible_metrics.get("leaf_text_block_count", 0),
        "browser_scroll_steps_run": scroll_metrics.get("steps_run", 0),
        "browser_scroll_final_height": scroll_metrics.get("final_height", 0),
        "archive_path": archive.get("archive_path", ""),
        "structured_path": archive.get("structured_path", ""),
        "errors": result.errors,
    }


LOW_QUALITY_STATUSES = {"empty", "too_short", "suspect_shell", "suspect_incomplete"}


def is_low_quality_item(item: dict[str, Any]) -> bool:
    return (
        item.get("quality_status") in LOW_QUALITY_STATUSES
        or item.get("completeness_status") == "suspect_incomplete"
        or item.get("content_completeness_status") in {"suspect_incomplete", "needs_review"}
        or item.get("asset_completeness_status") in {"suspect_incomplete", "unverified_assets"}
        or item.get("feishu_comparison_status") == "needs_review"
    )


def batch_id(urls: list[str]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha1("\n".join(urls).encode("utf-8")).hexdigest()[:10]
    return f"batch-{stamp}-{digest}"


def build_batch_review(data: dict[str, Any], report_paths: dict[str, str] | None = None) -> dict[str, Any]:
    paths = report_paths or {}
    review_items = []
    for item in data.get("items", []):
        review_items.append(
            {
                "index": item.get("index", 0),
                "requested_url": item.get("requested_url", ""),
                "final_url": item.get("final_url", ""),
                "title": item.get("title", ""),
                "status": item.get("status", ""),
                "transport": item.get("transport", ""),
                "strategy": item.get("strategy", ""),
                "route": item.get("route", ""),
                "extraction_engine": item.get("extraction_engine", ""),
                "quality_status": item.get("quality_status", ""),
                "content_completeness_status": item.get("content_completeness_status", ""),
                "content_scores": {
                    "text": item.get("content_text_score", 0),
                    "image": item.get("content_image_score", 0),
                    "table": item.get("content_table_score", 0),
                    "structure": item.get("content_structure_score", 0),
                    "attachment": item.get("content_attachment_score", 0),
                },
                "text_chars": item.get("text_chars", 0),
                "image_count": item.get("preferred_markdown_image_count") or item.get("markdown_image_count", 0),
                "table_count": item.get("table_count", 0),
                "heading_count": item.get("preferred_markdown_heading_count") or item.get("markdown_heading_count", 0),
                "recommended_next_action": item.get("recommended_next_action") or item.get("review_action", ""),
                "recommended_path": item.get("recommended_path") or item.get("review_path", ""),
                "preferred_views": item.get("preferred_views") or {},
                "archive_path": item.get("archive_path", ""),
                "structured_path": item.get("structured_path", ""),
                "errors": item.get("errors", []),
                "needs_rerun": item.get("status") != "ok" or is_low_quality_item(item),
            }
        )
    return {
        "version": 1,
        "status": data.get("status", ""),
        "total": int(data.get("total") or 0),
        "ok": int(data.get("ok") or 0),
        "failed": int(data.get("failed") or 0),
        "low_quality": int(data.get("low_quality") or 0),
        "paths": paths,
        "items": review_items,
        "rerun_urls": [str(item.get("requested_url", "")) for item in review_items if item.get("needs_rerun") and item.get("requested_url")] + list(data.get("pending_urls") or []),
    }


def read_batch_review(batch_path: str | Path) -> dict[str, Any]:
    path = Path(batch_path)
    if path.is_dir():
        review_path = path / "batch_review.json"
        if review_path.exists():
            return json.loads(review_path.read_text(encoding="utf-8"))
        summary_path = path / "summary.json"
    else:
        review_path = path
        if review_path.name == "batch_review.json":
            return json.loads(review_path.read_text(encoding="utf-8"))
        summary_path = path
    data = read_batch_summary(summary_path)
    report_paths = data.get("batch_report") or {}
    return build_batch_review(data, report_paths)


def render_review_markdown(data: dict[str, Any], report_paths: dict[str, str]) -> str:
    lines = [
        "# Batch Review Checklist",
        "",
        f"- Status: {data.get('status', '')}",
        f"- Total: {int(data.get('total') or 0)}",
        f"- OK: {int(data.get('ok') or 0)}",
        f"- Failed: {int(data.get('failed') or 0)}",
        f"- Low quality: {int(data.get('low_quality') or 0)}",
        "",
        "## Files",
        "",
        f"- Summary: `{report_paths.get('summary_path', '')}`",
        f"- Batch review JSON: `{report_paths.get('batch_review_json_path', '')}`",
        f"- Items JSONL: `{report_paths.get('items_jsonl_path', '')}`",
        f"- Low quality JSONL: `{report_paths.get('low_quality_jsonl_path', '')}`",
        f"- Failed JSONL: `{report_paths.get('failed_jsonl_path', '')}`",
        f"- Rerun URLs: `{report_paths.get('rerun_urls_path', '')}`",
        "",
        "## Checklist",
        "",
        "- [ ] Open every failed item and check `errors`.",
        "- [ ] Open every low-quality item and inspect `structured_path` / `archive_path`.",
        "- [ ] Retry `rerun-urls.txt` with browser profile or a site-specific backend when needed.",
        "- [ ] Mark items as accepted only when `quality_status=ok` and the extracted text matches the page.",
        "",
        "## Rerun",
        "",
        "```powershell",
        f"python -m web_content_fetcher.cli fetch-batch --urls-file \"{report_paths.get('rerun_urls_path', '')}\" --archive-dir \"{Path(report_paths.get('batch_path', '')).parent.parent if report_paths.get('batch_path') else ''}\"",
        "```",
        "",
        "## Items Needing Review",
        "",
    ]
    review_items = [item for item in data.get("items", []) if item.get("status") != "ok" or is_low_quality_item(item)]
    if not review_items:
        lines.append("No failed or low-quality items.")
    for item in review_items:
        lines.extend(
            [
                f"### {item.get('index', '')}. {item.get('requested_url', '')}",
                "",
                f"- Status: {item.get('status', '')}",
                f"- Quality: {item.get('quality_status', '')}",
                f"- Content completeness: {item.get('content_completeness_status', '')} text={item.get('content_text_score', 0)} image={item.get('content_image_score', 0)} table={item.get('content_table_score', 0)} structure={item.get('content_structure_score', 0)}",
                f"- Completeness: {item.get('completeness_status', '')} chars={item.get('completeness_char_ratio', '')} blocks={item.get('completeness_block_ratio', '')}",
                f"- Asset completeness: {item.get('asset_completeness_status', '')} downloaded={item.get('downloaded_asset_count', 0)} downloaded_only={item.get('downloaded_only_image_count', 0)} visible_only={item.get('browser_visible_only_image_count', 0)} failed={item.get('failed_image_asset_count', 0)}",
                f"- Transport: {item.get('transport', '')}",
                f"- Extraction: {item.get('extraction_engine', '')}",
                f"- Text chars: {item.get('text_chars', 0)}",
                f"- Markdown headings: {item.get('markdown_heading_count', 0)}",
                f"- Markdown images: {item.get('markdown_image_count', 0)}",
                f"- Browser visible headings/images/blocks: {item.get('browser_visible_heading_count', 0)} / {item.get('browser_visible_image_count', 0)} / {item.get('browser_visible_block_count', 0)}",
                f"- Preferred markdown chars/headings/images/blocks: {item.get('preferred_markdown_chars', 0)} / {item.get('preferred_markdown_heading_count', 0)} / {item.get('preferred_markdown_image_count', 0)} / {item.get('preferred_markdown_block_count', 0)}",
                f"- Browser visible leaf/scroll/height: {item.get('browser_visible_leaf_text_block_count', 0)} / {item.get('browser_scroll_steps_run', 0)} / {item.get('browser_scroll_final_height', 0)}",
                f"- Missing headings/images: {item.get('missing_heading_count', 0)} / {item.get('missing_image_count', 0)}",
                f"- Recommended action: {item.get('recommended_next_action') or item.get('review_action', '')} `{item.get('recommended_path') or item.get('review_path', '')}`",
                f"- Feishu comparison: {item.get('feishu_comparison_status', '')} `{'; '.join(str(reason) for reason in item.get('feishu_comparison_reasons', []))}`",
                f"- Feishu DOCX export: `{item.get('feishu_comparison_docx_path', '')}`",
                f"- Preferred markdown: `{item.get('preferred_markdown_path', '')}`",
                f"- Completeness reasons: `{'; '.join(str(reason) for reason in item.get('completeness_reasons', []))}`",
                f"- Asset reasons: `{'; '.join(str(reason) for reason in item.get('asset_completeness_reasons', []))}`",
                f"- Archive: `{item.get('archive_path', '')}`",
                f"- Structured: `{item.get('structured_path', '')}`",
                f"- Errors: `{'; '.join(str(error) for error in item.get('errors', []))}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_batch_report(data: dict[str, Any], archive_dir: str, batch_name: str = "", *, report_root: Path | None = None) -> dict[str, str]:
    if not archive_dir:
        return {}
    base = Path(archive_dir) / "batches" / (batch_name or batch_id([str(item.get("requested_url", "")) for item in data.get("items", [])]))
    root = report_root or base
    suffix = 2
    while report_root is None and root.exists():
        root = Path(f"{base}-{suffix}")
        suffix += 1
    root.mkdir(parents=True, exist_ok=report_root is not None)
    summary_path = root / "summary.json"
    batch_review_path = root / "batch_review.json"
    items_path = root / "items.jsonl"
    low_quality_path = root / "low-quality.jsonl"
    failed_path = root / "failed.jsonl"
    rerun_urls_path = root / "rerun-urls.txt"
    review_path = root / "review.md"
    low_quality_items = [item for item in data.get("items", []) if is_low_quality_item(item)]
    failed_items = [item for item in data.get("items", []) if item.get("status") != "ok"]
    rerun_items = [*failed_items, *[item for item in low_quality_items if item not in failed_items]]
    paths = {
        "batch_path": str(root),
        "summary_path": str(summary_path),
        "batch_review_json_path": str(batch_review_path),
        "items_jsonl_path": str(items_path),
        "low_quality_jsonl_path": str(low_quality_path),
        "failed_jsonl_path": str(failed_path),
        "rerun_urls_path": str(rerun_urls_path),
        "review_markdown_path": str(review_path),
    }
    summary_data = {
        **data,
        "low_quality_items": low_quality_items,
        "failed_items": failed_items,
        "batch_report": paths,
    }
    batch_review = build_batch_review(summary_data, paths)
    atomic_write_json(summary_path, summary_data)
    atomic_write_json(batch_review_path, batch_review)
    with items_path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in data.get("items", []):
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    with low_quality_path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in low_quality_items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    with failed_path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in failed_items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    rerun_urls_path.write_text(
        "\n".join([str(item.get("requested_url", "")) for item in rerun_items if item.get("requested_url")] + list(data.get("pending_urls") or [])) + ("\n" if rerun_items or data.get("pending_urls") else ""),
        encoding="utf-8",
        newline="\n",
    )
    review_path.write_text(render_review_markdown(summary_data, paths), encoding="utf-8", newline="\n")
    if data.get("results"):
        results_path = root / "results.json"
        results_path.write_text(json.dumps(data.get("results"), ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        paths["results_path"] = str(results_path)
        summary_data["batch_report"] = paths
        batch_review = build_batch_review(summary_data, paths)
        atomic_write_json(summary_path, summary_data)
        atomic_write_json(batch_review_path, batch_review)
        review_path.write_text(render_review_markdown(summary_data, paths), encoding="utf-8", newline="\n")
    return paths


def _request_identity(request: BatchFetchRequest) -> str:
    identity = asdict(request)
    for key in ("resume_from", "max_attempts", "max_batch_seconds", "same_error_limit"):
        identity.pop(key)
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _checkpoint_hash(data: dict[str, Any]) -> str:
    body = {key: value for key, value in data.items() if key != "checkpoint_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _archive_proof(result: FetchResult) -> list[dict[str, str]]:
    archive = result.archive or {}
    proof = []
    for key in ("structured_path", "raw_snapshot_path", "extracted_text_path"):
        path = archive.get(key)
        if not path or not Path(path).is_file():
            return []
        proof.append({"path": str(path), "sha256": sha256_file(path)})
    # The existing archive index owns original-resource and native-order paths.
    seen = {entry["path"] for entry in proof}
    for entry in build_archive_index(result).get("files", []):
        path = str(entry.get("path") or "")
        if path and path not in seen:
            if not Path(path).is_file():
                return []
            seen.add(path)
            proof.append({"path": path, "sha256": sha256_file(path)})
    return proof


def _failure_cause(item: dict[str, Any]) -> str:
    if item.get("content_ok"):
        return ""
    status = item.get("http_status")
    if status:
        if status == 429:
            return "http_429"
        if status >= 500:
            return "http_5xx"
    if item.get("transport_ok"):
        return "content:" + str(item.get("quality_status") or "invalid")
    error = str((item.get("errors") or ["unknown"])[0])
    # Stable class, without per-URL or per-attempt message fragments.
    return "transport:" + error.split(":", 1)[0]


def fetch_batch(request: BatchFetchRequest) -> dict[str, Any]:
    if request.max_attempts < 1 or request.max_batch_seconds <= 0 or request.same_error_limit < 1:
        raise ValueError("batch_budget_must_be_positive")
    identity = _request_identity(request)
    checkpoint_path = None
    checkpoint = {"version": 1, "request_sha256": identity, "items": [], "results": {},
                  "attempts": 0, "elapsed_seconds": 0.0, "failure_cause": "", "consecutive_failures": 0}
    if request.resume_from:
        checkpoint_path = Path(request.resume_from)
        if checkpoint_path.is_dir():
            checkpoint_path = checkpoint_path / "checkpoint.json"
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint.get("checkpoint_sha256") != _checkpoint_hash(checkpoint):
            raise ValueError("batch_checkpoint_hash_mismatch")
        if checkpoint.get("version") != 1 or checkpoint.get("request_sha256") != identity:
            raise ValueError("batch_request_identity_mismatch")
        seen_indexes = set()
        for item in checkpoint["items"]:
            index = item["index"]
            if index in seen_indexes or not 1 <= index <= len(request.urls) or item["requested_url"] != request.urls[index - 1]:
                raise ValueError("batch_checkpoint_item_identity_mismatch")
            seen_indexes.add(index)
            if item.get("content_ok"):
                proof = item.get("archive_proof", [])
                if len(proof) < 3 or any(not Path(entry["path"]).is_file() or sha256_file(entry["path"]) != entry["sha256"] for entry in proof):
                    raise ValueError("batch_resume_archive_missing_or_drifted")
    elif request.save_report and request.archive_dir:
        report = write_batch_report({"status": "running", "total": len(request.urls), "items": []}, request.archive_dir)
        checkpoint_path = Path(report["batch_path"]) / "checkpoint.json"

    items = checkpoint["items"]
    full_by_index = checkpoint["results"]
    started = time.monotonic()
    previous_elapsed = checkpoint["elapsed_seconds"]
    stop_reason = ""

    def persist() -> None:
        checkpoint["elapsed_seconds"] = previous_elapsed + time.monotonic() - started
        checkpoint["items"] = items
        checkpoint["results"] = full_by_index
        checkpoint["checkpoint_sha256"] = _checkpoint_hash(checkpoint)
        if checkpoint_path:
            atomic_write_json(checkpoint_path, checkpoint)

    persist()
    for index, url in enumerate(request.urls, start=1):
        prior = next((item for item in items if item["index"] == index), None)
        if prior and prior.get("content_ok"):
            continue
        duplicate = next((item for item in items if item["requested_url"] == url and item.get("content_ok")), None)
        if duplicate and prior is None:
            items.append({**duplicate, "index": index, "reused_from_index": duplicate["index"]})
            if request.include_results:
                full_by_index[str(index)] = full_by_index[str(duplicate["index"])]
            persist()
            continue
        elapsed = previous_elapsed + time.monotonic() - started
        if checkpoint["attempts"] >= request.max_attempts:
            stop_reason = "attempt_budget_exhausted"
        elif request.max_batch_seconds - elapsed < 1:
            stop_reason = "time_budget_exhausted"
        elif checkpoint["consecutive_failures"] >= request.same_error_limit:
            stop_reason = "same_cause_circuit_open"
        if stop_reason:
            break
        timeout = min(request.timeout_seconds, max(1, int(request.max_batch_seconds - elapsed)))
        checkpoint["attempts"] += 1
        checkpoint["active_index"] = index
        persist()  # Reserve the attempt before fetching, including interrupted attempts.
        if prior:
            items.remove(prior)
        full_by_index.pop(str(index), None)
        try:
            if request.as_documents:
                result = fetch_document_link(
                    url,
                    archive_dir=request.archive_dir,
                    timeout_seconds=timeout,
                    browser_profile=request.browser_profile,
                    browser_user_data_dir=request.browser_user_data_dir,
                    browser_executable_path=request.browser_executable_path,
                    browser_storage_state=request.browser_storage_state,
                    headless=request.headless,
                    check_robots=request.check_robots,
                    extractor=request.extractor,
                    backend=request.backend,
                    compare_feishu_export=request.compare_feishu_export,
                    browser_dismiss_obstructive_overlays=request.browser_dismiss_obstructive_overlays,
                    wechat_api_base_url=request.wechat_api_base_url,
                )
            else:
                result = fetch_content(
                    FetchRequest(
                        url=url,
                        archive_dir=request.archive_dir,
                        timeout_seconds=timeout,
                        backend=request.backend,
                        extractor=request.extractor,
                        browser_profile=request.browser_profile,
                        browser_user_data_dir=request.browser_user_data_dir,
                        browser_executable_path=request.browser_executable_path,
                        browser_storage_state=request.browser_storage_state,
                        browser_headless=request.headless,
                        browser_dismiss_obstructive_overlays=request.browser_dismiss_obstructive_overlays,
                        check_robots=request.check_robots,
                        compare_feishu_export=request.compare_feishu_export,
                        wechat_api_base_url=request.wechat_api_base_url,
                    )
                )
            item = summarize_result(index, result)
            item["archive_proof"] = _archive_proof(result) if item.get("content_ok") else []
            items.append(item)
            if request.include_results:
                full_by_index[str(index)] = result.to_dict(include_raw=False)
        except Exception as exc:
            items.append(
                {
                    "index": index,
                    "requested_url": url,
                    "final_url": url,
                    "status": "failed",
                    "transport": "none",
                    "transport_ok": False,
                    "content_ok": False,
                    "title": "",
                    "extraction_engine": "",
                    "quality_status": "",
                    "text_chars": 0,
                    "markdown_heading_count": 0,
                    "markdown_image_count": 0,
                    "archive_path": "",
                    "structured_path": "",
                    "errors": [f"{type(exc).__name__}:{str(exc)[:200]}"],
                }
            )
        cause = _failure_cause(items[-1])
        checkpoint["consecutive_failures"] = (checkpoint["consecutive_failures"] + 1 if cause == checkpoint["failure_cause"] else 1) if cause else 0
        checkpoint["failure_cause"] = cause
        checkpoint["active_index"] = None
        persist()
        if checkpoint["elapsed_seconds"] >= request.max_batch_seconds:
            stop_reason = "time_budget_exhausted"
            break
    items.sort(key=lambda item: item["index"])
    ok_count = sum(1 for item in items if item.get("status") == "ok")
    low_quality_count = sum(1 for item in items if is_low_quality_item(item))
    failed_count = len(items) - ok_count
    data: dict[str, Any] = {
        "status": "ok" if failed_count == 0 else "partial" if ok_count else "failed",
        "total": len(items),
        "ok": ok_count,
        "failed": failed_count,
        "low_quality": low_quality_count,
        "items": items,
        "requested_total": len(request.urls),
        "pending": len(request.urls) - len(items),
        "pending_urls": [url for index, url in enumerate(request.urls, start=1) if not any(item["index"] == index for item in items)],
        "completed": len(items) == len(request.urls) and not stop_reason,
        "stop_reason": stop_reason,
        "request_sha256": identity,
        "attempts": checkpoint["attempts"],
        "checkpoint_path": str(checkpoint_path or ""),
    }
    if stop_reason:
        data["status"] = "partial" if items else "blocked"
    persist()
    data["elapsed_seconds"] = checkpoint["elapsed_seconds"]
    if request.include_results:
        data["results"] = [full_by_index[key] for key in sorted(full_by_index, key=int)]
    low_quality_items = [item for item in items if is_low_quality_item(item)]
    failed_items = [item for item in items if item.get("status") != "ok"]
    data["low_quality_items"] = low_quality_items
    data["failed_items"] = failed_items
    if request.save_report and request.archive_dir:
        try:
            data["batch_report"] = write_batch_report(data, request.archive_dir, report_root=checkpoint_path.parent if checkpoint_path else None)
        except Exception as exc:
            data["batch_report"] = {}
            data["report_error"] = f"{type(exc).__name__}:{str(exc)[:200]}"
    return data


def rerun_batch_report(
    *,
    batch_path: str = "",
    rerun_urls_path: str = "",
    archive_dir: str = "",
    timeout_seconds: int = 30,
    backend: str = "auto",
    extractor: str = "auto",
    as_documents: bool = True,
    browser_profile: str = "",
    browser_user_data_dir: str = "",
    browser_executable_path: str = "",
    browser_storage_state: str = "",
    headless: bool = False,
    check_robots: bool = True,
    compare_feishu_export: bool = False,
    browser_dismiss_obstructive_overlays: bool = True,
    include_results: bool = False,
) -> dict[str, Any]:
    path = resolve_rerun_urls_path(batch_path=batch_path, rerun_urls_path=rerun_urls_path)
    urls = read_url_list(str(path)) if path.exists() else []
    target_archive_dir = archive_dir or infer_archive_dir_from_rerun_path(path)
    if not urls:
        return {
            "status": "empty",
            "total": 0,
            "ok": 0,
            "failed": 0,
            "low_quality": 0,
            "items": [],
            "rerun": {
                "batch_path": str(batch_path or ""),
                "rerun_urls_path": str(path),
                "archive_dir": target_archive_dir,
                "reason": "rerun URL list is missing or empty",
            },
        }
    data = fetch_batch(
        BatchFetchRequest(
            urls=urls,
            archive_dir=target_archive_dir,
            timeout_seconds=timeout_seconds,
            backend=backend,
            extractor=extractor,
            as_documents=as_documents,
            browser_profile=browser_profile,
            browser_user_data_dir=browser_user_data_dir,
            browser_executable_path=browser_executable_path,
            browser_storage_state=browser_storage_state,
            headless=headless,
            check_robots=check_robots,
            compare_feishu_export=compare_feishu_export,
            browser_dismiss_obstructive_overlays=browser_dismiss_obstructive_overlays,
            include_results=include_results,
        )
    )
    data["rerun"] = {
        "batch_path": str(batch_path or ""),
        "rerun_urls_path": str(path),
        "archive_dir": target_archive_dir,
        "url_count": len(urls),
    }
    return data
