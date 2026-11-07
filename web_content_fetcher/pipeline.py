from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .archive import list_archives
from .archive_rebuilder import RebuildArchiveRequest, rebuild_archive
from .document_router import fetch_document_link


DEFAULT_VISUAL_COMMAND = os.getenv("WEB_CONTENT_FETCHER_VISUAL_COMMAND", "").strip()


@dataclass
class FetchAndRebuildRequest:
    url: str
    archive_dir: str = ""
    timeout_seconds: int = 30
    browser_profile: str = ""
    browser_user_data_dir: str = ""
    browser_executable_path: str = ""
    browser_storage_state: str = ""
    headless: bool = False
    check_robots: bool = True
    extractor: str = "auto"
    backend: str = "auto"
    crawl4ai_wait_for: str = ""
    crawl4ai_css_selector: str = ""
    crawl4ai_screenshot: bool = False
    crawl4ai_pdf: bool = False
    crawl4ai_cache_mode: str = "bypass"
    firecrawl_api_url: str = ""
    firecrawl_api_key_env: str = "FIRECRAWL_API_KEY"
    firecrawl_formats: list[str] = field(default_factory=lambda: ["markdown", "html", "links"])
    firecrawl_only_main_content: bool = True
    firecrawl_wait_for_ms: int = 0
    scrapling_mode: str = "static"
    scrapling_headless: bool = True
    scrapling_network_idle: bool = False
    scrapling_wait_ms: int = 0
    scrapling_wait_selector: str = ""
    scrapling_real_chrome: bool = False
    scrapling_user_data_dir: str = ""
    scrapling_python_path: str = ""
    scrapling_python_executable: str = ""
    compare_feishu_export: bool = False
    with_visual_check: bool = True
    visual_command: str = DEFAULT_VISUAL_COMMAND
    visual_timeout_seconds: int = 180


@dataclass
class RebuildExistingArchiveRequest:
    archive_path: str
    with_visual_check: bool = True
    visual_command: str = DEFAULT_VISUAL_COMMAND
    visual_timeout_seconds: int = 180


@dataclass
class RebuildArchiveBatchRequest:
    archive_dir: str
    limit: int = 20
    with_visual_check: bool = True
    visual_command: str = DEFAULT_VISUAL_COMMAND
    visual_timeout_seconds: int = 180
    continue_on_error: bool = True
    save_report: bool = True


def fetch_and_rebuild(request: FetchAndRebuildRequest) -> dict[str, Any]:
    result = fetch_document_link(
        request.url,
        archive_dir=request.archive_dir,
        timeout_seconds=request.timeout_seconds,
        browser_profile=request.browser_profile,
        browser_user_data_dir=request.browser_user_data_dir,
        browser_executable_path=request.browser_executable_path,
        browser_storage_state=request.browser_storage_state,
        headless=request.headless,
        check_robots=request.check_robots,
        extractor=request.extractor,
        backend=request.backend,
        crawl4ai_wait_for=request.crawl4ai_wait_for,
        crawl4ai_css_selector=request.crawl4ai_css_selector,
        crawl4ai_screenshot=request.crawl4ai_screenshot,
        crawl4ai_pdf=request.crawl4ai_pdf,
        crawl4ai_cache_mode=request.crawl4ai_cache_mode,
        firecrawl_api_url=request.firecrawl_api_url,
        firecrawl_api_key_env=request.firecrawl_api_key_env,
        firecrawl_formats=request.firecrawl_formats,
        firecrawl_only_main_content=request.firecrawl_only_main_content,
        firecrawl_wait_for_ms=request.firecrawl_wait_for_ms,
        scrapling_mode=request.scrapling_mode,
        scrapling_headless=request.scrapling_headless,
        scrapling_network_idle=request.scrapling_network_idle,
        scrapling_wait_ms=request.scrapling_wait_ms,
        scrapling_wait_selector=request.scrapling_wait_selector,
        scrapling_real_chrome=request.scrapling_real_chrome,
        scrapling_user_data_dir=request.scrapling_user_data_dir,
        scrapling_python_path=request.scrapling_python_path,
        scrapling_python_executable=request.scrapling_python_executable,
        compare_feishu_export=request.compare_feishu_export,
    )
    archive_path = str((result.archive or {}).get("archive_path") or "")
    if not archive_path:
        return {
            "status": "failed",
            "url": request.url,
            "fetch": result.to_dict(include_raw=False),
            "archive_path": "",
            "errors": [*result.errors, "fetch did not produce an archive_path"],
        }

    rebuilt = rebuild_existing_archive(
        RebuildExistingArchiveRequest(
            archive_path=archive_path,
            with_visual_check=request.with_visual_check,
            visual_command=request.visual_command,
            visual_timeout_seconds=request.visual_timeout_seconds,
        )
    )
    errors = list(result.errors)
    errors.extend(rebuilt.get("errors") or [])
    workflow_status = "ok" if result.status == "ok" and rebuilt.get("status") == "ok" else "failed"
    quality = rebuilt.get("quality") or {}
    status = _content_acceptance_status(workflow_status, quality)
    return {
        "schema_version": 1,
        "status": status,
        "workflow_status": workflow_status,
        "content_status": status,
        "url": request.url,
        "archive_path": archive_path,
        "fetch": {
            "status": result.status,
            "transport": result.transport,
            "title": result.title,
            "final_url": result.final_url,
            "extraction_engine": result.extraction_engine,
            "extraction_quality": result.extraction_quality,
            "text_chars": len(result.extracted_text or ""),
        },
        **{key: value for key, value in rebuilt.items() if key not in {"schema_version", "status", "archive_path", "errors"}},
        "errors": errors,
    }


def rebuild_archive_batch(request: RebuildArchiveBatchRequest) -> dict[str, Any]:
    items = list_archives(request.archive_dir, limit=request.limit)
    results: list[dict[str, Any]] = []
    failed = 0
    ok = 0
    for index, item in enumerate(items, start=1):
        archive_path = str(item.get("archive_path") or "")
        try:
            data = rebuild_existing_archive(
                RebuildExistingArchiveRequest(
                    archive_path=archive_path,
                    with_visual_check=request.with_visual_check,
                    visual_command=request.visual_command,
                    visual_timeout_seconds=request.visual_timeout_seconds,
                )
            )
            status = str(data.get("status") or "failed")
            if status == "ok":
                ok += 1
            else:
                failed += 1
            results.append(
                {
                    "index": index,
                    "archive_path": archive_path,
                    "title": item.get("title") or "",
                    "status": status,
                    "visual_status": (data.get("visual_check") or {}).get("status") or "",
                    "final_readable_html_path": data.get("final_readable_html_path") or "",
                    "final_agent_markdown_path": data.get("final_agent_markdown_path") or "",
                    "completeness_report_path": data.get("completeness_report_path") or "",
                    "errors": data.get("errors") or [],
                }
            )
            if status != "ok" and not request.continue_on_error:
                break
        except Exception as exc:
            failed += 1
            results.append(
                {
                    "index": index,
                    "archive_path": archive_path,
                    "title": item.get("title") or "",
                    "status": "failed",
                    "visual_status": "",
                    "final_readable_html_path": "",
                    "final_agent_markdown_path": "",
                    "completeness_report_path": "",
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }
            )
            if not request.continue_on_error:
                break
    total = len(results)
    status = "ok" if failed == 0 else "partial" if ok > 0 else "failed"
    data = {
        "schema_version": 1,
        "status": status,
        "archive_dir": request.archive_dir,
        "total": total,
        "ok": ok,
        "failed": failed,
        "limit": request.limit,
        "with_visual_check": request.with_visual_check,
        "items": results,
    }
    if request.save_report and request.archive_dir:
        data["rebuild_batch_report"] = write_rebuild_batch_report(data, request.archive_dir)
    else:
        data["rebuild_batch_report"] = {}
    return data


def rebuild_batch_id() -> str:
    return "rebuild-batch-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def render_rebuild_review_markdown(data: dict[str, Any], report_paths: dict[str, str]) -> str:
    lines = [
        "# Archive Rebuild Batch Review",
        "",
        f"- Status: {data.get('status', '')}",
        f"- Archive dir: `{data.get('archive_dir', '')}`",
        f"- Total: {int(data.get('total') or 0)}",
        f"- OK: {int(data.get('ok') or 0)}",
        f"- Failed: {int(data.get('failed') or 0)}",
        f"- With visual check: {bool(data.get('with_visual_check'))}",
        "",
        "## Files",
        "",
        f"- Summary: `{report_paths.get('summary_path', '')}`",
        f"- Items JSONL: `{report_paths.get('items_jsonl_path', '')}`",
        f"- Failed JSONL: `{report_paths.get('failed_jsonl_path', '')}`",
        f"- Failed archives: `{report_paths.get('failed_archives_path', '')}`",
        "",
        "## Checklist",
        "",
        "- [ ] Open each failed archive and inspect errors.",
        "- [ ] Open each final readable file and compare against screenshot or visual output when content matters.",
        "- [ ] Re-run failed archives individually after fixing local browser/OCR/tooling state.",
        "",
        "## Items",
        "",
    ]
    for item in data.get("items", []):
        lines.extend(
            [
                f"### {item.get('index', '')}. {item.get('title') or item.get('archive_path') or ''}",
                "",
                f"- Status: {item.get('status', '')}",
                f"- Visual: {item.get('visual_status', '')}",
                f"- Archive: `{item.get('archive_path', '')}`",
                f"- Final readable: `{item.get('final_readable_html_path', '')}`",
                f"- Final agent: `{item.get('final_agent_markdown_path', '')}`",
                f"- Completeness report: `{item.get('completeness_report_path', '')}`",
                f"- Errors: `{'; '.join(str(error) for error in item.get('errors', []))}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_rebuild_batch_report(data: dict[str, Any], archive_dir: str, batch_name: str = "") -> dict[str, str]:
    root = Path(archive_dir) / "rebuild-batches" / (batch_name or rebuild_batch_id())
    root.mkdir(parents=True, exist_ok=True)
    summary_path = root / "summary.json"
    items_path = root / "items.jsonl"
    failed_path = root / "failed.jsonl"
    failed_archives_path = root / "failed-archives.txt"
    review_path = root / "review.md"
    paths = {
        "batch_path": str(root),
        "summary_path": str(summary_path),
        "items_jsonl_path": str(items_path),
        "failed_jsonl_path": str(failed_path),
        "failed_archives_path": str(failed_archives_path),
        "review_markdown_path": str(review_path),
    }
    summary_data = {key: value for key, value in data.items() if key != "rebuild_batch_report"}
    summary_data["rebuild_batch_report"] = paths
    summary_path.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding="utf-8")
    with items_path.open("w", encoding="utf-8") as items_file, failed_path.open("w", encoding="utf-8") as failed_file:
        for item in data.get("items", []):
            line = json.dumps(item, ensure_ascii=False)
            items_file.write(line + "\n")
            if item.get("status") != "ok":
                failed_file.write(line + "\n")
    failed_archives = [str(item.get("archive_path") or "") for item in data.get("items", []) if item.get("status") != "ok" and item.get("archive_path")]
    failed_archives_path.write_text("\n".join(failed_archives) + ("\n" if failed_archives else ""), encoding="utf-8")
    review_path.write_text(render_rebuild_review_markdown(summary_data, paths), encoding="utf-8")
    return paths


def rebuild_existing_archive(request: RebuildExistingArchiveRequest) -> dict[str, Any]:
    archive_path = str(request.archive_path or "")
    if not archive_path:
        return {
            "schema_version": 1,
            "status": "failed",
            "archive_path": "",
            "errors": ["archive_path is required"],
        }

    first_rebuild = rebuild_archive(RebuildArchiveRequest(archive_path=archive_path, with_visual_check=request.with_visual_check))
    visual_result = _run_visual_check(archive_path, request) if request.with_visual_check else {"status": "skipped", "reason": "with_visual_check=false"}
    final_rebuild = rebuild_archive(RebuildArchiveRequest(archive_path=archive_path, with_visual_check=request.with_visual_check))
    errors: list[str] = []
    if visual_result.get("status") == "failed":
        errors.append(str(visual_result.get("error") or "visual check failed"))
    workflow_status = "ok" if visual_result.get("status") != "failed" else "failed"
    quality = final_rebuild.get("quality") or {}
    status = _content_acceptance_status(workflow_status, quality)
    return {
        "schema_version": 1,
        "status": status,
        "workflow_status": workflow_status,
        "content_status": status,
        "archive_path": archive_path,
        "first_rebuild": first_rebuild,
        "visual_check": visual_result,
        "final_rebuild": final_rebuild,
        "final_readable_html_path": final_rebuild.get("final_readable_html_path") or "",
        "final_agent_markdown_path": final_rebuild.get("final_agent_markdown_path") or "",
        "final_structured_json_path": final_rebuild.get("final_structured_json_path") or "",
        "completeness_report_path": final_rebuild.get("completeness_report_path") or "",
        "quality": final_rebuild.get("quality") or {},
        "errors": errors,
    }


def _content_acceptance_status(workflow_status: str, quality: dict[str, Any]) -> str:
    if workflow_status != "ok":
        return "failed"
    quality_status = str((quality or {}).get("status") or "")
    if quality_status in {"empty", "failed"}:
        return "failed"
    if quality_status in {"needs_review", "suspect_incomplete", "suspect_shell"}:
        return "needs_review"
    return "ok"


def _run_visual_check(archive_path: str, request: FetchAndRebuildRequest | RebuildExistingArchiveRequest) -> dict[str, Any]:
    command = Path(request.visual_command)
    if not command.exists():
        return {
            "status": "skipped",
            "reason": "visual command not found",
            "command": str(command),
        }
    completed = subprocess.run(
        [str(command), archive_path, "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=request.visual_timeout_seconds,
    )
    data: dict[str, Any] = {}
    if completed.stdout.strip():
        try:
            import json

            loaded = json.loads(completed.stdout)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    if completed.returncode != 0:
        return {
            "status": "failed",
            "command": str(command),
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "error": completed.stderr.strip() or completed.stdout.strip() or f"visual command failed: {completed.returncode}",
        }
    visual_outputs = {
        "layout_ocr_path": data.get("layout_ocr_path") or "",
        "visual_blocks_path": data.get("visual_blocks_path") or "",
        "table_candidates_path": data.get("table_candidates_path") or "",
        "image_positions_path": data.get("image_positions_path") or "",
    }
    return {
        "status": data.get("status") or "ok",
        "command": str(command),
        "returncode": completed.returncode,
        "visual_outputs": visual_outputs,
        "data": data,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
