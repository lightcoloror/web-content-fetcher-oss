from __future__ import annotations

import argparse
import html
import json
import mimetypes
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .archive import list_archives, pack_archive
from .batch import BatchFetchRequest, fetch_batch, list_batch_reports, read_url_list
from .bridge_doctor import bridge_doctor
from .browser_fetch import COMMON_LOGIN_URLS, browser_profile_status, default_profile_dir
from .cli import markdown
from .config import load_config, web_ui_host, web_ui_port
from .core import FetchRequest, fetch_content
from .document_router import fetch_document_link
from .pipeline import FetchAndRebuildRequest, RebuildArchiveBatchRequest, RebuildExistingArchiveRequest, fetch_and_rebuild, rebuild_archive_batch, rebuild_existing_archive
from .quality import QualitySmokeRequest, list_quality_runs, run_quality_smoke
from .recovery import make_manual_recovery_pack


STYLE = """
body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f7f7f4;color:#202124}
main{max-width:1180px;margin:0 auto;padding:24px}
h1{font-size:24px;margin:0 0 18px}
form{display:grid;gap:12px;background:#fff;border:1px solid #ddd;padding:16px;border-radius:8px}
label{display:grid;gap:4px;font-size:13px;font-weight:600}
input[type=text],textarea,select{font:14px Consolas,monospace;padding:8px;border:1px solid #bbb;border-radius:4px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.checks{display:flex;gap:16px;flex-wrap:wrap}
button{width:max-content;padding:8px 16px;border:1px solid #222;background:#222;color:#fff;border-radius:4px}
button.secondary{background:#fff;color:#222}
section{margin-top:18px;background:#fff;border:1px solid #ddd;border-radius:8px;padding:16px}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.summary div{border:1px solid #e0e0dc;background:#fafafa;border-radius:6px;padding:10px}
.summary b{display:block;font-size:12px;color:#5f6368;margin-bottom:4px}
.files{width:100%;border-collapse:collapse;font-size:13px}
.files th,.files td{border-top:1px solid #eee;text-align:left;padding:6px;vertical-align:top}
.inline{display:inline}
pre{white-space:pre-wrap;word-break:break-word;font:13px Consolas,monospace}
.batch-results{width:100%;border-collapse:collapse;font-size:13px}
.batch-results th,.batch-results td{border-top:1px solid #eee;text-align:left;padding:6px;vertical-align:top}
.batch-results td:nth-child(1),.batch-results td:nth-child(2),.batch-results td:nth-child(4),.batch-results td:nth-child(5){white-space:nowrap}
@media(max-width:760px){.row{grid-template-columns:1fr}}
"""


def _is_archive_preview_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except Exception:
        return False
    if not resolved.is_file():
        return False
    archive_root = resolved.parent
    if archive_root.name == "image_assets":
        archive_root = archive_root.parent
    if not ((archive_root / "structured.json").exists() or (archive_root / "metadata.json").exists()):
        return False
    try:
        resolved.relative_to(archive_root)
    except ValueError:
        return False
    return True


def _preview_href(path: str) -> str:
    if not path:
        return ""
    return "/preview?path=" + urllib.parse.quote(path, safe="")


def _path_link(path: str, label: str = "") -> str:
    if not path:
        return ""
    text = html.escape(label or path)
    href = html.escape(_preview_href(path))
    return f'<a href="{href}" target="_blank" rel="noopener">{text}</a><br><code>{html.escape(path)}</code>'


def _block_context_label(block: dict) -> str:
    if not isinstance(block, dict) or not block:
        return ""
    block_type = str(block.get("type") or "")
    line = str(block.get("line") or "")
    text = str(block.get("text") or block.get("alt") or block.get("src") or "")
    prefix = f"{block_type} L{line}".strip()
    return f"{prefix}: {text}" if text else prefix


def _rewrite_readable_asset_links(content: str, readable_path: Path) -> str:
    archive_root = readable_path.parent

    def replace(match: re.Match[str]) -> str:
        attr = match.group(1)
        value = html.unescape(match.group(2))
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme or parsed.netloc or value.startswith("/") or value.startswith("#"):
            return match.group(0)
        target = (archive_root / urllib.parse.unquote(parsed.path)).resolve()
        if not _is_archive_preview_path(target):
            return match.group(0)
        rewritten = _preview_href(str(target))
        if parsed.query:
            rewritten += "&" + parsed.query
        return f'{attr}="{html.escape(rewritten, quote=True)}"'

    return re.sub(r'\b(src|href)="([^"]+)"', replace, content)


def _scrapling_form_kwargs(form: dict[str, str]) -> dict:
    return {
        "scrapling_mode": form.get("scrapling_mode", "static"),
        "scrapling_headless": form.get("scrapling_headful") != "on",
        "scrapling_network_idle": form.get("scrapling_network_idle") == "on",
        "scrapling_wait_ms": int(form.get("scrapling_wait_ms") or 0),
        "scrapling_wait_selector": form.get("scrapling_wait_selector", "").strip(),
        "scrapling_real_chrome": form.get("scrapling_real_chrome") == "on",
        "scrapling_user_data_dir": form.get("scrapling_user_data_dir", "").strip(),
        "scrapling_python_path": form.get("scrapling_python_path", "").strip(),
        "scrapling_python_executable": form.get("scrapling_python_executable", "").strip(),
    }


def run_fetch(form: dict[str, str]) -> tuple[str, str, dict]:
    url = form.get("url", "").strip()
    archive_dir = form.get("archive_dir", "").strip()
    if form.get("auto_route", "on") == "on":
        result = fetch_document_link(
            url,
            archive_dir=archive_dir,
            timeout_seconds=int(form.get("timeout") or 30),
            browser_profile=form.get("profile", "").strip(),
            browser_user_data_dir=form.get("chrome_user_data_dir", "").strip(),
            headless=form.get("headless") == "on",
            check_robots=form.get("check_robots") == "on",
            extractor=form.get("extractor", "auto"),
            backend=form.get("backend", "auto"),
            crawl4ai_wait_for=form.get("crawl4ai_wait_for", "").strip(),
            crawl4ai_css_selector=form.get("crawl4ai_css_selector", "").strip(),
            crawl4ai_screenshot=form.get("crawl4ai_screenshot") == "on",
            crawl4ai_pdf=form.get("crawl4ai_pdf") == "on",
            crawl4ai_cache_mode=form.get("crawl4ai_cache_mode", "bypass"),
            firecrawl_api_url=form.get("firecrawl_api_url", "").strip(),
            firecrawl_api_key_env=form.get("firecrawl_api_key_env", "FIRECRAWL_API_KEY").strip() or "FIRECRAWL_API_KEY",
            firecrawl_formats=[item.strip() for item in form.get("firecrawl_formats", "markdown,html,links").split(",") if item.strip()],
            firecrawl_only_main_content=form.get("firecrawl_include_full_page") != "on",
            firecrawl_wait_for_ms=int(form.get("firecrawl_wait_for_ms") or 0),
            **_scrapling_form_kwargs(form),
            compare_feishu_export=form.get("compare_feishu_export") == "on",
        )
    else:
        request = FetchRequest(
            url=url,
            archive_dir=archive_dir,
            timeout_seconds=int(form.get("timeout") or 30),
            extractor=form.get("extractor", "auto"),
            backend=form.get("backend", "auto"),
            use_browser=form.get("use_browser") == "on",
            browser_auto_scroll=form.get("auto_scroll") == "on",
            browser_profile=form.get("profile", "").strip(),
            browser_user_data_dir=form.get("chrome_user_data_dir", "").strip(),
            browser_headless=form.get("headless") == "on",
            save_screenshot=form.get("screenshot") == "on",
            download_images=form.get("download_images") == "on",
            save_singlefile=form.get("singlefile") == "on",
            check_robots=form.get("check_robots") == "on",
            crawl4ai_wait_for=form.get("crawl4ai_wait_for", "").strip(),
            crawl4ai_css_selector=form.get("crawl4ai_css_selector", "").strip(),
            crawl4ai_screenshot=form.get("crawl4ai_screenshot") == "on",
            crawl4ai_pdf=form.get("crawl4ai_pdf") == "on",
            crawl4ai_cache_mode=form.get("crawl4ai_cache_mode", "bypass"),
            firecrawl_api_url=form.get("firecrawl_api_url", "").strip(),
            firecrawl_api_key_env=form.get("firecrawl_api_key_env", "FIRECRAWL_API_KEY").strip() or "FIRECRAWL_API_KEY",
            firecrawl_formats=[item.strip() for item in form.get("firecrawl_formats", "markdown,html,links").split(",") if item.strip()],
            firecrawl_only_main_content=form.get("firecrawl_include_full_page") != "on",
            firecrawl_wait_for_ms=int(form.get("firecrawl_wait_for_ms") or 0),
            **_scrapling_form_kwargs(form),
            compare_feishu_export=form.get("compare_feishu_export") == "on",
            discover_endpoints_only=form.get("discover_endpoints_only") == "on",
        )
        result = fetch_content(request)
    data = result.to_dict(include_raw=False)
    return markdown(result), json.dumps(data, ensure_ascii=False, indent=2), data


def run_fetch_and_rebuild(form: dict[str, str]) -> tuple[str, str, dict]:
    data = fetch_and_rebuild(
        FetchAndRebuildRequest(
            url=form.get("url", "").strip(),
            archive_dir=form.get("archive_dir", "").strip(),
            timeout_seconds=int(form.get("timeout") or 30),
            browser_profile=form.get("profile", "").strip(),
            browser_user_data_dir=form.get("chrome_user_data_dir", "").strip(),
            headless=form.get("headless") == "on",
            check_robots=form.get("check_robots") == "on",
            extractor=form.get("extractor", "auto"),
            backend=form.get("backend", "auto"),
            crawl4ai_wait_for=form.get("crawl4ai_wait_for", "").strip(),
            crawl4ai_css_selector=form.get("crawl4ai_css_selector", "").strip(),
            crawl4ai_screenshot=form.get("crawl4ai_screenshot") == "on",
            crawl4ai_pdf=form.get("crawl4ai_pdf") == "on",
            crawl4ai_cache_mode=form.get("crawl4ai_cache_mode", "bypass"),
            firecrawl_api_url=form.get("firecrawl_api_url", "").strip(),
            firecrawl_api_key_env=form.get("firecrawl_api_key_env", "FIRECRAWL_API_KEY").strip() or "FIRECRAWL_API_KEY",
            firecrawl_formats=[item.strip() for item in form.get("firecrawl_formats", "markdown,html,links").split(",") if item.strip()],
            firecrawl_only_main_content=form.get("firecrawl_include_full_page") != "on",
            firecrawl_wait_for_ms=int(form.get("firecrawl_wait_for_ms") or 0),
            **_scrapling_form_kwargs(form),
            compare_feishu_export=form.get("compare_feishu_export") == "on",
            with_visual_check=form.get("with_visual_check", "on") == "on",
        )
    )
    summary = "\n".join(
        [
            f"Status: {data.get('status', '')}",
            f"Archive: {data.get('archive_path', '')}",
            f"Final readable: {data.get('final_readable_html_path', '')}",
            f"Final agent markdown: {data.get('final_agent_markdown_path', '')}",
            f"Completeness report: {data.get('completeness_report_path', '')}",
        ]
    )
    return summary, json.dumps(data, ensure_ascii=False, indent=2), data


def run_rebuild_existing_archive(form: dict[str, str]) -> tuple[str, str, dict]:
    data = rebuild_existing_archive(
        RebuildExistingArchiveRequest(
            archive_path=form.get("archive_path", "").strip(),
            with_visual_check=form.get("with_visual_check", "on") == "on",
        )
    )
    summary = "\n".join(
        [
            f"Status: {data.get('status', '')}",
            f"Archive: {data.get('archive_path', '')}",
            f"Final readable: {data.get('final_readable_html_path', '')}",
            f"Final agent markdown: {data.get('final_agent_markdown_path', '')}",
            f"Completeness report: {data.get('completeness_report_path', '')}",
        ]
    )
    return summary, json.dumps(data, ensure_ascii=False, indent=2), data


def run_rebuild_archive_batch(form: dict[str, str]) -> dict:
    return rebuild_archive_batch(
        RebuildArchiveBatchRequest(
            archive_dir=form.get("archive_dir", "").strip(),
            limit=int(form.get("limit") or 20),
            with_visual_check=form.get("with_visual_check", "on") == "on",
        )
    )


def run_batch(form: dict[str, str]) -> dict:
    urls = [line.strip() for line in (form.get("urls", "") or "").splitlines() if line.strip() and not line.strip().startswith("#")]
    return fetch_batch(
        BatchFetchRequest(
            urls=urls,
            archive_dir=form.get("archive_dir", "").strip(),
            timeout_seconds=int(form.get("timeout") or 30),
            backend=form.get("backend", "auto"),
            extractor=form.get("extractor", "auto"),
            as_documents=form.get("batch_auto_route", "on") == "on",
            browser_profile=form.get("profile", "").strip(),
            browser_user_data_dir=form.get("chrome_user_data_dir", "").strip(),
            headless=form.get("headless") == "on",
            check_robots=form.get("check_robots") == "on",
            compare_feishu_export=form.get("compare_feishu_export") == "on",
            include_results=form.get("include_results") == "on",
        )
    )


def run_batch_file(form: dict[str, str]) -> dict:
    return fetch_batch(
        BatchFetchRequest(
            urls=read_url_list(form.get("rerun_urls_path", "")),
            archive_dir=form.get("archive_dir", "").strip(),
            timeout_seconds=int(form.get("timeout") or 30),
            backend=form.get("backend", "auto"),
            extractor=form.get("extractor", "auto"),
            as_documents=form.get("batch_auto_route", "on") == "on",
            browser_profile=form.get("profile", "").strip(),
            browser_user_data_dir=form.get("chrome_user_data_dir", "").strip(),
            headless=form.get("headless") == "on",
            check_robots=form.get("check_robots") == "on",
            compare_feishu_export=form.get("compare_feishu_export") == "on",
            include_results=form.get("include_results") == "on",
        )
    )


def run_quality(form: dict[str, str]) -> dict:
    data = run_quality_smoke(
        QualitySmokeRequest(
            corpus_path=form.get("quality_corpus", "").strip(),
            output_root=form.get("quality_output_root", "").strip(),
            timeout_seconds=int(form.get("quality_timeout") or 90),
            backend=form.get("quality_backend", "auto"),
            limit=int(form.get("quality_limit") or 0),
            compare_feishu_export=form.get("quality_compare_feishu_export") == "on",
            headless=form.get("quality_headless") == "on",
        )
    )
    if form.get("quality_recovery_pack") == "on":
        data["manual_recovery"] = make_manual_recovery_pack(str(data.get("output_root", "")))
    return data


def run_recovery_pack(form: dict[str, str]) -> dict:
    return make_manual_recovery_pack(
        form.get("quality_run_path", "").strip(),
        output_dir=form.get("recovery_output_dir", "").strip(),
    )


def _batch_summary_html(data: dict) -> str:
    if not data:
        return ""
    if "archive_dir" in data and "batch_report" not in data:
        return ""
    report = data.get("batch_report") or {}
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('index', '')))}</td>"
        f"<td>{html.escape(str(item.get('status', '')))}</td>"
        f"<td>{html.escape(str(item.get('title') or item.get('requested_url') or ''))}</td>"
        f"<td>{html.escape(str(item.get('transport', '')))}</td>"
        f"<td>{html.escape(str(item.get('extraction_engine', '')))}</td>"
        f"<td>{html.escape(str(item.get('quality_status', '')))}</td>"
        f"<td>{html.escape(str(item.get('text_chars', '')))}</td>"
        f"<td>{html.escape(str(item.get('markdown_heading_count', '')))}</td>"
        f"<td>{html.escape(str(item.get('markdown_image_count', '')))}</td>"
        f"<td>{html.escape(str(item.get('feishu_comparison_status', '')))}</td>"
        f"<td><code>{html.escape(str(item.get('archive_path', '')))}</code></td>"
        f"<td>{html.escape('; '.join(str(error) for error in (item.get('errors') or [])))}</td>"
        "</tr>"
        for item in data.get("items", [])
    )
    return f"""
<section>
  <strong>Batch Summary</strong>
  <div class="summary">
    <div><b>Status</b>{html.escape(str(data.get('status', '')))}</div>
    <div><b>Total</b>{html.escape(str(data.get('total', '')))}</div>
    <div><b>OK</b>{html.escape(str(data.get('ok', '')))}</div>
    <div><b>Failed</b>{html.escape(str(data.get('failed', '')))}</div>
    <div><b>Low quality</b>{html.escape(str(data.get('low_quality', '')))}</div>
    <div><b>Report</b><code>{html.escape(str(report.get('batch_path', '')))}</code></div>
    <div><b>Rerun URLs</b><code>{html.escape(str(report.get('rerun_urls_path', '')))}</code></div>
  </div>
  <table class="batch-results">
    <thead><tr><th>#</th><th>Status</th><th>Title / URL</th><th>Transport</th><th>Engine</th><th>Quality</th><th>Chars</th><th>Headings</th><th>Images</th><th>Feishu</th><th>Archive</th><th>Errors</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>"""


def _rebuild_batch_summary_html(data: dict) -> str:
    if not data or "items" not in data or "archive_dir" not in data:
        return ""
    report = data.get("rebuild_batch_report") or {}
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('index', '')))}</td>"
        f"<td>{html.escape(str(item.get('status', '')))}</td>"
        f"<td>{html.escape(str(item.get('visual_status', '')))}</td>"
        f"<td>{html.escape(str(item.get('title') or item.get('archive_path') or ''))}</td>"
        f"<td>{_path_link(str(item.get('final_readable_html_path', '')), 'Open final')}</td>"
        f"<td>{_path_link(str(item.get('final_agent_markdown_path', '')), 'Open agent')}</td>"
        f"<td>{_path_link(str(item.get('completeness_report_path', '')), 'Open report')}</td>"
        f"<td>{html.escape('; '.join(str(error) for error in (item.get('errors') or [])))}</td>"
        "</tr>"
        for item in data.get("items", [])
    )
    return f"""
<section>
  <strong>Archive Rebuild Batch</strong>
  <div class="summary">
    <div><b>Status</b>{html.escape(str(data.get('status', '')))}</div>
    <div><b>Total</b>{html.escape(str(data.get('total', '')))}</div>
    <div><b>OK</b>{html.escape(str(data.get('ok', '')))}</div>
    <div><b>Failed</b>{html.escape(str(data.get('failed', '')))}</div>
    <div><b>Archive dir</b><code>{html.escape(str(data.get('archive_dir', '')))}</code></div>
    <div><b>Report</b><code>{html.escape(str(report.get('batch_path', '')))}</code></div>
    <div><b>Review</b><code>{html.escape(str(report.get('review_markdown_path', '')))}</code></div>
  </div>
  <table class="batch-results">
    <thead><tr><th>#</th><th>Status</th><th>Visual</th><th>Title / Archive</th><th>Readable</th><th>Agent</th><th>Report</th><th>Errors</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>"""


def _quality_summary_html(data: dict) -> str:
    if not data or "quality_report" not in data:
        return ""
    report = data.get("quality_report") or {}
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('id', '')))}</td>"
        f"<td>{html.escape(str(item.get('status', '')))}</td>"
        f"<td>{html.escape(str(item.get('strategy', '')))}</td>"
        f"<td>{html.escape(str(item.get('route', '')))}</td>"
        f"<td>{html.escape(str(item.get('text_chars', 0)))}</td>"
        f"<td>{html.escape(str(item.get('image_count', 0)))}</td>"
        f"<td>{html.escape(str(item.get('table_count', 0)))}</td>"
        f"<td>{'ok' if (item.get('quality_gate') or {}).get('passed') else 'failed'}</td>"
        f"<td>{html.escape(str((item.get('quality_gate') or {}).get('rerun_strategy', '')))}</td>"
        f"<td><code>{html.escape(str(item.get('archive_path', '')))}</code></td>"
        "</tr>"
        for item in data.get("items", [])
    )
    return f"""
<section>
  <strong>Quality Smoke Result</strong>
  <div class="summary">
    <div><b>Status</b>{html.escape(str(data.get('status', '')))}</div>
    <div><b>Total</b>{html.escape(str(data.get('total', 0)))}</div>
    <div><b>Failed</b>{html.escape(str(data.get('failed', 0)))}</div>
    <div><b>Quality failed</b>{html.escape(str(data.get('quality_failed', 0)))}</div>
    <div><b>Summary</b><code>{html.escape(str(report.get('smoke_summary_markdown_path', '')))}</code></div>
    <div><b>Rerun plan</b><code>{html.escape(str(report.get('rerun_plan_json_path', '')))}</code></div>
  </div>
  <table class="batch-results">
    <thead><tr><th>ID</th><th>Status</th><th>Strategy</th><th>Route</th><th>Text</th><th>Images</th><th>Tables</th><th>Gate</th><th>Rerun</th><th>Archive</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>"""


def _recovery_pack_summary_html(data: dict) -> str:
    if not data:
        return ""
    paths = data.get("paths") or {}
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('id', '')))}</td>"
        f"<td>{html.escape(str(item.get('strategy', '')))}</td>"
        f"<td>{html.escape(str(item.get('url', '')))}</td>"
        f"<td><code>{html.escape(str(item.get('archive_path', '')))}</code></td>"
        "</tr>"
        for item in data.get("items", [])
    )
    return f"""
<section>
  <strong>Manual Recovery Pack</strong>
  <div class="summary">
    <div><b>Status</b>{html.escape(str(data.get('status', '')))}</div>
    <div><b>Total</b>{html.escape(str(data.get('total', 0)))}</div>
    <div><b>Markdown</b><code>{html.escape(str(paths.get('manual_recovery_markdown_path', '')))}</code></div>
    <div><b>JSON</b><code>{html.escape(str(paths.get('manual_recovery_json_path', '')))}</code></div>
    <div><b>Rerun URLs</b><code>{html.escape(str(paths.get('rerun_urls_path', '')))}</code></div>
  </div>
  <p>{html.escape(str(data.get('safe_boundary_note', '')))}</p>
  <table class="batch-results">
    <thead><tr><th>ID</th><th>Strategy</th><th>URL</th><th>Archive</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>"""


def _quality_runs_html(active_archive: str) -> str:
    quality_root = str(Path(active_archive) / "quality-runs")
    runs = list_quality_runs(quality_root, limit=20)
    try:
        doctor = bridge_doctor()
    except Exception as exc:
        doctor = {"status": "error", "checks": [], "notes": [f"{type(exc).__name__}: {exc}"]}
    doctor_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('name', '')))}</td>"
        f"<td>{html.escape(str(item.get('status', '')))}</td>"
        f"<td>{html.escape(str(item.get('resolved_path') or item.get('origin') or item.get('path') or ''))}</td>"
        f"<td>{html.escape(str(item.get('install_hint', '')))}</td>"
        "</tr>"
        for item in doctor.get("checks", [])
    )
    run_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('run_id', '')))}</td>"
        f"<td>{html.escape(str(item.get('status', '')))}</td>"
        f"<td>{int(item.get('total') or 0)} total / {int(item.get('failed') or 0)} failed / {int(item.get('quality_failed') or 0)} quality failed</td>"
        f"<td><code>{html.escape(str(item.get('summary_markdown_path', '')))}</code></td>"
        f"<td><code>{html.escape(str(item.get('quality_failures_json_path', '')))}</code></td>"
        f"<td><code>{html.escape(str(item.get('rerun_plan_json_path', '')))}</code></td>"
        f"<td>{_path_link(str(item.get('manual_recovery_markdown_path', '')), 'Open recovery')}<br><code>{html.escape(str(item.get('manual_recovery_json_path', '')))}</code></td>"
        "<td>"
        "<form method=\"post\" class=\"inline\">"
        "<input type=\"hidden\" name=\"action\" value=\"quality_recovery_pack\">"
        f"<input type=\"hidden\" name=\"archive_dir\" value=\"{html.escape(active_archive)}\">"
        f"<input type=\"hidden\" name=\"quality_run_path\" value=\"{html.escape(str(item.get('run_path', '')))}\">"
        "<button class=\"secondary\" type=\"submit\">Recovery Pack</button>"
        "</form>"
        "</td>"
        "</tr>"
        for item in runs
    )
    run_table = (
        f"<table class=\"files\"><thead><tr><th>Run</th><th>Status</th><th>Counts</th><th>Summary</th><th>Failures</th><th>Rerun Plan</th><th>Recovery Pack</th><th>Recover</th></tr></thead><tbody>{run_rows}</tbody></table>"
        if run_rows
        else "<p>No quality runs found.</p>"
    )
    return f"""
<section>
  <strong>Quality Runs</strong>
  <form method="post">
    <input type="hidden" name="action" value="quality_smoke">
    <div class="row">
      <label>Output root <input name="quality_output_root" type="text" value="{html.escape(quality_root)}"></label>
      <label>Corpus <input name="quality_corpus" type="text" placeholder="tests/fixtures/real_links.json"></label>
    </div>
    <div class="row">
      <label>Limit <input name="quality_limit" type="text" value="0"></label>
      <label>Timeout seconds <input name="quality_timeout" type="text" value="90"></label>
    </div>
    <label>Backend <select name="quality_backend"><option value="auto">Auto</option><option value="browser">Browser</option><option value="crawl4ai">Crawl4AI</option><option value="firecrawl">Firecrawl</option><option value="scrapling">Scrapling</option><option value="feishu_export">Feishu Export</option></select></label>
    <div class="checks">
      <label><input name="quality_headless" type="checkbox"> Headless</label>
      <label><input name="quality_compare_feishu_export" type="checkbox"> Compare Feishu export</label>
      <label><input name="quality_recovery_pack" type="checkbox" checked> Write recovery pack</label>
    </div>
    <button class="secondary" type="submit">Run Quality Smoke</button>
  </form>
  <div class="summary">
    <div><b>Bridge doctor</b>{html.escape(str(doctor.get('status', '')))}</div>
    <div><b>Quality root</b><code>{html.escape(quality_root)}</code></div>
  </div>
  <table class="files"><thead><tr><th>Bridge</th><th>Status</th><th>Path</th><th>Hint</th></tr></thead><tbody>{doctor_rows}</tbody></table>
  {run_table}
</section>"""


def _archive_list_html(archive_dir: str, pack_result: dict | None = None) -> str:
    items = list_archives(archive_dir, limit=20) if archive_dir else []
    batches = list_batch_reports(archive_dir, limit=20) if archive_dir else []
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('created_at', '')))}</td>"
        f"<td>{html.escape(str(item.get('status', '')))}</td>"
        f"<td>{html.escape(str(item.get('transport', '')))}</td>"
        f"<td>{html.escape(str(item.get('title') or item.get('url') or ''))}</td>"
        f"<td>{int(item.get('file_count') or 0)} files / {int(item.get('image_asset_count') or 0)} images / {int(item.get('attachment_count') or 0)} attachments</td>"
        f"<td>{_path_link(str(item.get('preferred_readable_path') or item.get('readable_html_path', '')), 'Open HTML')}<br>{_path_link(str(item.get('preferred_markdown_path', '')), 'Open MD')}</td>"
        f"<td><code>{html.escape(str(item.get('archive_path', '')))}</code></td>"
        "<td>"
        "<form method=\"post\" class=\"inline\">"
        f"<input type=\"hidden\" name=\"action\" value=\"pack_archive\">"
        f"<input type=\"hidden\" name=\"archive_dir\" value=\"{html.escape(archive_dir)}\">"
        f"<input type=\"hidden\" name=\"archive_path\" value=\"{html.escape(str(item.get('archive_path', '')))}\">"
        "<button class=\"secondary\" type=\"submit\">Pack</button>"
        "</form>"
        "<form method=\"post\" class=\"inline\">"
        f"<input type=\"hidden\" name=\"action\" value=\"rebuild_existing_archive\">"
        f"<input type=\"hidden\" name=\"archive_dir\" value=\"{html.escape(archive_dir)}\">"
        f"<input type=\"hidden\" name=\"archive_path\" value=\"{html.escape(str(item.get('archive_path', '')))}\">"
        f"<input type=\"hidden\" name=\"with_visual_check\" value=\"on\">"
        "<button class=\"secondary\" type=\"submit\">Rebuild</button>"
        "</form>"
        "</td>"
        "</tr>"
        for item in items
    )
    table = (
        f"<table class=\"files\"><thead><tr><th>Created</th><th>Status</th><th>Transport</th><th>Title</th><th>Assets</th><th>Preferred Readable</th><th>Archive</th><th>Action</th></tr></thead><tbody>{rows}</tbody></table>"
        if rows
        else "<p>No archives found.</p>"
    )
    packed = ""
    if pack_result:
        packed = (
            "<div class=\"summary\">"
            f"<div><b>ZIP</b><code>{html.escape(str(pack_result.get('zip_path', '')))}</code></div>"
            f"<div><b>Bytes</b>{html.escape(str(pack_result.get('bytes', '')))}</div>"
            f"<div><b>SHA256</b><code>{html.escape(str(pack_result.get('sha256', '')))}</code></div>"
            "</div>"
        )
    batch_rows = "\n".join(
        (lambda batch_filter: (
        f"<tr data-batch-filter=\"{html.escape(batch_filter)}\">"
        f"<td>{html.escape(str(item.get('created_at', '')))}</td>"
        f"<td>{html.escape(str(item.get('status', '')))}</td>"
        f"<td>{int(item.get('total') or 0)} total / {int(item.get('failed') or 0)} failed / {int(item.get('low_quality') or 0)} low quality / {int(item.get('needs_login') or 0)} needs login / {int(item.get('needs_export_or_api') or 0)} needs export/API</td>"
        f"<td><code>{html.escape(str(item.get('batch_path', '')))}</code></td>"
        f"<td><code>{html.escape(str(item.get('batch_review_json_path', '')))}</code></td>"
        f"<td><code>{html.escape(str(item.get('review_markdown_path', '')))}</code></td>"
        f"<td><code>{html.escape(str(item.get('rerun_urls_path', '')))}</code></td>"
        "<td>"
        "<form method=\"post\" class=\"inline\">"
        f"<input type=\"hidden\" name=\"action\" value=\"rerun_batch\">"
        f"<input type=\"hidden\" name=\"archive_dir\" value=\"{html.escape(archive_dir)}\">"
        f"<input type=\"hidden\" name=\"rerun_urls_path\" value=\"{html.escape(str(item.get('rerun_urls_path', '')))}\">"
        "<button class=\"secondary\" type=\"submit\">Rerun</button>"
        "</form>"
        "</td>"
        "</tr>"))(
            "failed"
            if int(item.get("failed") or 0) > 0
            else "needs-export"
            if int(item.get("needs_export_or_api") or 0) > 0
            else "needs-login"
            if int(item.get("needs_login") or 0) > 0
            else "suspect"
            if int(item.get("low_quality") or 0) > 0
            else "ok"
        )
        for item in batches
    )
    batch_table = (
        f"<label>Batch filter <select id=\"batch-filter\"><option value=\"all\">All</option><option value=\"ok\">OK</option><option value=\"suspect\">Suspect incomplete</option><option value=\"failed\">Failed</option><option value=\"needs-login\">Needs login</option><option value=\"needs-export\">Needs export/API</option></select></label>"
        f"<table class=\"files\"><thead><tr><th>Batch</th><th>Status</th><th>Result</th><th>Report</th><th>Review JSON</th><th>Review</th><th>Rerun URLs</th><th>Action</th></tr></thead><tbody>{batch_rows}</tbody></table>"
        "<script>document.getElementById('batch-filter')?.addEventListener('change',function(){const v=this.value;document.querySelectorAll('[data-batch-filter]').forEach(function(row){row.style.display=(v==='all'||row.dataset.batchFilter===v)?'':'none';});});</script>"
        if batch_rows
        else "<p>No batch reports found.</p>"
    )
    return f"""
<section>
  <strong>Archives</strong>
  <form method="post" class="inline">
    <input type="hidden" name="action" value="rebuild_archive_batch">
    <input type="hidden" name="archive_dir" value="{html.escape(archive_dir)}">
    <input type="hidden" name="limit" value="20">
    <input type="hidden" name="with_visual_check" value="on">
    <button class="secondary" type="submit">Rebuild Listed</button>
  </form>
  {packed}
  {table}
  <strong>Batch Reports</strong>
  {batch_table}
</section>"""


def _summary_html(data: dict) -> str:
    if not data:
        return ""
    if data.get("final_rebuild") or data.get("final_readable_html_path"):
        fetch = data.get("fetch") or {}
        visual = data.get("visual_check") or {}
        visual_outputs = visual.get("visual_outputs") or (visual.get("data") or {})
        quality = data.get("quality") or {}
        return f"""
<section>
  <strong>Fetch And Rebuild Summary</strong>
  <div class="summary">
    <div><b>Status</b>{html.escape(str(data.get('status', '')))}</div>
    <div><b>Archive</b><code>{html.escape(str(data.get('archive_path', '')))}</code></div>
    <div><b>Fetch</b>{html.escape(str(fetch.get('status', '')))} / {html.escape(str(fetch.get('transport', '')))} / {html.escape(str(fetch.get('extraction_engine', '')))}</div>
    <div><b>Text chars</b>{html.escape(str(fetch.get('text_chars', '')))}</div>
    <div><b>Quality</b>{html.escape(str(quality.get('status', '')))}</div>
    <div><b>SPA Shell</b>{'yes' if quality.get('spa_shell_detected') else 'no'}</div>
    <div><b>Login Redirect</b>{'yes' if quality.get('login_redirect_detected') else 'no'}</div>
    <div><b>Visual check</b>{html.escape(str(visual.get('status', '')))}</div>
    <div><b>Final readable</b>{_path_link(str(data.get('final_readable_html_path', '')), 'Open final readable')}</div>
    <div><b>Final agent</b>{_path_link(str(data.get('final_agent_markdown_path', '')), 'Open final markdown')}</div>
    <div><b>Final structured</b>{_path_link(str(data.get('final_structured_json_path', '')), 'Open final structured')}</div>
    <div><b>Completeness report</b>{_path_link(str(data.get('completeness_report_path', '')), 'Open completeness')}</div>
    <div><b>Visual OCR</b>{_path_link(str(visual_outputs.get('layout_ocr_path', '')), 'Open OCR markdown')}</div>
    <div><b>Visual blocks</b>{_path_link(str(visual_outputs.get('visual_blocks_path', '')), 'Open blocks JSON')}</div>
    <div><b>Table candidates</b>{_path_link(str(visual_outputs.get('table_candidates_path', '')), 'Open tables JSON')}</div>
    <div><b>Image positions</b>{_path_link(str(visual_outputs.get('image_positions_path', '')), 'Open images JSON')}</div>
  </div>
  {f"<p><strong>Recommendation:</strong> {html.escape(str(quality.get('recommendation', '')))}</p>" if quality.get('recommendation') else ""}
  {f"<p><strong>Errors:</strong> {html.escape('; '.join(str(item) for item in data.get('errors', [])))}</p>" if data.get('errors') else ""}
</section>"""
    archive = data.get("archive") or {}
    site_data = data.get("site_data") or {}
    quality = data.get("extraction_quality") or {}
    route = site_data.get("document_route") or {}
    archive_index = data.get("archive_index") or {}
    if not archive_index and archive.get("structured_path"):
        try:
            structured = json.loads(Path(str(archive["structured_path"])).read_text(encoding="utf-8"))
            archive_index = structured.get("archive_index") or {}
        except Exception:
            archive_index = {}
    files = archive_index.get("files") or []
    file_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('role', '')))}</td>"
        f"<td>{html.escape(str(item.get('media_type', '')))}</td>"
        f"<td><code>{html.escape(str(item.get('path', '')))}</code></td>"
        "</tr>"
        for item in files
    )
    files_html = f"""
<table class="files"><thead><tr><th>Role</th><th>Type</th><th>Path</th></tr></thead><tbody>{file_rows}</tbody></table>
""" if file_rows else ""
    preferred_markdown = archive_index.get("preferred_markdown") or {}
    outline = preferred_markdown.get("outline") or archive_index.get("markdown_outline") or []
    markdown_images = preferred_markdown.get("images") or archive_index.get("markdown_images") or []
    markdown_blocks = preferred_markdown.get("blocks") or archive_index.get("markdown_blocks") or []
    markdown_view_label = "Preferred Markdown" if preferred_markdown.get("blocks") or preferred_markdown.get("outline") or preferred_markdown.get("images") else "Markdown"
    browser_visible = archive_index.get("browser_visible") or {}
    browser_visible_structure = browser_visible.get("markdown_structure") or {}
    browser_visible_metrics = browser_visible.get("metrics") or {}
    scroll_metrics = browser_visible_metrics.get("scroll") or {}
    browser_visible_outline = browser_visible.get("dom_outline") or []
    browser_visible_path = str(browser_visible.get("path") or archive.get("browser_visible_path") or "")
    browser_visible_html_path = str(browser_visible.get("html_path") or archive.get("browser_visible_html_path") or "")
    completeness = archive_index.get("completeness_check") or {}
    asset_completeness = archive_index.get("asset_completeness_check") or {}
    feishu_comparison = archive_index.get("feishu_comparison") or {}
    missing_headings = completeness.get("missing_headings") or []
    missing_images = completeness.get("missing_images") or []
    preferred_readable = archive_index.get("preferred_readable") or {}
    preferred_markdown_structure = preferred_markdown.get("structure") or {}
    preferred_views = archive_index.get("preferred_views") or {}
    primary_views = preferred_views.get("primary") or {}
    primary_human = primary_views.get("human") or {}
    primary_agent = primary_views.get("agent") or {}
    primary_images = primary_views.get("images") or {}
    primary_attachments = primary_views.get("attachments") or {}
    preferred_readable_path = str(primary_human.get("path") or preferred_readable.get("html_path") or archive.get("readable_html_path") or browser_visible_html_path or "")
    preferred_markdown_path = str(primary_agent.get("path") or preferred_markdown.get("path") or archive.get("preferred_markdown_path") or preferred_readable.get("markdown_path") or "")
    review_recommendation = archive_index.get("review_recommendation") or {}
    outline_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('line', '')))}</td>"
        f"<td>H{html.escape(str(item.get('level', '')))}</td>"
        f"<td>{html.escape(str(item.get('text', '')))}</td>"
        "</tr>"
        for item in outline[:12]
    )
    browser_outline_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('order', '')))}</td>"
        f"<td>H{html.escape(str(item.get('level', '')))}</td>"
        f"<td>{html.escape(str(item.get('tag', '')))}</td>"
        f"<td>{html.escape(str(item.get('role', '')))}</td>"
        f"<td>{html.escape(str(item.get('text', '')))}</td>"
        "</tr>"
        for item in browser_visible_outline[:12]
    )
    images_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('index', '')))}</td>"
        f"<td>{html.escape(str(item.get('line', '')))}</td>"
        f"<td>{html.escape(str(item.get('alt', '')))}</td>"
        f"<td><code>{html.escape(str(item.get('src', '')))}</code></td>"
        f"<td>{html.escape(_block_context_label(item.get('previous_block') or {}))}</td>"
        f"<td>{html.escape(_block_context_label(item.get('next_block') or {}))}</td>"
        "</tr>"
        for item in markdown_images[:12]
    )
    missing_heading_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('line', '')))}</td>"
        f"<td>H{html.escape(str(item.get('level', '')))}</td>"
        f"<td>{html.escape(str(item.get('text', '')))}</td>"
        "</tr>"
        for item in missing_headings[:12]
    )
    missing_image_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('index', '')))}</td>"
        f"<td>{html.escape(str(item.get('line', '')))}</td>"
        f"<td>{html.escape(str(item.get('alt', '')))}</td>"
        f"<td><code>{html.escape(str(item.get('src', '')))}</code></td>"
        "</tr>"
        for item in missing_images[:12]
    )
    block_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('order', '')))}</td>"
        f"<td>{html.escape(str(item.get('type', '')))}</td>"
        f"<td>{html.escape(str(item.get('line', '')))}</td>"
        f"<td>{html.escape(str(item.get('level', '')))}</td>"
        f"<td>{html.escape(str(item.get('text') or item.get('alt') or ''))}</td>"
        f"<td><code>{html.escape(str(item.get('src', '')))}</code></td>"
        "</tr>"
        for item in markdown_blocks[:20]
    )
    outline_html = (
        f"<table class=\"files\"><thead><tr><th>Line</th><th>Level</th><th>Heading</th></tr></thead><tbody>{outline_rows}</tbody></table>"
        if outline_rows
        else ""
    )
    browser_outline_html = (
        f"<table class=\"files\"><thead><tr><th>Order</th><th>Level</th><th>Tag</th><th>Role</th><th>Heading</th></tr></thead><tbody>{browser_outline_rows}</tbody></table>"
        if browser_outline_rows
        else ""
    )
    images_html = (
        f"<table class=\"files\"><thead><tr><th>#</th><th>Line</th><th>Alt</th><th>Src</th><th>Prev</th><th>Next</th></tr></thead><tbody>{images_rows}</tbody></table>"
        if images_rows
        else ""
    )
    missing_html = ""
    if missing_heading_rows or missing_image_rows:
        missing_html = "".join(
            [
                "<strong>Missing From Extracted Markdown</strong>",
                f"<table class=\"files\"><thead><tr><th>Line</th><th>Level</th><th>Heading</th></tr></thead><tbody>{missing_heading_rows}</tbody></table>" if missing_heading_rows else "",
                f"<table class=\"files\"><thead><tr><th>#</th><th>Line</th><th>Alt</th><th>Src</th></tr></thead><tbody>{missing_image_rows}</tbody></table>" if missing_image_rows else "",
            ]
        )
    blocks_html = (
        f"<table class=\"files\"><thead><tr><th>#</th><th>Type</th><th>Line</th><th>Level</th><th>Text/Alt</th><th>Src</th></tr></thead><tbody>{block_rows}</tbody></table>"
        if block_rows
        else ""
    )
    return f"""
<section>
  <strong>Result Summary</strong>
  <div class="summary">
    <div><b>Status</b>{html.escape(str(data.get('status', '')))}</div>
    <div><b>Transport</b>{html.escape(str(data.get('transport', '')))}</div>
    <div><b>Extraction</b>{html.escape(str(data.get('extraction_engine', '')))}</div>
    <div><b>Text chars</b>{html.escape(str(quality.get('text_chars') or len(data.get('extracted_text') or '')))}</div>
    <div><b>Quality</b>{html.escape(str(quality.get('status', '')))}</div>
    <div><b>Route</b>{html.escape(str(route.get('kind') or site_data.get('site_profile') or ''))}</div>
    <div><b>Images</b>{len(data.get('image_assets') or [])} assets / {len(data.get('images') or [])} refs</div>
    <div><b>Archive</b><code>{html.escape(str(archive.get('archive_path', '')))}</code></div>
    <div><b>Preferred Readable</b>{_path_link(preferred_readable_path, 'Open preferred')} {html.escape(str(preferred_readable.get('role', '')))} {html.escape(str(preferred_readable.get('source', '')))}</div>
    <div><b>Preferred Markdown</b>{_path_link(preferred_markdown_path, 'Open markdown')} {html.escape(str(preferred_markdown.get('source', '')))} {html.escape(str(preferred_markdown.get('chars', 0)))} chars / {html.escape(str(preferred_markdown_structure.get('heading_count', 0)))} headings / {html.escape(str(preferred_markdown_structure.get('image_count', 0)))} images / {html.escape(str(preferred_markdown_structure.get('block_count', 0)))} blocks</div>
    <div><b>Preferred Views</b>{_path_link(str(primary_human.get('path', '')), 'Human')} {html.escape(str(primary_human.get('role', '')))}<br>{_path_link(str(primary_agent.get('path', '')), 'Agent')} {html.escape(str(primary_agent.get('role', '')))}<br>{_path_link(str(primary_images.get('path', '')), 'Images')} {html.escape(str(primary_images.get('role', '')))}<br>{_path_link(str(primary_attachments.get('path', '')), 'Attachment')} {html.escape(str(primary_attachments.get('label', '')))}</div>
    <div><b>Recommended Action</b>{html.escape(str(review_recommendation.get('action', '')))} {_path_link(str(review_recommendation.get('path', '')), 'Open')}</div>
    <div><b>Readable HTML</b>{_path_link(str(archive.get('readable_html_path', '')), 'Open readable')}</div>
    <div><b>Browser Visible</b>{_path_link(browser_visible_html_path or browser_visible_path, 'Open visible')} {html.escape(str(browser_visible.get('block_count', 0)))} DOM blocks / {html.escape(str(browser_visible_structure.get('block_count', 0)))} markdown blocks / {html.escape(str(browser_visible_structure.get('dom_heading_count') or browser_visible_structure.get('heading_count', 0)))} headings / {html.escape(str(browser_visible_structure.get('image_count', 0)))} images</div>
    <div><b>Visible Metrics</b>{html.escape(str(browser_visible_metrics.get('leaf_text_block_count', 0)))} leaf text blocks / scroll {html.escape(str(scroll_metrics.get('steps_run', 0)))} steps / height {html.escape(str(scroll_metrics.get('final_height', 0)))}</div>
    <div><b>Completeness</b>{html.escape(str(completeness.get('status', '')))} chars {html.escape(str(completeness.get('char_ratio', '')))} / blocks {html.escape(str(completeness.get('block_ratio', '')))} / headings {html.escape(str(completeness.get('heading_ratio', '')))} / images {html.escape(str(completeness.get('image_ratio', '')))}</div>
    <div><b>Asset Completeness</b>{html.escape(str(asset_completeness.get('status', '')))} downloaded {html.escape(str(asset_completeness.get('downloaded_asset_count', 0)))} / downloaded-only {html.escape(str(asset_completeness.get('downloaded_only_count', 0)))} / visible-only {html.escape(str(asset_completeness.get('browser_visible_only_count', 0)))} / failed {html.escape(str(asset_completeness.get('failed_count', 0)))}</div>
    <div><b>Feishu Comparison</b>{html.escape(str(feishu_comparison.get('status', '')))} browser {html.escape(str((feishu_comparison.get('browser') or {}).get('text_chars', '')))} chars / export {html.escape(str((feishu_comparison.get('export') or {}).get('text_chars', '')))} chars<br><code>{html.escape(str(feishu_comparison.get('docx_path', '')))}</code></div>
    <div><b>Structured</b><code>{html.escape(str(archive.get('structured_path', '')))}</code></div>
    <div><b>JSON API</b>{'yes' if ((archive_index.get('api_response') or {}).get('is_json')) else 'no'} {_path_link(str((archive_index.get('api_response') or {}).get('summary_markdown_path', '')), 'Open summary')}</div>
    <div><b>Endpoints</b>{len(archive_index.get('endpoint_candidates') or [])} candidates {_path_link(str(((site_data.get('endpoint_discovery') or {}) if isinstance(site_data, dict) else {}).get('candidates_markdown_path', '')), 'Open candidates')}</div>
  </div>
  {f"<p><strong>Suggestion:</strong> {html.escape(str(quality.get('recommendation', '')))}</p>" if quality.get('recommendation') else ""}
  {f"<p><strong>Quality reasons:</strong> {html.escape('; '.join(str(item) for item in quality.get('reasons', [])))}</p>" if quality.get('reasons') else ""}
  {f"<p><strong>Preferred reason:</strong> {html.escape(str(preferred_readable.get('reason', '')))}</p>" if preferred_readable.get('reason') else ""}
  {f"<p><strong>Review recommendation:</strong> {html.escape(str(review_recommendation.get('reason', '')))}</p>" if review_recommendation.get('reason') else ""}
  {f"<p><strong>Completeness reasons:</strong> {html.escape('; '.join(str(item) for item in completeness.get('reasons', [])))}</p>" if completeness.get('reasons') else ""}
  {f"<p><strong>Asset reasons:</strong> {html.escape('; '.join(str(item) for item in asset_completeness.get('reasons', [])))}</p>" if asset_completeness.get('reasons') else ""}
  {f"<p><strong>Feishu comparison reasons:</strong> {html.escape('; '.join(str(item) for item in feishu_comparison.get('reasons', [])))}</p>" if feishu_comparison.get('reasons') else ""}
  {files_html}
  {missing_html}
  {f"<strong>Browser DOM Outline</strong>{browser_outline_html}" if browser_outline_html else ""}
  {f"<strong>{markdown_view_label} Blocks</strong>{blocks_html}" if blocks_html else ""}
  {f"<strong>{markdown_view_label} Outline</strong>{outline_html}" if outline_html else ""}
  {f"<strong>{markdown_view_label} Images</strong>{images_html}" if images_html else ""}
</section>"""


def render(
    markdown_text: str = "",
    json_text: str = "",
    data: dict | None = None,
    batch_data: dict | None = None,
    quality_data: dict | None = None,
    recovery_data: dict | None = None,
    error: str = "",
    archive_dir: str = "",
    pack_result: dict | None = None,
) -> bytes:
    default_archive = str(Path.cwd() / "output" / "evidence")
    active_archive = archive_dir or default_archive
    default_browser_profile = str(default_profile_dir())
    profile_status = browser_profile_status(default_browser_profile)
    login_links = "".join(f'<li><a href="{html.escape(url, quote=True)}">{html.escape(url)}</a></li>' for url in COMMON_LOGIN_URLS)
    profile_warning = (
        '<p><strong>Warning:</strong> No Cookies database detected. Open login-all and log in manually before fetching private pages.</p>'
        if profile_status.get("possibly_anonymous")
        else ""
    )
    browser_profile_panel = f"""
<section>
  <strong>Login State Preparation</strong>
  <div class="summary">
    <div><b>Status</b><br>{html.escape(str(profile_status.get('status', '')))}</div>
    <div><b>Default profile</b><br><code>{html.escape(default_browser_profile)}</code></div>
    <div><b>Cookies DB</b><br>{'yes' if profile_status.get('has_cookies_db') else 'no'} ({html.escape(str(profile_status.get('cookies_db_count', 0)))})</div>
    <div><b>Modified</b><br>{html.escape(str(profile_status.get('modified_at', '')))}</div>
  </div>
  {profile_warning}
  <p><code>python -m web_content_fetcher.cli browser-profile check</code></p>
  <p><code>python -m web_content_fetcher.cli browser-profile login-all</code></p>
  <ul>{login_links}</ul>
</section>"""
    body = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Web Content Fetcher</title><style>{STYLE}</style></head>
<body><main>
<h1>Web Content Fetcher</h1>
{browser_profile_panel}
<form method="post">
  <input type="hidden" name="action" value="fetch">
  <label>URL <input name="url" type="text" placeholder="https://example.com" required></label>
  <div class="row">
    <label>Archive dir <input name="archive_dir" type="text" value="{html.escape(active_archive)}"></label>
    <label>Chrome profile <input name="profile" type="text" placeholder="Default"></label>
  </div>
  <div class="row">
    <label>Timeout seconds <input name="timeout" type="text" value="30"></label>
    <label>Backend <select name="backend"><option value="auto">Auto</option><option value="direct">Direct</option><option value="browser">Browser</option><option value="crawl4ai">Crawl4AI</option><option value="firecrawl">Firecrawl</option><option value="scrapling">Scrapling</option><option value="feishu_export">Feishu Export</option><option value="xhs_auto">XHS Auto</option><option value="xhs_redbook_cli">XHS Redbook CLI</option><option value="xhs_browser_state">XHS Browser State</option></select></label>
  </div>
  <label>Extractor <select name="extractor"><option value="auto">Auto</option><option value="yuque">Yuque</option><option value="trafilatura">Trafilatura</option><option value="readability">Readability</option><option value="defuddle">Defuddle</option><option value="builtin">Builtin</option></select></label>
  <label>Persistent browser profile <input name="chrome_user_data_dir" type="text" value="{html.escape(default_browser_profile)}"></label>
  <div class="row">
    <label>Crawl4AI wait_for <input name="crawl4ai_wait_for" type="text" placeholder="#main, css:.ready, or JS condition"></label>
    <label>Crawl4AI CSS selector <input name="crawl4ai_css_selector" type="text" placeholder="article, main, .doc-content"></label>
  </div>
  <div class="row">
    <label>Crawl4AI cache mode <select name="crawl4ai_cache_mode"><option value="bypass">Bypass</option><option value="enabled">Enabled</option></select></label>
    <label>Firecrawl formats <input name="firecrawl_formats" type="text" value="markdown,html,links"></label>
  </div>
  <div class="row">
    <label>Scrapling mode <select name="scrapling_mode"><option value="static">Static</option><option value="dynamic">Dynamic</option><option value="stealthy">Stealthy</option></select></label>
    <label>Scrapling wait selector <input name="scrapling_wait_selector" type="text" placeholder="article, main, .ready"></label>
  </div>
  <div class="row">
    <label>Scrapling wait ms <input name="scrapling_wait_ms" type="text" value="0"></label>
    <label>Scrapling PYTHONPATH <input name="scrapling_python_path" type="text" placeholder="C:\\path\\to\\Scrapling"></label>
  </div>
  <div class="row">
    <label>Scrapling Python <input name="scrapling_python_executable" type="text" placeholder="C:\\path\\to\\Scrapling\\.venv\\Scripts\\python.exe"></label>
    <span></span>
  </div>
  <div class="checks">
    <label><input name="auto_route" type="checkbox" checked> Auto route document link</label>
    <label><input name="use_browser" type="checkbox"> Use browser</label>
    <label><input name="auto_scroll" type="checkbox"> Auto scroll</label>
    <label><input name="headless" type="checkbox" checked> Headless</label>
    <label><input name="screenshot" type="checkbox" checked> Screenshot</label>
    <label><input name="download_images" type="checkbox" checked> Download images</label>
    <label><input name="singlefile" type="checkbox"> SingleFile</label>
    <label><input name="crawl4ai_screenshot" type="checkbox"> Crawl4AI screenshot</label>
    <label><input name="crawl4ai_pdf" type="checkbox"> Crawl4AI PDF</label>
    <label><input name="compare_feishu_export" type="checkbox"> Compare Feishu export</label>
    <label>Firecrawl API URL <input name="firecrawl_api_url" type="text" placeholder="https://api.firecrawl.dev/v1/scrape"></label>
    <label>Firecrawl key env <input name="firecrawl_api_key_env" type="text" value="FIRECRAWL_API_KEY"></label>
    <label>Firecrawl wait ms <input name="firecrawl_wait_for_ms" type="text" value="0"></label>
    <label><input name="firecrawl_include_full_page" type="checkbox"> Firecrawl full page</label>
    <label><input name="scrapling_network_idle" type="checkbox"> Scrapling network idle</label>
    <label><input name="scrapling_headful" type="checkbox"> Scrapling headful</label>
    <label><input name="scrapling_real_chrome" type="checkbox"> Scrapling real Chrome</label>
    <label><input name="discover_endpoints_only" type="checkbox"> Endpoint discovery dry-run</label>
    <label><input name="check_robots" type="checkbox" checked> Robots hint</label>
    <label><input name="with_visual_check" type="checkbox" checked> Visual check rebuild</label>
  </div>
  <div class="checks">
    <button type="submit" name="run_mode" value="fetch">Fetch</button>
    <button type="submit" name="run_mode" value="fetch_and_rebuild" class="secondary">Fetch + Rebuild</button>
  </div>
</form>
<section>
  <strong>Batch Fetch</strong>
  <form method="post">
    <input type="hidden" name="action" value="fetch_batch">
    <label>URLs <textarea name="urls" rows="6" placeholder="https://example.com&#10;https://www.vaticannews.va/..."></textarea></label>
    <div class="row">
      <label>Archive dir <input name="archive_dir" type="text" value="{html.escape(active_archive)}"></label>
      <label>Chrome profile <input name="profile" type="text" placeholder="Default"></label>
    </div>
    <div class="row">
      <label>Timeout seconds <input name="timeout" type="text" value="30"></label>
      <label>Backend <select name="backend"><option value="auto">Auto</option><option value="direct">Direct</option><option value="browser">Browser</option><option value="crawl4ai">Crawl4AI</option><option value="firecrawl">Firecrawl</option><option value="scrapling">Scrapling</option><option value="feishu_export">Feishu Export</option><option value="xhs_auto">XHS Auto</option><option value="xhs_redbook_cli">XHS Redbook CLI</option><option value="xhs_browser_state">XHS Browser State</option></select></label>
    </div>
    <label>Extractor <select name="extractor"><option value="auto">Auto</option><option value="yuque">Yuque</option><option value="trafilatura">Trafilatura</option><option value="readability">Readability</option><option value="defuddle">Defuddle</option><option value="builtin">Builtin</option></select></label>
    <label>Persistent browser profile <input name="chrome_user_data_dir" type="text" value="{html.escape(default_browser_profile)}"></label>
    <div class="checks">
      <label><input name="batch_auto_route" type="checkbox" checked> Auto route document links</label>
      <label><input name="headless" type="checkbox" checked> Headless</label>
      <label><input name="compare_feishu_export" type="checkbox"> Compare Feishu export</label>
      <label><input name="check_robots" type="checkbox" checked> Robots hint</label>
      <label><input name="include_results" type="checkbox"> Include full results</label>
    </div>
    <button type="submit">Fetch Batch</button>
  </form>
</section>
{f'<section><strong>Error</strong><pre>{html.escape(error)}</pre></section>' if error else ''}
{_summary_html(data or {})}
{_batch_summary_html(batch_data or {})}
{_rebuild_batch_summary_html(batch_data or {})}
{_quality_summary_html(quality_data or {})}
{_recovery_pack_summary_html(recovery_data or {})}
{_quality_runs_html(active_archive)}
{_archive_list_html(active_archive, pack_result)}
{f'<section><strong>Markdown</strong><pre>{html.escape(markdown_text)}</pre></section>' if markdown_text else ''}
{f'<section><strong>JSON</strong><pre>{html.escape(json_text)}</pre></section>' if json_text else ''}
</main></body></html>"""
    return body.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/preview":
            params = urllib.parse.parse_qs(parsed.query)
            path = Path((params.get("path") or [""])[-1])
            self._send_preview(path)
            return
        self._send(render())

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        form = {key: values[-1] for key, values in urllib.parse.parse_qs(raw).items()}
        try:
            if form.get("action") == "pack_archive":
                packed = pack_archive(form.get("archive_path", ""))
                self._send(render(archive_dir=form.get("archive_dir", ""), pack_result=packed))
                return
            if form.get("action") == "rebuild_existing_archive":
                md, js, data = run_rebuild_existing_archive(form)
                self._send(render(md, js, data, archive_dir=form.get("archive_dir", "")))
                return
            if form.get("action") == "rebuild_archive_batch":
                data = run_rebuild_archive_batch(form)
                self._send(
                    render(
                        json_text=json.dumps(data, ensure_ascii=False, indent=2),
                        batch_data=data,
                        archive_dir=form.get("archive_dir", ""),
                    )
                )
                return
            if form.get("action") == "fetch_batch":
                data = run_batch(form)
                self._send(
                    render(
                        json_text=json.dumps(data, ensure_ascii=False, indent=2),
                        batch_data=data,
                        archive_dir=form.get("archive_dir", ""),
                    )
                )
                return
            if form.get("action") == "quality_smoke":
                data = run_quality(form)
                self._send(
                    render(
                        json_text=json.dumps(data, ensure_ascii=False, indent=2),
                        quality_data=data,
                        recovery_data=data.get("manual_recovery") or {},
                        archive_dir=form.get("archive_dir", ""),
                    )
                )
                return
            if form.get("action") == "quality_recovery_pack":
                data = run_recovery_pack(form)
                self._send(
                    render(
                        json_text=json.dumps(data, ensure_ascii=False, indent=2),
                        recovery_data=data,
                        archive_dir=form.get("archive_dir", ""),
                    )
                )
                return
            if form.get("action") == "rerun_batch":
                data = run_batch_file(form)
                self._send(
                    render(
                        json_text=json.dumps(data, ensure_ascii=False, indent=2),
                        batch_data=data,
                        archive_dir=form.get("archive_dir", ""),
                    )
                )
                return
            if form.get("run_mode") == "fetch_and_rebuild":
                md, js, data = run_fetch_and_rebuild(form)
            else:
                md, js, data = run_fetch(form)
            self._send(render(md, js, data, archive_dir=form.get("archive_dir", "")))
        except Exception as exc:
            self._send(render(error=f"{type(exc).__name__}: {exc}", archive_dir=form.get("archive_dir", "")), status=500)

    def log_message(self, format: str, *args) -> None:
        return

    def _send(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_preview(self, path: Path) -> None:
        if not _is_archive_preview_path(path):
            self._send(render(error="preview path is not inside an archive"), status=404)
            return
        resolved = path.resolve()
        media_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        if resolved.name == "readable.html":
            text = resolved.read_text(encoding="utf-8", errors="replace")
            body = _rewrite_readable_asset_links(text, resolved).encode("utf-8")
            media_type = "text/html; charset=utf-8"
        elif media_type.startswith("text/") or media_type in {"application/json", "application/xml"}:
            body = resolved.read_text(encoding="utf-8", errors="replace").encode("utf-8")
            if "charset=" not in media_type:
                media_type += "; charset=utf-8"
        else:
            body = resolved.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    from .runtime_entry import maybe_reexec
    runtime_exit = maybe_reexec('web_content_fetcher.ui', argv)
    if runtime_exit is not None:
        return runtime_exit
    config = load_config()
    parser = argparse.ArgumentParser(description="Run the Web Content Fetcher UI.")
    parser.add_argument("--host", default=web_ui_host(config))
    parser.add_argument("--port", type=int, default=web_ui_port(config))
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Web Content Fetcher UI: http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
