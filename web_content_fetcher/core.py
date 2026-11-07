from __future__ import annotations

import gzip
import hashlib
import html as html_lib
import json
import asyncio
import os
import re
import shutil
import subprocess
import socket
import ssl
import urllib.parse
import urllib.request
import urllib.error
import zlib
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .archive import archive_child_path, sha256_text, write_archive
from .assets import download_images
from .browser_fetch import BrowserFetchOptions, browser_profile_status, browser_user_data_dir, fetch_with_browser
from .cache import read_cache, write_cache
from .backend_registry import backend_catalog, normalize_fetch_backend
from .config import web_access_base_url
from .completeness import access_boundary, inspect_capture
from .crawl4ai_backend import Crawl4AIRequest, run_crawl4ai
from .diagnostics import (
    detect_login_redirect,
    detect_document_mismatch,
    detect_spa_shell,
    discover_endpoint_candidates,
    endpoint_candidates_markdown,
    normalize_browser_error,
    preflight_archive_dir,
    redact_sensitive_json,
    script_urls_from_html,
    summarize_json_response,
)
from .extractors import extract_content, quality_for
from .firecrawl_backend import FirecrawlRequest, run_firecrawl
from .monolith_backend import MonolithRequest, run_monolith
from .feishu_export import (
    FeishuExportUnavailable,
    compare_feishu_captures,
    export_feishu_docx,
    feishu_credentials_available,
    is_feishu_doc_url,
    markdown_capture_stats,
    parse_docx_export,
)
from .html_extract import collect_images, collect_links, extract_selector_results, parse_html, title_from_html
from .profiles import apply_profile
from .robots import robots_hint
from .scrapling_backend import ScraplingRequest, run_scrapling
from .werss_backend import WerssRequest, run_werss
from .wechat_download_api import fetch_wechat_article
from .site_plugins import extract_attachment_mentions, extract_site_data
from .xhs_backend import (
    extract_initial_state_note,
    is_xhs_url,
    normalize_note,
    note_to_markdown,
    redbook_cli_fetch,
    write_raw_tool_result,
)


DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
RESULT_SCHEMA_VERSION = 5


@dataclass
class FetchRequest:
    url: str
    fetch_profile: str = "generic"
    timeout_seconds: int = 15
    max_bytes: int = 2_500_000
    user_agent: str = DEFAULT_UA
    selectors: list[dict[str, Any]] = field(default_factory=list)
    use_web_access: bool = False
    web_access_base_url: str = ""
    backend: str = "auto"
    extractor: str = "auto"
    prefer_browser: bool = False
    use_browser: bool = False
    browser_headless: bool = False
    browser_profile: str = ""
    browser_user_data_dir: str = ""
    browser_executable_path: str = ""
    browser_storage_state: str = ""
    browser_wait_until: str = "load"
    browser_auto_scroll: bool = False
    browser_scroll_steps: int = 8
    browser_settle_ms: int = 700
    browser_dismiss_obstructive_overlays: bool = True
    save_screenshot: bool = False
    download_images: bool = False
    save_singlefile: bool = False
    snapshot_backend: str = ""
    monolith_executable: str = ""
    monolith_mhtml: bool = False
    auth_profile: str = ""
    proxy_session: str = ""
    archive_dir: str = ""
    cache_dir: str = ""
    cache_max_age_seconds: int = 0
    check_robots: bool = True
    public_http_only: bool = False
    save_raw: bool = True
    save_extracted: bool = True
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
    werss_base_url: str = ""
    werss_article_id: str = ""
    werss_api_key_env: str = "WERSS_API_KEY"
    wechat_api_base_url: str = ""
    compare_feishu_export: bool = False
    discover_endpoints_only: bool = False
    site_strategy: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchResult:
    requested_url: str
    final_url: str
    status: str
    transport: str
    schema_version: int = RESULT_SCHEMA_VERSION
    content_type: str = ""
    fetched_at: str = ""
    title: str = ""
    raw_snapshot: str = ""
    extracted_text: str = ""
    links: list[str] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    image_assets: list[dict[str, Any]] = field(default_factory=list)
    selector_results: list[dict[str, Any]] = field(default_factory=list)
    site_data: dict[str, Any] = field(default_factory=dict)
    extraction_engine: str = ""
    extraction_quality: dict[str, Any] = field(default_factory=dict)
    extracted_metadata: dict[str, Any] = field(default_factory=dict)
    raw_sha256: str = ""
    extracted_sha256: str = ""
    robots: dict[str, Any] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)
    archive: dict[str, Any] | None = None
    page_html_path: str = ""
    screenshot_path: str = ""
    singlefile_path: str = ""
    preferred_markdown_path: str = ""
    reading_report_path: str = ""
    browser_visible_path: str = ""
    browser_visible_html_path: str = ""
    browser_visible_markdown: str = ""
    browser_visible_blocks: list[dict[str, Any]] = field(default_factory=list)
    endpoint_candidates: list[dict[str, Any]] = field(default_factory=list)
    evidence_chain: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    http_status: int | None = None
    transport_ok: bool | None = None
    content_ok: bool | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    capture_completeness: dict[str, Any] = field(default_factory=dict)

    def assess_validity(self) -> None:
        if self.transport_ok is None:
            self.transport_ok = 200 <= self.http_status < 300 if self.http_status is not None else self.transport != "none" and self.status != "failed"
        self.content_ok = bool(
            self.transport_ok and self.extracted_text.strip()
            and not self.payload.get("binary") and not self.payload.get("truncated")
            and not self.extraction_quality.get("login_redirect_detected")
            and not self.extraction_quality.get("document_identity_mismatch")
            and not self.extraction_quality.get("spa_shell_detected")
            and not self.extraction_quality.get("access_blocked")
            and not self.extraction_quality.get("dom_link_text_missing")
            and not self.extraction_quality.get("dom_body_text_missing")
            and self.extraction_quality.get("status") not in {"empty", "too_short", "shell", "suspect_shell", "login_required"}
        )
        if self.status == "ok" and not self.content_ok:
            self.status = "partial" if self.transport_ok else "failed"

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        self.assess_validity()
        data = asdict(self)
        archive = self.archive or {}
        preferred_readable = build_archive_index(self).get("preferred_readable") if self.archive else {}
        data["archive_path"] = archive.get("archive_path", "")
        data["readable_html_path"] = archive.get("readable_html_path", "")
        data["preferred_readable_html_path"] = archive.get("preferred_readable_html_path", "")
        data["preferred_readable_path"] = (preferred_readable or {}).get("html_path", "") or archive.get("preferred_readable_html_path", "") or archive.get("readable_html_path", "")
        data["preferred_markdown_path"] = self.preferred_markdown_path or archive.get("preferred_markdown_path", "")
        data["reading_report_path"] = self.reading_report_path or archive.get("reading_report_path", "")
        data["archive_home_path"] = archive.get("archive_home_path", "")
        data["structured_path"] = archive.get("structured_path", "")
        data["image_gallery_path"] = archive.get("image_gallery_path", "")
        if not include_raw:
            data.pop("raw_snapshot", None)
        return data


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "gbk", "latin-1"):
        try:
            return data.decode(encoding)
        except Exception:
            continue
    return data.decode("utf-8", errors="ignore")


class DirectPayload(str):
    """Keep legacy tuple/string consumers while retaining bounded response bytes."""

    def __new__(cls, data: bytes, content_type: str, http_status: int, truncated: bool = False):
        declared = content_type.split(";", 1)[0].strip().lower()
        signatures = ((b"%PDF-", "application/pdf", ".pdf"),
                      (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
                      (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
                      (b"GIF8", "image/gif", ".gif"),
                      (b"PK\x03\x04", "application/zip", ".zip"))
        detected, extension = "", ""
        for magic, mime, suffix in signatures:
            if data.startswith(magic):
                detected, extension = mime, suffix
                break
        head = data[:1024].lstrip().lower()
        if not detected and head.startswith((b"<!doctype html", b"<html", b"<body")):
            detected = "text/html"
        binary = bool(extension) or (not detected and (b"\0" in data[:8192] or declared.startswith(("image/", "audio/", "video/")) or declared in {"application/pdf", "application/zip", "application/octet-stream"}))
        obj = super().__new__(cls, "" if binary else decode_bytes(data))
        obj.raw_bytes = data
        obj.http_status = http_status
        obj.payload = {"binary": binary, "declared_mime": declared,
                       "detected_mime": detected or ("application/octet-stream" if binary else declared),
                       "mime_conflict": bool(detected and declared and declared != detected),
                       "extension": extension or (".bin" if binary else ""),
                       "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                       "truncated": truncated}
        return obj


def direct_fetch(request: FetchRequest) -> tuple[str, str, str]:
    socket.setdefaulttimeout(request.timeout_seconds)
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        request.url,
        headers={
            "User-Agent": request.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml,application/json,text/plain,*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        if request.public_http_only:
            from .public_http import public_open
            response = public_open(req, timeout=request.timeout_seconds, context=ctx)
        else:
            response = urllib.request.urlopen(req, timeout=request.timeout_seconds, context=ctx)
    except urllib.error.HTTPError as exc:
        response = exc
    with response as resp:
        status = resp.getcode()
        content_type = resp.headers.get("content-type", "")
        allowed_text = content_type.split(';', 1)[0].strip().lower() in {
            'text/html', 'application/xhtml+xml', 'text/plain', 'text/markdown', 'application/json'}
        data = resp.read(request.max_bytes + 1) if not request.public_http_only or allowed_text else b''
        encoding = (resp.headers.get("content-encoding", "") or "").lower()
        final_url = resp.geturl()
    truncated = len(data) > request.max_bytes
    if data and ("gzip" in encoding or "deflate" in encoding):
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if "gzip" in encoding else zlib.MAX_WBITS)
        data = decoder.decompress(data, request.max_bytes + 1)
        truncated = truncated or not decoder.eof or len(data) > request.max_bytes
    raw = DirectPayload(data[:request.max_bytes], content_type, status, truncated)
    return raw, raw.payload["detected_mime"] or content_type, final_url


def web_access_fetch(request: FetchRequest) -> tuple[str, str, str, str]:
    base_url = request.web_access_base_url or web_access_base_url()
    payload: dict[str, Any] = {
        "url": request.url,
        "formats": ["markdown", "html", "links"],
        "intent": "read",
        "policyMode": "permissive",
        "timeoutMs": request.timeout_seconds * 1000,
    }
    if request.prefer_browser:
        payload["preferBrowser"] = True
    if request.auth_profile:
        payload["authProfile"] = request.auth_profile
    if request.proxy_session:
        payload["proxySession"] = request.proxy_session

    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/fetch",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": request.user_agent,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=request.timeout_seconds, context=ctx) as resp:
        raw = resp.read(request.max_bytes)
    body = json.loads(raw.decode("utf-8", errors="replace"))
    if not body.get("success"):
        raise RuntimeError(f"web_access_failed:{body.get('errorType') or body.get('error') or 'unknown'}")
    data = body.get("data") or {}
    html = str(data.get("html") or "")
    markdown = str(data.get("markdown") or "")
    content = html or markdown
    if not content:
        raise RuntimeError("web_access_empty_content")
    final_url = str(body.get("finalUrl") or body.get("url") or request.url)
    transport = str(body.get("levelUsed") or "web_access")
    content_type = "text/html" if html else "text/markdown"
    return content, content_type, final_url, transport



def _fetch_text_url(url: str, request: FetchRequest) -> tuple[str, str]:
    socket.setdefaulttimeout(request.timeout_seconds)
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": request.user_agent,
            "Accept": "application/javascript,text/javascript,text/plain,*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=request.timeout_seconds, context=ctx) as resp:
        raw = resp.read(min(request.max_bytes, 1_500_000))
        content_type = resp.headers.get("content-type", "")
        encoding = (resp.headers.get("content-encoding") or "").lower()
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return decode_bytes(raw), content_type


def _discover_endpoint_sources(raw: str, final_url: str, request: FetchRequest) -> tuple[list[dict[str, Any]], list[str]]:
    sources = [{"source_url": final_url, "source_type": "html", "content": raw}]
    errors: list[str] = []
    for script_url in script_urls_from_html(raw, final_url)[:12]:
        try:
            script_text, _content_type = _fetch_text_url(script_url, request)
            sources.append({"source_url": script_url, "source_type": "script", "content": script_text})
        except Exception as exc:
            errors.append(f"endpoint_discovery_script:{type(exc).__name__}:{script_url}:{str(exc)[:120]}")
    return sources, errors


def _write_endpoint_candidate_artifacts(result: FetchResult) -> None:
    if not result.archive or not result.endpoint_candidates:
        return
    json_path = archive_child_path(result.archive, "endpoint_candidates.json")
    markdown_path = archive_child_path(result.archive, "endpoint_candidates.md")
    if json_path:
        payload = json.dumps(result.endpoint_candidates, ensure_ascii=False, indent=2)
        json_path.write_text(payload, encoding="utf-8", newline="\n")
        result.site_data.setdefault("endpoint_discovery", {})["candidates_json_path"] = str(json_path)
        result.site_data.setdefault("endpoint_discovery", {})["candidates_json_sha256"] = sha256_text(payload)
    if markdown_path:
        markdown = endpoint_candidates_markdown(result.endpoint_candidates, page_url=result.final_url or result.requested_url)
        markdown_path.write_text(markdown, encoding="utf-8", newline="\n")
        result.site_data.setdefault("endpoint_discovery", {})["candidates_markdown_path"] = str(markdown_path)
        result.site_data.setdefault("endpoint_discovery", {})["candidates_markdown_sha256"] = sha256_text(markdown)


def _write_json_api_artifacts(result: FetchResult) -> None:
    if not result.archive:
        return
    api = (result.extracted_metadata or {}).get("api_response") or {}
    if not api.get("is_json"):
        return
    pretty = str(api.get("pretty_json") or "")
    summary = str(api.get("summary_markdown") or "")
    api = {key: value for key, value in api.items() if key not in {"pretty_json", "summary_markdown"}}
    pretty_path = archive_child_path(result.archive, "pretty.json")
    summary_path = archive_child_path(result.archive, "json_summary.md")
    if pretty_path:
        pretty_path.write_text(pretty, encoding="utf-8", newline="\n")
        api["pretty_json_path"] = str(pretty_path)
        api["pretty_json_sha256"] = sha256_text(pretty)
    if summary_path:
        summary_path.write_text(summary, encoding="utf-8", newline="\n")
        api["summary_markdown_path"] = str(summary_path)
        api["summary_markdown_sha256"] = sha256_text(summary)
    result.extracted_metadata["api_response"] = api
    result.site_data.setdefault("api_response", {}).update(api)


def _augment_quality_diagnostics(result: FetchResult, raw: str) -> None:
    identity = detect_document_mismatch(result.requested_url, result.final_url)
    if identity["detected"]:
        result.site_data.setdefault("diagnostics", {})["document_identity"] = identity
        result.extraction_quality = {**result.extraction_quality,
            "document_identity_mismatch": True, "status": "document_mismatch",
            "recommendation": identity["recommendation"]}
        if "document_identity_mismatch" not in result.errors:
            result.errors.append("document_identity_mismatch")
    spa = detect_spa_shell(raw, extracted_text=result.extracted_text, links=result.links)
    login = detect_login_redirect(raw, requested_url=result.requested_url, final_url=result.final_url, title=result.title)
    hints: list[str] = []
    reasons = list((result.extraction_quality or {}).get("reasons") or [])
    if spa.get("detected"):
        hints.append("spa_shell_detected")
        reasons.extend(spa.get("reasons") or [])
    if login.get("detected"):
        hints.append("login_redirect_detected")
        reasons.extend(login.get("reasons") or [])
    if not hints:
        return
    quality = dict(result.extraction_quality or {})
    status = str(quality.get("status") or "")
    if login.get("detected"):
        if status not in {"empty", "too_short", "suspect_incomplete"}:
            quality["status"] = "suspect_shell"
        quality["login_redirect_detected"] = True
    if spa.get("detected"):
        if status not in {"empty", "too_short", "suspect_incomplete"}:
            quality["status"] = "suspect_shell"
        quality["spa_shell_detected"] = True
    recovery_hints = list(quality.get("recovery_hints") or [])
    recovery_hints.extend(hints)
    if spa.get("detected"):
        recovery_hints.append("try_endpoint_discovery_dry_run")
    if login.get("detected"):
        recovery_hints.append("use_authorized_browser_profile_or_official_export")
    quality["recovery_hints"] = list(dict.fromkeys(recovery_hints))
    quality["reasons"] = list(dict.fromkeys(str(item) for item in reasons if str(item)))
    quality["recommendation"] = login.get("recommendation") if login.get("detected") else spa.get("recommendation")
    result.extraction_quality = quality
    diagnostics = result.site_data.setdefault("diagnostics", {})
    diagnostics["spa_shell"] = spa
    diagnostics["login_redirect"] = login


def _build_evidence_chain(result: FetchResult) -> list[dict[str, Any]]:
    chain = list(result.evidence_chain or [])
    if result.capture_completeness:
        chain.append({"stage": "saved_dom_completeness", "source_sha256": result.raw_sha256,
                      "manifest_field": "capture_completeness", "url": result.requested_url,
                      "policy": "offline_comparison_no_original_download_implied"})
    archive = result.archive or {}
    if archive.get("raw_snapshot_path"):
        chain.append({"stage": "raw_snapshot", "transport": result.transport, "url": result.final_url or result.requested_url, "path": archive.get("raw_snapshot_path"), "sha256": archive.get("raw_snapshot_sha256", "")})
    if result.page_html_path:
        chain.append({"stage": "page_html", "transport": result.transport, "url": result.final_url or result.requested_url, "path": result.page_html_path})
    diagnostics = (result.site_data or {}).get("diagnostics") or {}
    if (diagnostics.get("login_redirect") or {}).get("detected"):
        chain.append({"stage": "browser_login_redirect", "url": result.final_url or result.requested_url, "diagnostic": diagnostics.get("login_redirect")})
    endpoint_discovery = (result.site_data or {}).get("endpoint_discovery") or {}
    for source in endpoint_discovery.get("sources", [])[:20]:
        if source.get("source_type") == "script":
            chain.append({"stage": "js_bundle", "url": source.get("source_url", "")})
    if result.endpoint_candidates:
        chain.append({"stage": "discovered_endpoint_candidates", "count": len(result.endpoint_candidates), "path": endpoint_discovery.get("candidates_json_path", "")})
    api = (result.site_data or {}).get("api_response") or {}
    if api.get("is_json"):
        chain.append({"stage": "json_api_archive", "url": result.final_url or result.requested_url, "path": api.get("pretty_json_path", ""), "sha256": api.get("pretty_json_sha256", "")})
    return chain


def browser_fetch(request: FetchRequest, archive: dict[str, Any] | None = None) -> tuple[str, str, str, str, dict[str, Any]]:
    screenshot_path = ""
    html_path = ""
    image_assets_dir = ""
    if archive:
        html_child = archive_child_path(archive, "page.html")
        if html_child:
            html_path = str(html_child)
        if request.save_screenshot:
            screenshot_child = archive_child_path(archive, "screenshot.png")
            if screenshot_child:
                screenshot_path = str(screenshot_child)
        if request.download_images:
            image_assets_child = archive_child_path(archive, "image_assets", ".keep")
            if image_assets_child:
                image_assets_dir = str(image_assets_child.parent)
    options = BrowserFetchOptions(
        url=request.url,
        timeout_seconds=request.timeout_seconds,
        headless=request.browser_headless,
        profile=request.browser_profile,
        user_data_dir=request.browser_user_data_dir,
        executable_path=request.browser_executable_path,
        storage_state=request.browser_storage_state,
        wait_until=request.browser_wait_until,
        screenshot_path=screenshot_path,
        html_path=html_path,
        image_assets_dir=image_assets_dir,
        download_images=request.download_images,
        auto_scroll=request.browser_auto_scroll,
        scroll_steps=request.browser_scroll_steps,
        settle_ms=request.browser_settle_ms,
        dismiss_obstructive_overlays=request.browser_dismiss_obstructive_overlays,
    )
    output = fetch_with_browser(options)
    profile_status = {} if options.storage_state else browser_profile_status(browser_user_data_dir(options), profile=options.profile)
    extras = {
        "title": output.title,
        "page_html_path": output.html_path,
        "screenshot_path": output.screenshot_path,
        "image_assets": output.image_assets or [],
        "browser_visible_blocks": output.visible_blocks or [],
        "browser_visible_markdown": output.visible_markdown or "",
        "browser_visible_metrics": output.visible_metrics or {},
        "site_data": {
            "browser_profile": {
                "user_data_dir": profile_status.get("user_data_dir", "") or ("" if options.storage_state else browser_user_data_dir(options)),
                "profile": options.profile,
                "headless": options.headless,
                "storage_state": bool(options.storage_state),
                "status": profile_status.get("status", "storage_state" if options.storage_state else ""),
                "has_cookies_db": profile_status.get("has_cookies_db", False),
                "possibly_anonymous": profile_status.get("possibly_anonymous", False if options.storage_state else True),
                "modified_at": profile_status.get("modified_at", ""),
            }
        },
    }
    return output.html, "text/html", output.final_url, "browser", extras


def crawl4ai_fetch(request: FetchRequest) -> tuple[str, str, str, str, dict[str, Any]]:
    result = asyncio.run(
        run_crawl4ai(
            Crawl4AIRequest(
                url=request.url,
                timeout_seconds=request.timeout_seconds,
                headless=request.browser_headless,
                wait_for=request.crawl4ai_wait_for,
                css_selector=request.crawl4ai_css_selector,
                screenshot=request.crawl4ai_screenshot,
                pdf=request.crawl4ai_pdf,
                cache_mode=request.crawl4ai_cache_mode,
                base_directory=request.archive_dir or request.cache_dir,
            )
        )
    )
    if not bool(result.get("success", True)):
        raise RuntimeError(str(result.get("error_message") or "crawl4ai_failed"))
    markdown = str(result.get("markdown") or "")
    html = str(result.get("html") or result.get("cleaned_html") or "")
    content = html or markdown
    if not content:
        raise RuntimeError("crawl4ai_empty_content")
    final_url = str(result.get("url") or request.url)
    extras = {
        "title": str((result.get("metadata") or {}).get("title") or ""),
        "crawl4ai_markdown": markdown,
        "site_data": {
            "crawl4ai": {
                "config": result.get("config") or {},
                "metadata": result.get("metadata") or {},
                "links": result.get("links") or {},
                "media": result.get("media") or {},
                "markdown_chars": len(markdown),
                "html_chars": len(html),
                "cleaned_html_chars": len(str(result.get("cleaned_html") or "")),
                "has_screenshot": bool(result.get("screenshot_bytes")),
                "has_pdf": bool(result.get("pdf_bytes")),
            }
        },
        "extracted_metadata": {
            "crawl4ai": {
                "config": result.get("config") or {},
                "metadata": result.get("metadata") or {},
                "markdown_chars": len(markdown),
                "html_chars": len(html),
            }
        },
        "crawl4ai": result,
    }
    content_type = "text/html" if html else "text/markdown"
    return content, content_type, final_url, "crawl4ai", extras



def firecrawl_fetch(request: FetchRequest) -> tuple[str, str, str, str, dict[str, Any]]:
    result = run_firecrawl(
        FirecrawlRequest(
            url=request.url,
            timeout_seconds=request.timeout_seconds,
            api_url=request.firecrawl_api_url,
            api_key_env=request.firecrawl_api_key_env,
            formats=request.firecrawl_formats,
            only_main_content=request.firecrawl_only_main_content,
            wait_for=request.firecrawl_wait_for_ms,
        )
    )
    markdown = str(result.get("markdown") or "")
    html = str(result.get("html") or "")
    content = html or markdown
    if not content:
        raise RuntimeError("firecrawl_empty_content")
    final_url = str(result.get("url") or request.url)
    metadata = result.get("metadata") or {}
    safe_metadata = redact_sensitive_json(metadata)
    safe_config = redact_sensitive_json(result.get("config") or {})
    safe_links = redact_sensitive_json(result.get("links") or [])
    safe_raw_response = redact_sensitive_json(result.get("raw_response") or {})
    archive_result = {
        **result,
        "metadata": safe_metadata,
        "config": safe_config,
        "links": safe_links,
        "raw_response": safe_raw_response,
    }
    extras = {
        "title": str(metadata.get("title") or ""),
        "site_data": {
            "firecrawl": {
                "config": safe_config,
                "metadata": safe_metadata,
                "links": safe_links,
                "markdown_chars": len(markdown),
                "html_chars": len(html),
                "link_count": len(result.get("links") or []),
                "has_screenshot": bool(result.get("screenshot_bytes")),
            }
        },
        "extracted_metadata": {
            "firecrawl": {
                "config": safe_config,
                "metadata": safe_metadata,
                "markdown_chars": len(markdown),
                "html_chars": len(html),
                "raw_response": safe_raw_response,
            }
        },
        "firecrawl": archive_result,
    }
    content_type = "text/html" if html else "text/markdown"
    return content, content_type, final_url, "firecrawl", extras


def scrapling_fetch(request: FetchRequest) -> tuple[str, str, str, str, dict[str, Any]]:
    result = run_scrapling(
        ScraplingRequest(
            url=request.url,
            timeout_seconds=request.timeout_seconds,
            mode=request.scrapling_mode,
            headless=request.scrapling_headless,
            network_idle=request.scrapling_network_idle,
            wait_ms=request.scrapling_wait_ms,
            wait_selector=request.scrapling_wait_selector,
            real_chrome=request.scrapling_real_chrome,
            user_data_dir=request.scrapling_user_data_dir,
            python_path=request.scrapling_python_path,
            python_executable=request.scrapling_python_executable,
        )
    )
    html = str(result.get("html") or "")
    if not html:
        raise RuntimeError("scrapling_empty_content")
    final_url = str(result.get("url") or request.url)
    extras = {
        "site_data": {
            "scrapling": {
                "config": result.get("config") or {},
                "status": result.get("status", 0),
                "reason": result.get("reason", ""),
                "links": result.get("links") or [],
                "images": result.get("images") or [],
                "html_chars": len(html),
                "text_chars": len(str(result.get("text") or "")),
                "link_count": len(result.get("links") or []),
                "image_count": len(result.get("images") or []),
            }
        },
        "extracted_metadata": {
            "scrapling": {
                "config": result.get("config") or {},
                "status": result.get("status", 0),
                "reason": result.get("reason", ""),
                "html_chars": len(html),
                "text_chars": len(str(result.get("text") or "")),
            }
        },
        "links": result.get("links") or [],
        "images": result.get("images") or [],
        "scrapling": result,
    }
    return html, "text/html", final_url, "scrapling", extras

def feishu_export_fetch(request: FetchRequest, archive: dict[str, Any] | None = None) -> tuple[str, str, str, str, dict[str, Any]]:
    export_dir = ""
    assets_dir = ""
    if archive:
        export_child = archive_child_path(archive, "feishu_export", ".keep")
        if export_child:
            export_dir = str(export_child.parent)
        assets_child = archive_child_path(archive, "image_assets", ".keep")
        if assets_child:
            assets_dir = str(assets_child.parent)
    exported = export_feishu_docx(request.url, output_dir=export_dir, timeout=max(request.timeout_seconds, 60))
    parsed = parse_docx_export(exported["docx_path"], assets_dir=assets_dir)
    markdown = str(parsed.get("markdown") or "")
    if not markdown:
        raise RuntimeError("feishu_export_empty_markdown")
    extras = {
        "title": parsed.get("title") or exported.get("file_name") or "",
        "image_assets": parsed.get("image_assets") or [],
        "site_data": {
            "feishu_export": {
                "doc_token": exported.get("doc_token", ""),
                "ticket": exported.get("ticket", ""),
                "file_token": exported.get("file_token", ""),
                "file_name": exported.get("file_name", ""),
                "docx_path": exported.get("docx_path", ""),
                "bytes": exported.get("bytes", 0),
                "image_count": parsed.get("image_count", 0),
                "markdown_stats": parsed.get("markdown_stats", {}),
                "text_chars": parsed.get("text_chars", 0),
                "heading_count": parsed.get("heading_count", 0),
                "table_count": parsed.get("table_count", 0),
            }
        },
        "extracted_metadata": {
            "feishu_export": {
                "docx_path": exported.get("docx_path", ""),
                "image_count": parsed.get("image_count", 0),
                "asset_dir": parsed.get("asset_dir", ""),
                "markdown_stats": parsed.get("markdown_stats", {}),
            }
        },
    }
    return markdown, "text/markdown", request.url, "feishu_export", extras


def _attach_feishu_export_comparison(result: FetchResult, request: FetchRequest) -> None:
    if not request.compare_feishu_export:
        return
    if not (is_feishu_doc_url(request.url) or is_feishu_doc_url(result.final_url)):
        return
    if result.transport == "feishu_export" or (result.site_data.get("feishu_export") if isinstance(result.site_data, dict) else None):
        return
    if not result.archive:
        result.errors.append("feishu_export_compare:archive_dir_required")
        return
    export_dir = ""
    assets_dir = ""
    export_child = archive_child_path(result.archive, "feishu_export", ".keep")
    if export_child:
        export_child.unlink(missing_ok=True)
        export_dir = str(export_child.parent)
    assets_child = archive_child_path(result.archive, "image_assets", ".keep")
    if assets_child:
        assets_child.unlink(missing_ok=True)
        assets_dir = str(assets_child.parent)
    try:
        exported = export_feishu_docx(request.url, output_dir=export_dir, timeout=max(request.timeout_seconds, 60))
        parsed = parse_docx_export(str(exported.get("docx_path") or ""), assets_dir=assets_dir)
    except FeishuExportUnavailable as exc:
        result.errors.append(f"feishu_export_compare:{str(exc)[:200]}")
        return
    except Exception as exc:
        result.errors.append(f"feishu_export_compare:{type(exc).__name__}:{str(exc)[:200]}")
        return
    export_data = {
        "doc_token": exported.get("doc_token", ""),
        "ticket": exported.get("ticket", ""),
        "file_token": exported.get("file_token", ""),
        "file_name": exported.get("file_name", ""),
        "docx_path": exported.get("docx_path", ""),
        "bytes": exported.get("bytes", 0),
        "image_count": parsed.get("image_count", 0),
        "markdown_stats": parsed.get("markdown_stats", {}),
        "text_chars": parsed.get("text_chars", 0),
        "heading_count": parsed.get("heading_count", 0),
        "table_count": parsed.get("table_count", 0),
        "mode": "comparison",
    }
    result.site_data = dict(result.site_data or {})
    result.site_data["feishu_export"] = export_data
    result.extracted_metadata = dict(result.extracted_metadata or {})
    result.extracted_metadata["feishu_export"] = {
        "docx_path": exported.get("docx_path", ""),
        "image_count": parsed.get("image_count", 0),
        "asset_dir": parsed.get("asset_dir", ""),
        "markdown_stats": parsed.get("markdown_stats", {}),
        "mode": "comparison",
    }
    export_assets = []
    for asset in parsed.get("image_assets") or []:
        item = dict(asset)
        item.setdefault("source", "feishu_export")
        item.setdefault("role", "docx_export_image")
        export_assets.append(item)
    if export_assets:
        existing_keys = {str(item.get("asset_sha256") or item.get("asset_path") or "") for item in result.image_assets}
        for item in export_assets:
            key = str(item.get("asset_sha256") or item.get("asset_path") or "")
            if key and key not in existing_keys:
                existing_keys.add(key)
                result.image_assets.append(item)


def xhs_redbook_cli_fetch(request: FetchRequest, archive: dict[str, Any] | None = None) -> tuple[str, str, str, str, dict[str, Any]]:
    raw = redbook_cli_fetch(request.url, chrome_profile=request.browser_profile, timeout=max(30, request.timeout_seconds))
    raw_path = write_raw_tool_result(archive, raw, name="xhs/redbook_raw.json")
    site_data = normalize_note(raw, source_url=request.url, source="redbook_cli")
    markdown = note_to_markdown(raw, source_url=request.url)
    if not markdown:
        raise RuntimeError("xhs_redbook_empty_markdown")
    extras = {
        "title": site_data.get("title") or "小红书笔记",
        "images": site_data.get("images") or [],
        "site_data": {"xhs": {**site_data, "raw_path": raw_path}},
        "extracted_metadata": {"xhs": {"backend": "redbook_cli", "raw_path": raw_path}},
    }
    return markdown, "text/markdown", request.url, "xhs_redbook_cli", extras


def xhs_browser_state_fetch(request: FetchRequest, archive: dict[str, Any] | None = None) -> tuple[str, str, str, str, dict[str, Any]]:
    raw, content_type, final_url, _transport, extras = browser_fetch(request, archive)
    note = extract_initial_state_note(raw, final_url or request.url)
    if not note:
        raise RuntimeError("xhs_initial_state_note_missing")
    raw_path = write_raw_tool_result(archive, note, name="xhs/browser_state_note.json")
    site_data = normalize_note(note, source_url=final_url or request.url, source="browser_initial_state")
    markdown = note_to_markdown(note, source_url=final_url or request.url)
    extras = dict(extras)
    extras.update(
        {
            "title": site_data.get("title") or extras.get("title") or "小红书笔记",
            "images": site_data.get("images") or [],
            "site_data": {"xhs": {**site_data, "raw_path": raw_path}},
            "extracted_metadata": {"xhs": {"backend": "browser_initial_state", "raw_path": raw_path}},
        }
    )
    return markdown, "text/markdown", final_url, "xhs_browser_state", extras


def xhs_auto_fetch(request: FetchRequest, archive: dict[str, Any] | None = None) -> tuple[str, str, str, str, dict[str, Any]]:
    errors: list[str] = []
    try:
        return xhs_redbook_cli_fetch(request, archive)
    except Exception as exc:
        errors.append(f"xhs_redbook_cli:{type(exc).__name__}:{str(exc)[:200]}")
    browser_request = replace(
        request,
        use_browser=True,
        browser_auto_scroll=True,
        browser_wait_until=request.browser_wait_until or "domcontentloaded",
        save_screenshot=True if request.archive_dir else request.save_screenshot,
    )
    try:
        raw, content_type, final_url, transport, extras = xhs_browser_state_fetch(browser_request, archive)
        extras = dict(extras)
        extras.setdefault("xhs_fallback_errors", errors)
        return raw, content_type, final_url, transport, extras
    except Exception as exc:
        errors.append(f"xhs_browser_state:{type(exc).__name__}:{str(exc)[:200]}")
        raise RuntimeError("; ".join(errors))


def _run_singlefile(request: FetchRequest, result: FetchResult) -> None:
    if not request.archive_dir or not result.archive:
        return
    output_path = archive_child_path(result.archive, "singlefile.html")
    if not output_path:
        return
    command = [
        "single-file",
        result.final_url,
        str(output_path),
    ]
    browser_args = []
    if request.browser_user_data_dir:
        browser_args.append(f"--user-data-dir={request.browser_user_data_dir}")
    if request.browser_profile:
        browser_args.append(f"--profile-directory={request.browser_profile}")
    if browser_args:
        command.extend(["--browser-args", json.dumps(browser_args)])
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=request.timeout_seconds + 20)
        if completed.returncode == 0 and output_path.exists():
            result.singlefile_path = str(output_path)
        else:
            error = (completed.stderr or completed.stdout or "singlefile_failed").strip()
            result.errors.append(f"singlefile:{error[:200]}")
    except FileNotFoundError:
        result.errors.append("singlefile:single-file command not found")
    except Exception as exc:
        result.errors.append(f"singlefile:{type(exc).__name__}:{str(exc)[:200]}")



def _run_monolith_snapshot(request: FetchRequest, result: FetchResult) -> None:
    if request.snapshot_backend != "monolith" or not request.archive_dir or not result.archive:
        return
    suffix = ".mhtml" if request.monolith_mhtml else ".html"
    output_path = archive_child_path(result.archive, f"monolith{suffix}")
    if output_path is None:
        return
    try:
        snapshot = run_monolith(
            MonolithRequest(
                html=result.raw_snapshot,
                base_url=result.final_url or result.requested_url,
                output_path=str(output_path),
                timeout_seconds=max(request.timeout_seconds, 30),
                executable=request.monolith_executable,
                mhtml=request.monolith_mhtml,
            )
        )
        result.site_data["monolith"] = snapshot
    except Exception as exc:
        result.errors.append(f"monolith:{type(exc).__name__}:{str(exc)[:300]}")
def _refresh_archive_metadata(result: FetchResult) -> None:
    if not result.archive:
        return
    metadata_path = result.archive.get("metadata_path")
    structured_path = result.archive.get("structured_path")
    meta = result.to_dict(include_raw=False)
    meta["evidence_archive"] = result.archive
    meta["archive_index"] = build_archive_index(result)
    if metadata_path:
        Path(str(metadata_path)).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    if structured_path:
        Path(str(structured_path)).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def _archive_link(path: str, label: str, description: str = "") -> str:
    if not path:
        return ""
    name = Path(path).name
    escaped_name = html_lib.escape(name, quote=True)
    escaped_label = html_lib.escape(label)
    escaped_description = html_lib.escape(description)
    description_html = f"<span>{escaped_description}</span>" if escaped_description else ""
    return f'<li><a href="{escaped_name}">{escaped_label}</a>{description_html}</li>'


def _markdown_image_urls(markdown: str) -> list[str]:
    urls: list[str] = []
    for match in MARKDOWN_IMAGE_RE.finditer(markdown or ""):
        _alt, url, _suffix = _markdown_image_match_parts(match)
        if url:
            urls.append(url)
    return urls


def _asset_markdown_path(result: FetchResult, asset: dict[str, Any]) -> str:
    markdown_path = str(asset.get("markdown_path") or "")
    if markdown_path:
        return markdown_path
    asset_path = Path(str(asset.get("asset_path") or ""))
    archive_root = Path(str((result.archive or {}).get("archive_path") or ""))
    if asset_path and asset_path.exists() and archive_root:
        try:
            return asset_path.relative_to(archive_root).as_posix()
        except ValueError:
            return asset_path.as_posix()
    return ""


def _image_gallery_groups(result: FetchResult) -> dict[str, Any]:
    extracted_refs = set(_markdown_image_urls(result.extracted_text))
    browser_refs = set(_markdown_image_urls(result.browser_visible_markdown))
    preferred_refs = set(_markdown_image_urls(_preferred_markdown_text_for_index(result)))
    groups: dict[str, list[dict[str, Any]]] = {
        "extracted_text": [],
        "browser_visible_only": [],
        "downloaded_only": [],
        "failed": [],
    }
    for asset in result.image_assets:
        item = dict(asset)
        markdown_path = _asset_markdown_path(result, item)
        if markdown_path:
            item["markdown_path"] = markdown_path
        if item.get("asset_error") or (item.get("asset_path") and not Path(str(item.get("asset_path"))).exists()):
            groups["failed"].append(item)
        elif markdown_path in extracted_refs or markdown_path in preferred_refs:
            groups["extracted_text"].append(item)
        elif markdown_path in browser_refs:
            groups["browser_visible_only"].append(item)
        else:
            groups["downloaded_only"].append(item)
    return {
        "path": (result.archive or {}).get("image_gallery_path", ""),
        "counts": {key: len(value) for key, value in groups.items()},
        "groups": groups,
    }


def _asset_completeness_check(
    result: FetchResult,
    *,
    gallery: dict[str, Any],
    markdown_index: dict[str, Any],
    browser_visible_index: dict[str, Any],
    completeness: dict[str, Any],
) -> dict[str, Any]:
    counts = dict(gallery.get("counts") or {})
    extracted_count = int(counts.get("extracted_text") or 0)
    visible_only_count = int(counts.get("browser_visible_only") or 0)
    downloaded_only_count = int(counts.get("downloaded_only") or 0)
    failed_count = int(counts.get("failed") or 0)
    downloaded_asset_count = len(result.image_assets or [])
    markdown_image_count = int(markdown_index.get("image_count") or 0)
    browser_visible_image_count = int(browser_visible_index.get("image_count") or 0)
    reasons: list[str] = []
    status = "ok"

    if downloaded_asset_count == 0:
        status = "no_downloaded_assets"
        if markdown_image_count or browser_visible_image_count:
            reasons.append("markdown references images but no downloaded image assets are indexed")
    if failed_count:
        status = "suspect_incomplete"
        reasons.append("one or more image assets failed to download")
    if visible_only_count:
        status = "suspect_incomplete"
        reasons.append("browser-visible images are missing from the main readable extraction")
    if markdown_image_count > downloaded_asset_count and downloaded_asset_count > 0:
        if status == "ok":
            status = "review_assets"
        reasons.append("markdown contains more image references than unique downloaded image assets; duplicates or missing assets need review")
    if downloaded_only_count:
        if not result.browser_visible_markdown:
            status = "unverified_assets" if status in {"ok", "no_downloaded_assets"} else status
            reasons.append("downloaded image assets are not referenced and browser-visible comparison is unavailable")
        elif status == "ok":
            status = "review_assets"
            reasons.append("downloaded image assets are not referenced by readable outputs")
    if completeness.get("status") == "suspect_incomplete" and status in {"ok", "review_assets", "no_downloaded_assets"}:
        status = "suspect_incomplete"
        reasons.append("structural completeness check already flagged missing browser-visible content")

    return {
        "status": status,
        "reasons": list(dict.fromkeys(reasons)),
        "downloaded_asset_count": downloaded_asset_count,
        "markdown_image_count": markdown_image_count,
        "browser_visible_image_count": browser_visible_image_count,
        "gallery_counts": counts,
        "extracted_text_count": extracted_count,
        "browser_visible_only_count": visible_only_count,
        "downloaded_only_count": downloaded_only_count,
        "failed_count": failed_count,
    }


def _write_image_gallery(result: FetchResult) -> None:
    if not result.archive or not result.image_assets:
        return
    path = archive_child_path(result.archive, "image_gallery.html")
    if not path:
        return
    gallery = _image_gallery_groups(result)

    def render_group(key: str, label: str, description: str) -> str:
        items = gallery["groups"].get(key, [])
        cards: list[str] = []
        for index, asset in enumerate(items, start=1):
            src = str(asset.get("markdown_path") or "")
            url = str(asset.get("url") or "")
            alt = str(asset.get("alt") or asset.get("title") or "")
            size = " x ".join(str(asset.get(name) or "") for name in ("width", "height")).strip(" x")
            byte_count = asset.get("bytes")
            error = str(asset.get("asset_error") or "")
            image_html = f'<img src="{html_lib.escape(src, quote=True)}" alt="{html_lib.escape(alt, quote=True)}">' if src and not error else ""
            meta_parts = [part for part in [size, f"{byte_count} bytes" if byte_count else "", error] if part]
            meta_html = " / ".join(html_lib.escape(str(part)) for part in meta_parts)
            cards.append(
                "\n".join(
                    [
                        '<article class="card">',
                        image_html,
                        f"<h3>#{index} {html_lib.escape(alt or Path(src).name or 'image')}</h3>",
                        f'<p class="meta">{meta_html}</p>' if meta_html else "",
                        f'<p class="path">{html_lib.escape(src)}</p>' if src else "",
                        f'<p class="url">{html_lib.escape(url)}</p>' if url else "",
                        "</article>",
                    ]
                )
            )
        body = "\n".join(cards) if cards else '<p class="empty">No images in this group.</p>'
        return "\n".join(
            [
                f'<section id="{html_lib.escape(key, quote=True)}">',
                f"<h2>{html_lib.escape(label)} <small>{len(items)}</small></h2>",
                f"<p>{html_lib.escape(description)}</p>",
                '<div class="grid">',
                body,
                "</div>",
                "</section>",
            ]
        )

    title = result.title or "Image gallery"
    sections = [
        render_group("extracted_text", "Extracted Text Images", "Images referenced by the main readable extraction."),
        render_group("browser_visible_only", "Browser Visible Only", "Images seen by the browser-visible layer but not included in the main readable extraction."),
        render_group("downloaded_only", "Downloaded Only", "Downloaded image assets not referenced by current Markdown outputs; often avatars, decoration, lazy UI, or candidates for review."),
        render_group("failed", "Failed", "Image references that could not be downloaded or are missing from disk."),
    ]
    html = "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{html_lib.escape(title)} - image gallery</title>",
            "<style>",
            "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.55;margin:28px auto;padding:0 20px;max-width:1180px;color:#202124;background:#fff;}",
            "h1{font-size:1.7rem;margin:0 0 8px;}h2{font-size:1.2rem;margin:34px 0 4px;}small{color:#57606a;font-weight:500;}",
            ".summary{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 24px;}.summary a{border:1px solid #d0d7de;border-radius:6px;padding:7px 10px;color:#0969da;text-decoration:none;}",
            ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;align-items:start;}",
            ".card{border:1px solid #d0d7de;border-radius:6px;padding:10px;overflow:hidden;background:#fff;}.card img{display:block;max-width:100%;height:auto;margin:0 auto 8px;}",
            ".card h3{font-size:.95rem;line-height:1.35;margin:0 0 4px;}.meta,.path,.url,.empty{font-size:.82rem;color:#57606a;word-break:break-all;margin:4px 0;}",
            "</style>",
            "</head>",
            "<body>",
            f"<h1>{html_lib.escape(title)} - image gallery</h1>",
            '<nav class="summary">',
            *(f'<a href="#{html_lib.escape(key, quote=True)}">{html_lib.escape(key)}: {count}</a>' for key, count in gallery["counts"].items()),
            "</nav>",
            *sections,
            "</body>",
            "</html>",
            "",
        ]
    )
    path.write_text(html, encoding="utf-8", newline="\n")
    result.archive["image_gallery_path"] = str(path)
    result.archive["image_gallery_sha256"] = sha256_text(html)
    result.archive["image_gallery_counts"] = gallery["counts"]


def _write_archive_home(result: FetchResult) -> None:
    if not result.archive:
        return
    path = archive_child_path(result.archive, "index.html")
    if not path:
        return
    archive = result.archive
    preferred = _preferred_readable_artifact(
        result,
        completeness=_completeness_check(
            extracted_text=result.extracted_text,
            extracted_index=markdown_structure_index(result.extracted_text),
            browser_visible_markdown=result.browser_visible_markdown,
            browser_visible_index=markdown_structure_index(result.browser_visible_markdown),
        ),
        markdown_index=markdown_structure_index(result.extracted_text),
        browser_visible_markdown_index=markdown_structure_index(result.browser_visible_markdown),
        preferred_markdown_index=markdown_structure_index(_preferred_markdown_text_for_index(result)),
    )
    archive_index = build_archive_index(result)
    feishu_comparison = archive_index.get("feishu_comparison") or {}
    feishu_html = ""
    if feishu_comparison:
        feishu_reasons = "; ".join(str(item) for item in feishu_comparison.get("reasons", []))
        feishu_html = (
            '<section class="status-card">'
            "<h2>Feishu/Lark export comparison</h2>"
            f"<p><strong>Status:</strong> {html_lib.escape(str(feishu_comparison.get('status') or ''))}</p>"
            f"<p><strong>DOCX:</strong> <code>{html_lib.escape(str(feishu_comparison.get('docx_path') or ''))}</code></p>"
            f"<p><strong>Reasons:</strong> {html_lib.escape(feishu_reasons)}</p>"
            "</section>"
        )
    links = [
        _archive_link(str(preferred.get("html_path") or ""), "Open recommended HTML", str(preferred.get("reason") or "")),
        _archive_link(str(preferred.get("markdown_path") or ""), "Open recommended Markdown", "best current text artifact for human reading and AI review"),
        _archive_link(archive.get("reading_report_path", ""), "reading_report.md", "quick reliability report for human review"),
        _archive_link(result.preferred_markdown_path or archive.get("preferred_markdown_path", ""), "preferred_markdown.md"),
        _archive_link(result.browser_visible_html_path, "browser_visible.html", "visible DOM rendered as readable HTML"),
        _archive_link(result.browser_visible_path, "browser_visible.md", "visible DOM rendered as Markdown"),
        _archive_link(archive.get("readable_html_path", ""), "readable.html", "extracted text rendered as readable HTML"),
        _archive_link(archive.get("image_gallery_path", ""), "image_gallery.html", "all downloaded image assets grouped by whether they appear in readable outputs"),
        _archive_link(archive.get("extracted_text_path", ""), "extracted_text.md"),
        _archive_link(result.page_html_path or archive.get("raw_snapshot_path", ""), "page.html / raw_snapshot.html", "raw page snapshot, may depend on scripts"),
        _archive_link(result.screenshot_path, "screenshot.png"),
        _archive_link(archive.get("structured_path", ""), "structured.json", "machine-readable archive index"),
    ]
    link_html = "\n".join(link for link in links if link)
    title = result.title or result.final_url or result.requested_url or "Archive"
    html = "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{html_lib.escape(title)} - archive</title>",
            "<style>",
            "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.65;max-width:880px;margin:32px auto;padding:0 20px;color:#202124;background:#fff;}",
            "h1{font-size:1.6rem;line-height:1.25;margin:0 0 10px;}",
            ".url{color:#57606a;word-break:break-all;margin-bottom:24px;}",
            ".status-card{border:1px solid #d0d7de;border-radius:6px;padding:12px 14px;margin:18px 0;background:#f6f8fa;}",
            ".status-card h2{font-size:1.05rem;margin:0 0 6px;}",
            ".status-card p{margin:4px 0;}",
            "code{word-break:break-all;}",
            "ul{padding-left:1.2rem;}",
            "li{margin:10px 0;}",
            "a{color:#0969da;font-weight:600;text-decoration:none;}",
            "a:hover{text-decoration:underline;}",
            "span{display:block;color:#57606a;font-size:.92rem;margin-top:2px;}",
            "</style>",
            "</head>",
            "<body>",
            f"<h1>{html_lib.escape(title)}</h1>",
            f'<div class="url">{html_lib.escape(result.final_url or result.requested_url)}</div>',
            feishu_html,
            "<ul>",
            link_html,
            "</ul>",
            "</body>",
            "</html>",
            "",
        ]
    )
    path.write_text(html, encoding="utf-8", newline="\n")
    result.archive["archive_home_path"] = str(path)
    result.archive["archive_home_sha256"] = sha256_text(html)


MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*(?:<([^>]*)>|([^)\s]+))([^)]*)\)")
MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
MARKDOWN_LIST_RE = re.compile(r"^\s*[-*+]\s+(.+?)\s*$")


def _markdown_image_match_parts(match: re.Match[str]) -> tuple[str, str, str]:
    alt = match.group(1)
    url = match.group(2) or match.group(3) or ""
    suffix = match.group(4) or ""
    return alt, url, suffix


def _normalize_url_for_match(value: str) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.sort()
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            urllib.parse.urlencode(query, doseq=True),
            "",
        )
    )


def _markdown_inline_to_html(text: str) -> str:
    parts: list[str] = []
    last = 0

    def replace_image(match: re.Match[str]) -> str:
        alt, url, suffix = _markdown_image_match_parts(match)
        title = html_lib.unescape(suffix.strip()).strip('"\'')
        title_attr = f' title="{html_lib.escape(title, quote=True)}"' if title else ""
        return f'<img src="{html_lib.escape(url, quote=True)}" alt="{html_lib.escape(alt, quote=True)}"{title_attr}>'

    for match in MARKDOWN_IMAGE_RE.finditer(text):
        parts.append(html_lib.escape(text[last : match.start()]))
        parts.append(replace_image(match))
        last = match.end()
    parts.append(html_lib.escape(text[last:]))
    escaped = "".join(parts)
    return re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)([^)]*)\)",
        lambda match: (
            f'<a href="{html_lib.escape(match.group(2), quote=True)}">'
            f"{match.group(1)}</a>"
        ),
        escaped,
    )


def _fallback_markdown_to_html_body(markdown: str) -> str:
    lines = (markdown or "").splitlines()
    body: list[str] = []
    paragraph: list[str] = []
    in_code = False
    in_list = False

    def flush_paragraph() -> None:
        nonlocal paragraph
        if not paragraph:
            return
        text = " ".join(item.strip() for item in paragraph if item.strip())
        if text:
            body.append(f"<p>{_markdown_inline_to_html(text)}</p>")
        paragraph = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            body.append("</ul>")
            in_list = False

    for line in lines:
        stripped = line.rstrip()
        if stripped.startswith("```"):
            flush_paragraph()
            close_list()
            if in_code:
                body.append("</code></pre>")
                in_code = False
            else:
                body.append("<pre><code>")
                in_code = True
            continue
        if in_code:
            body.append(html_lib.escape(stripped))
            continue
        if not stripped.strip():
            flush_paragraph()
            close_list()
            continue
        heading = MARKDOWN_HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            body.append(f"<h{level}>{_markdown_inline_to_html(heading.group(2))}</h{level}>")
            continue
        list_item = MARKDOWN_LIST_RE.match(stripped)
        if list_item:
            flush_paragraph()
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{_markdown_inline_to_html(list_item.group(1))}</li>")
            continue
        if stripped.lstrip().startswith(">"):
            flush_paragraph()
            close_list()
            quote = stripped.lstrip()[1:].strip()
            body.append(f"<blockquote>{_markdown_inline_to_html(quote)}</blockquote>")
            continue
        paragraph.append(stripped)

    flush_paragraph()
    close_list()
    if in_code:
        body.append("</code></pre>")
    return "\n".join(body)


def _markdown_to_html_body(markdown: str) -> tuple[str, str]:
    try:
        from markdown_it import MarkdownIt

        return MarkdownIt("commonmark", {"html": False}).enable("table").render(markdown or ""), "markdown-it-py"
    except Exception:
        return _fallback_markdown_to_html_body(markdown), "builtin"


def _wrap_readable_html(body_html: str, *, title: str = "") -> str:
    title_text = title or "Readable archive"
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{html_lib.escape(title_text)}</title>",
            "<style>",
            "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.75;max-width:880px;margin:32px auto;padding:0 20px;color:#202124;background:#fff;}",
            "img{max-width:100%;height:auto;display:block;margin:18px 0;}",
            "pre{overflow:auto;background:#f6f8fa;padding:14px;border-radius:6px;}",
            "blockquote{border-left:4px solid #d0d7de;margin:16px 0;padding:2px 16px;color:#57606a;}",
            "table{border-collapse:collapse;width:100%;margin:18px 0;display:block;overflow-x:auto;}",
            "th,td{border:1px solid #d0d7de;padding:6px 10px;text-align:left;vertical-align:top;}",
            "th{background:#f6f8fa;font-weight:600;}",
            "a{color:#0969da;}",
            ".wcf-summary{border:1px solid #d0d7de;background:#f6f8fa;padding:14px 16px;border-radius:8px;margin:0 0 24px;}",
            ".wcf-summary h1{font-size:1.25rem;margin:0 0 10px;}",
            ".wcf-summary dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 14px;margin:0;}",
            ".wcf-summary dt{font-weight:700;color:#57606a;}",
            ".wcf-summary dd{margin:0;word-break:break-word;}",
            ".wcf-warning{border-left:4px solid #d1242f;background:#fff8f8;padding:8px 12px;margin:12px 0;}",
            "</style>",
            "</head>",
            "<body>",
            body_html,
            "</body>",
            "</html>",
            "",
        ]
    )


def _render_readable_html(markdown: str, *, title: str = "") -> tuple[str, str]:
    body_html, renderer = _markdown_to_html_body(markdown)
    return _wrap_readable_html(body_html, title=title), renderer


def _markdown_table_count(markdown: str) -> int:
    count = 0
    lines = str(markdown or "").splitlines()
    for index, line in enumerate(lines[1:], start=1):
        previous = lines[index - 1].strip()
        stripped = line.strip()
        if "|" in previous and "|" in stripped and set(stripped.replace("|", "").replace(":", "").replace("-", "").strip()) <= set():
            count += 1
    return count


def _preferred_readable_summary_html(result: FetchResult, markdown: str) -> str:
    markdown_index = markdown_structure_index(markdown)
    quality = result.extraction_quality or {}
    site_strategy = result.site_data.get("site_strategy", {}) if isinstance(result.site_data, dict) else {}
    site_key = str(site_strategy.get("site_key") or "generic")
    strategy_kind = str(site_strategy.get("kind") or "")
    action = "manual_review"
    if quality.get("status") == "ok":
        action = "read_preferred_readable"
    if result.archive and result.archive.get("reading_report_path"):
        action = "open_reading_report_when_in_doubt"
    capture = result.capture_completeness
    manual = capture.get("manual_capture") or {}
    if manual:
        action = str(manual.get("action"))
    warning = ""
    if quality.get("status") and quality.get("status") != "ok":
        warning = f'<div class="wcf-warning">Content may be incomplete: {html_lib.escape(str(quality.get("status")))}</div>'
    items = [
        ("Source URL", result.final_url or result.requested_url),
        ("Transport", result.transport),
        ("Extractor", result.extraction_engine),
        ("Site strategy", f"{site_key} / {strategy_kind}".strip(" /")),
        ("Quality", str(quality.get("status") or "unknown")),
        ("Text chars", str(len(markdown or ""))),
        ("Headings", str(markdown_index.get("heading_count", 0))),
        ("Images", str(markdown_index.get("image_count", 0))),
        ("Tables", str(_markdown_table_count(markdown))),
        ("Attachments", str(len(result.site_data.get("attachments", []) if isinstance(result.site_data, dict) else []))),
        ("Recommended action", action),
        ("Capture completeness", str(capture.get("status", "unknown"))),
        ("Original images", str((capture.get("body_images") or {}).get("original_availability", "unknown"))),
        ("Supplement material", str(manual.get("instructions", ""))),
        ("Manual fallback", str(site_strategy.get("recommended_manual_fallback") or "none")),
    ]
    dl = "".join(f"<dt>{html_lib.escape(key)}</dt><dd>{html_lib.escape(value)}</dd>" for key, value in items)
    return f'<section class="wcf-summary"><h1>Archive Reading Summary</h1>{warning}<dl>{dl}</dl></section>'


def markdown_to_readable_html(markdown: str, *, title: str = "") -> str:
    html, _renderer = _render_readable_html(markdown, title=title)
    return html


def _write_readable_html(result: FetchResult) -> None:
    if not result.archive or not result.extracted_text:
        return
    path = archive_child_path(result.archive, "readable.html")
    if not path:
        return
    html, renderer = _render_readable_html(result.extracted_text, title=result.title)
    path.write_text(html, encoding="utf-8", newline="\n")
    result.archive["readable_html_path"] = str(path)
    result.archive["readable_html_sha256"] = sha256_text(html)
    result.archive["readable_html_renderer"] = renderer


def _write_browser_visible_markdown(result: FetchResult) -> None:
    if not result.archive or not result.browser_visible_markdown:
        return
    path = archive_child_path(result.archive, "browser_visible.md")
    if not path:
        return
    path.write_text(result.browser_visible_markdown, encoding="utf-8", newline="\n")
    result.browser_visible_path = str(path)
    result.archive["browser_visible_path"] = str(path)
    result.archive["browser_visible_sha256"] = sha256_text(result.browser_visible_markdown)


def _write_browser_visible_html(result: FetchResult) -> None:
    if not result.archive or not result.browser_visible_markdown:
        return
    path = archive_child_path(result.archive, "browser_visible.html")
    if not path:
        return
    html, renderer = _render_readable_html(result.browser_visible_markdown, title=f"{result.title or 'Browser visible'} - Browser visible")
    path.write_text(html, encoding="utf-8", newline="\n")
    result.browser_visible_html_path = str(path)
    result.archive["browser_visible_html_path"] = str(path)
    result.archive["browser_visible_html_sha256"] = sha256_text(html)
    result.archive["browser_visible_html_renderer"] = renderer


def _write_crawl4ai_artifacts(result: FetchResult, extras: dict[str, Any]) -> None:
    if not result.archive:
        return
    crawl_data = dict(extras.get("crawl4ai") or {})
    if not crawl_data:
        return
    crawl_index = result.site_data.setdefault("crawl4ai", {})
    metadata_index = result.extracted_metadata.setdefault("crawl4ai", {})
    markdown = str(crawl_data.get("markdown") or "")
    cleaned_html = str(crawl_data.get("cleaned_html") or "")
    html = str(crawl_data.get("html") or "")
    if markdown:
        path = archive_child_path(result.archive, "crawl4ai", "markdown.md")
        if path:
            path.write_text(markdown, encoding="utf-8", newline="\n")
            crawl_index["markdown_path"] = str(path)
            crawl_index["markdown_sha256"] = sha256_text(markdown)
            metadata_index["markdown_path"] = str(path)
    if cleaned_html:
        path = archive_child_path(result.archive, "crawl4ai", "cleaned.html")
        if path:
            path.write_text(cleaned_html, encoding="utf-8", newline="\n")
            crawl_index["cleaned_html_path"] = str(path)
            crawl_index["cleaned_html_sha256"] = sha256_text(cleaned_html)
            metadata_index["cleaned_html_path"] = str(path)
    if html and html != cleaned_html:
        path = archive_child_path(result.archive, "crawl4ai", "page.html")
        if path:
            path.write_text(html, encoding="utf-8", newline="\n")
            crawl_index["html_path"] = str(path)
            crawl_index["html_sha256"] = sha256_text(html)
            metadata_index["html_path"] = str(path)
    screenshot_bytes = crawl_data.get("screenshot_bytes") or b""
    if screenshot_bytes:
        path = archive_child_path(result.archive, "crawl4ai", "screenshot.png")
        if path:
            path.write_bytes(screenshot_bytes)
            crawl_index["screenshot_path"] = str(path)
            metadata_index["screenshot_path"] = str(path)
    pdf_bytes = crawl_data.get("pdf_bytes") or b""
    if pdf_bytes:
        path = archive_child_path(result.archive, "crawl4ai", "page.pdf")
        if path:
            path.write_bytes(pdf_bytes)
            crawl_index["pdf_path"] = str(path)
            metadata_index["pdf_path"] = str(path)
    result_json = {
        "success": crawl_data.get("success", True),
        "error_message": crawl_data.get("error_message", ""),
        "url": crawl_data.get("url", result.final_url),
        "config": crawl_data.get("config") or {},
        "metadata": crawl_data.get("metadata") or {},
        "links": crawl_data.get("links") or {},
        "media": crawl_data.get("media") or {},
        "markdown_chars": len(markdown),
        "html_chars": len(html),
        "cleaned_html_chars": len(cleaned_html),
        "has_screenshot": bool(screenshot_bytes),
        "has_pdf": bool(pdf_bytes),
    }
    path = archive_child_path(result.archive, "crawl4ai", "result.json")
    if path:
        path.write_text(json.dumps(result_json, ensure_ascii=False, indent=2), encoding="utf-8")
        crawl_index["result_json_path"] = str(path)
        metadata_index["result_json_path"] = str(path)



def _write_firecrawl_artifacts(result: FetchResult, extras: dict[str, Any]) -> None:
    if not result.archive:
        return
    fire_data = dict(extras.get("firecrawl") or {})
    if not fire_data:
        return
    fire_index = result.site_data.setdefault("firecrawl", {})
    metadata_index = result.extracted_metadata.setdefault("firecrawl", {})
    markdown = str(fire_data.get("markdown") or "")
    html = str(fire_data.get("html") or "")
    if markdown:
        path = archive_child_path(result.archive, "firecrawl", "markdown.md")
        if path:
            path.write_text(markdown, encoding="utf-8", newline="\n")
            fire_index["markdown_path"] = str(path)
            fire_index["markdown_sha256"] = sha256_text(markdown)
            metadata_index["markdown_path"] = str(path)
    if html:
        path = archive_child_path(result.archive, "firecrawl", "page.html")
        if path:
            path.write_text(html, encoding="utf-8", newline="\n")
            fire_index["html_path"] = str(path)
            fire_index["html_sha256"] = sha256_text(html)
            metadata_index["html_path"] = str(path)
    screenshot_bytes = fire_data.get("screenshot_bytes") or b""
    if screenshot_bytes:
        path = archive_child_path(result.archive, "firecrawl", "screenshot.png")
        if path:
            path.write_bytes(screenshot_bytes)
            fire_index["screenshot_path"] = str(path)
            metadata_index["screenshot_path"] = str(path)
    result_json = {
        "success": fire_data.get("success", True),
        "url": fire_data.get("url", result.final_url),
        "config": fire_data.get("config") or {},
        "metadata": fire_data.get("metadata") or {},
        "links": fire_data.get("links") or [],
        "markdown_chars": len(markdown),
        "html_chars": len(html),
        "has_screenshot": bool(screenshot_bytes),
        "raw_response": fire_data.get("raw_response") or {},
    }
    path = archive_child_path(result.archive, "firecrawl", "result.json")
    if path:
        path.write_text(json.dumps(result_json, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        fire_index["result_json_path"] = str(path)
        metadata_index["result_json_path"] = str(path)


def _write_scrapling_artifacts(result: FetchResult, extras: dict[str, Any]) -> None:
    if not result.archive:
        return
    scrap_data = dict(extras.get("scrapling") or {})
    if not scrap_data:
        return
    scrap_index = result.site_data.setdefault("scrapling", {})
    metadata_index = result.extracted_metadata.setdefault("scrapling", {})
    html = str(scrap_data.get("html") or "")
    text = str(scrap_data.get("text") or "")
    if html:
        path = archive_child_path(result.archive, "scrapling", "page.html")
        if path:
            path.write_text(html, encoding="utf-8", newline="\n")
            scrap_index["html_path"] = str(path)
            scrap_index["html_sha256"] = sha256_text(html)
            metadata_index["html_path"] = str(path)
    if text:
        path = archive_child_path(result.archive, "scrapling", "selector_text.txt")
        if path:
            path.write_text(text, encoding="utf-8", newline="\n")
            scrap_index["selector_text_path"] = str(path)
            scrap_index["selector_text_sha256"] = sha256_text(text)
            metadata_index["selector_text_path"] = str(path)
    result_json = {
        "success": scrap_data.get("success", True),
        "url": scrap_data.get("url", result.final_url),
        "status": scrap_data.get("status", 0),
        "reason": scrap_data.get("reason", ""),
        "config": scrap_data.get("config") or {},
        "links": scrap_data.get("links") or [],
        "images": scrap_data.get("images") or [],
        "html_chars": len(html),
        "text_chars": len(text),
    }
    path = archive_child_path(result.archive, "scrapling", "result.json")
    if path:
        path.write_text(json.dumps(result_json, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        scrap_index["result_json_path"] = str(path)
        metadata_index["result_json_path"] = str(path)


def _preferred_markdown_source(result: FetchResult) -> tuple[str, str, str]:
    if result.status == "access_blocked":
        return "", "access_blocked", "manual_capture_required"
    firecrawl = result.site_data.get("firecrawl", {}) if isinstance(result.site_data, dict) else {}
    firecrawl_markdown_path = str(firecrawl.get("markdown_path") or "")
    if firecrawl_markdown_path:
        try:
            path = Path(firecrawl_markdown_path)
            if path.exists():
                markdown = path.read_text(encoding="utf-8")
                if markdown.strip():
                    return markdown, "firecrawl", "Firecrawl markdown is available for this explicitly selected backend"
        except Exception:
            pass
    crawl4ai = result.site_data.get("crawl4ai", {}) if isinstance(result.site_data, dict) else {}
    crawl_markdown_path = str(crawl4ai.get("markdown_path") or "")
    if crawl_markdown_path:
        try:
            path = Path(crawl_markdown_path)
            if path.exists():
                markdown = path.read_text(encoding="utf-8")
                if markdown.strip():
                    return markdown, "crawl4ai", "Crawl4AI markdown is available for this backend"
        except Exception:
            pass
    if not result.browser_visible_markdown:
        return result.extracted_text or "", "extracted_text", "no browser-visible markdown available"
    markdown_index = markdown_structure_index(result.extracted_text)
    browser_visible_index = markdown_structure_index(result.browser_visible_markdown)
    completeness = _completeness_check(
        extracted_text=result.extracted_text,
        extracted_index=markdown_index,
        browser_visible_markdown=result.browser_visible_markdown,
        browser_visible_index=browser_visible_index,
    )
    if result.extraction_engine == "browser_visible_dom" or (
        completeness.get("status") == "suspect_incomplete"
        and _browser_visible_can_replace_extracted(result.browser_visible_markdown, result.extracted_text)
    ):
        return result.browser_visible_markdown, "browser_visible", "browser-visible markdown is more complete or was used as extracted text"
    return result.extracted_text or "", "extracted_text", "standard extracted markdown passed completeness checks"


def _write_preferred_markdown(result: FetchResult) -> None:
    if not result.archive:
        return
    markdown, source, reason = _preferred_markdown_source(result)
    if not markdown:
        return
    path = archive_child_path(result.archive, "preferred_markdown.md")
    if not path:
        return
    path.write_text(markdown, encoding="utf-8", newline="\n")
    result.preferred_markdown_path = str(path)
    result.archive["preferred_markdown_path"] = str(path)
    result.archive["preferred_markdown_sha256"] = sha256_text(markdown)
    result.archive["preferred_markdown_source"] = source
    result.archive["preferred_markdown_reason"] = reason


def _preferred_markdown_text_for_index(result: FetchResult) -> str:
    archive = result.archive or {}
    preferred_path = result.preferred_markdown_path or archive.get("preferred_markdown_path", "")
    if preferred_path:
        try:
            path = Path(str(preferred_path))
            if path.exists():
                return path.read_text(encoding="utf-8")
        except Exception:
            pass
    markdown, _source, _reason = _preferred_markdown_source(result)
    return markdown or ""


def _write_preferred_readable_html(result: FetchResult) -> None:
    if not result.archive:
        return
    markdown = _preferred_markdown_text_for_index(result)
    if not markdown and not result.capture_completeness.get("manual_capture"):
        return
    path = archive_child_path(result.archive, "preferred_readable.html")
    if not path:
        return
    body_html, renderer = _markdown_to_html_body(markdown)
    html = _wrap_readable_html(
        _preferred_readable_summary_html(result, markdown) + "\n" + body_html,
        title=f"{result.title or 'Preferred readable'} - Preferred",
    )
    path.write_text(html, encoding="utf-8", newline="\n")
    result.archive["preferred_readable_html_path"] = str(path)
    result.archive["preferred_readable_html_sha256"] = sha256_text(html)
    result.archive["preferred_readable_html_renderer"] = renderer


def _rewrite_markdown_image_links(result: FetchResult) -> None:
    if not result.archive or not result.image_assets:
        return
    assets_by_url = _assets_by_normalized_url(result)
    if not assets_by_url:
        return

    def replace(match: re.Match[str]) -> str:
        alt, url, suffix = _markdown_image_match_parts(match)
        asset = assets_by_url.get(_normalize_url_for_match(url))
        if not asset:
            return match.group(0)
        return f"![{alt}]({asset['markdown_path']}{suffix})"

    if result.extracted_text:
        rewritten = MARKDOWN_IMAGE_RE.sub(replace, result.extracted_text)
        if rewritten != result.extracted_text:
            result.extracted_text = rewritten
            result.extracted_sha256 = sha256_text(rewritten)
            extracted_path = result.archive.get("extracted_text_path")
            if extracted_path:
                Path(str(extracted_path)).write_text(rewritten, encoding="utf-8", newline="\n")
                result.archive["extracted_text_sha256"] = result.extracted_sha256
                result.archive["archive_hash"] = sha256_text(
                    json.dumps(
                        {
                            "url": result.final_url,
                            "title": result.title,
                            "raw_snapshot_sha256": result.raw_sha256,
                            "extracted_text_sha256": result.extracted_sha256,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
    if result.browser_visible_markdown:
        result.browser_visible_markdown = MARKDOWN_IMAGE_RE.sub(replace, result.browser_visible_markdown)
    _rewrite_browser_visible_block_images(result, assets_by_url)


def _assets_by_normalized_url(result: FetchResult) -> dict[str, dict[str, Any]]:
    archive_root = Path(str((result.archive or {}).get("archive_path") or ""))
    if not archive_root:
        return {}
    assets_by_url: dict[str, dict[str, Any]] = {}
    for asset in result.image_assets:
        url = _normalize_url_for_match(str(asset.get("url") or ""))
        path = Path(str(asset.get("asset_path") or ""))
        if not url or not path.exists():
            continue
        relative = path.relative_to(archive_root).as_posix() if path.is_relative_to(archive_root) else path.as_posix()
        asset["markdown_path"] = relative
        assets_by_url[url] = asset
    return assets_by_url


def _rewrite_browser_visible_block_images(result: FetchResult, assets_by_url: dict[str, dict[str, Any]]) -> None:
    if not result.browser_visible_blocks:
        return
    for block in result.browser_visible_blocks:
        src = str(block.get("src") or "")
        asset = assets_by_url.get(_normalize_url_for_match(src))
        if asset:
            block["src"] = asset.get("markdown_path", src)
        text = str(block.get("text") or "")
        if text:
            block["text"] = MARKDOWN_IMAGE_RE.sub(lambda match: _replace_image_match_with_asset(match, assets_by_url), text)
        for key in ("inline_items", "image_positions"):
            items = block.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_src = str(item.get("src") or "")
                item_asset = assets_by_url.get(_normalize_url_for_match(item_src))
                if item_asset:
                    item["src"] = item_asset.get("markdown_path", item_src)
                item_text = str(item.get("text") or "")
                if item_text:
                    item["text"] = MARKDOWN_IMAGE_RE.sub(lambda match: _replace_image_match_with_asset(match, assets_by_url), item_text)


def _replace_image_match_with_asset(match: re.Match[str], assets_by_url: dict[str, dict[str, Any]]) -> str:
    alt, url, suffix = _markdown_image_match_parts(match)
    asset = assets_by_url.get(_normalize_url_for_match(url))
    if not asset:
        return match.group(0)
    return f"![{alt}]({asset['markdown_path']}{suffix})"


def _quality_needs_browser_fallback(result: FetchResult) -> bool:
    # Content validity is independent of transport success in result schema v5.
    if result.status not in {"ok", "partial"} or result.transport_ok is False:
        return False
    if result.payload.get("binary") or result.payload.get("truncated"):
        return False
    if result.extraction_quality.get("access_blocked") or result.extraction_quality.get("document_identity_mismatch"):
        return False
    quality_status = str((result.extraction_quality or {}).get("status") or "")
    return quality_status in {"empty", "too_short", "suspect_shell", "suspect_incomplete"}


def _browser_result_has_preservable_evidence(result: FetchResult) -> bool:
    if result.transport != "browser":
        return False
    return bool(
        result.image_assets
        or result.browser_visible_blocks
        or len(" ".join((result.extracted_text or "").split())) >= 500
    )


def _is_feishu_reader_fallback_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url or "")
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if not ("feishu.cn" in host or "larksuite.com" in host):
        return False
    return any(marker in path for marker in ("/doc/", "/docx/", "/wiki/"))


def _jina_reader_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https":
        return f"https://r.jina.ai/http://{url[len('https://'):]}"
    if parsed.scheme == "http":
        return f"https://r.jina.ai/http://{url[len('http://'):]}"
    return f"https://r.jina.ai/http://{url}"


def jina_reader_fetch(request: FetchRequest) -> tuple[str, str, str, str]:
    reader_url = _jina_reader_url(request.url)
    reader_request = replace(
        request,
        url=reader_url,
        max_bytes=max(request.max_bytes, 5_000_000),
    )
    raw, content_type, _final_url = direct_fetch(reader_request)
    if len(" ".join(raw.split())) < 120:
        raise RuntimeError("jina_reader_empty_or_too_short")
    return raw, content_type or "text/markdown", request.url, "jina_reader"


def _try_jina_reader_fallback(request: FetchRequest, errors: list[str]) -> FetchResult | None:
    if not _is_feishu_reader_fallback_url(request.url):
        return None
    try:
        raw, content_type, final_url, transport = jina_reader_fetch(request)
        return structure_content(request, raw, content_type, final_url, transport, [*errors, "fallback:jina_reader_after_feishu_low_quality"])
    except Exception as exc:
        errors.append(f"jina_reader_fallback:{type(exc).__name__}:{str(exc)[:200]}")
        return None


def _auto_firecrawl_block_reason(request: FetchRequest, result: FetchResult | None = None) -> str:
    if request.backend != "auto":
        return "backend_not_auto"
    key_env = request.firecrawl_api_key_env or "FIRECRAWL_API_KEY"
    if not (os.getenv(key_env) or ""):
        return f"missing_firecrawl_api_key_env:{key_env}"
    parsed = urllib.parse.urlparse(request.url or "")
    host = parsed.netloc.lower()
    sensitive_hosts = (
        "feishu.cn",
        "larksuite.com",
        "zsxq.com",
        "xiaohongshu.com",
        "xhslink.com",
        "notion.so",
        "yuque.com",
        "kdocs.cn",
        "docs.qq.com",
    )
    if any(marker in host for marker in sensitive_hosts):
        return f"sensitive_host:{host}"
    if result:
        quality = result.extraction_quality or {}
        if quality.get("login_redirect_detected"):
            return "login_redirect_detected"
        if any("login_redirect" in str(error) for error in result.errors):
            return "login_redirect_detected"
    return ""


def _try_auto_firecrawl_fallback(request: FetchRequest, errors: list[str], result: FetchResult | None = None) -> FetchResult | None:
    block_reason = _auto_firecrawl_block_reason(request, result)
    if block_reason:
        note = f"firecrawl_auto_fallback_skipped:{block_reason}"
        errors.append(note)
        if result is not None and note not in result.errors:
            result.errors = [*result.errors, note]
        return None
    try:
        raw, content_type, final_url, transport, extras = firecrawl_fetch(request)
        fallback = structure_content(
            request,
            raw,
            content_type,
            final_url,
            transport,
            [*errors, "fallback:firecrawl_auto_after_low_quality_or_failure"],
            extras,
        )
        fallback.site_data.setdefault("fallback", {})["firecrawl_auto"] = {
            "used": True,
            "reason": errors[-1] if errors else "low_quality_or_failure",
            "api_key_env": request.firecrawl_api_key_env or "FIRECRAWL_API_KEY",
        }
        return fallback
    except Exception as exc:
        note = f"firecrawl_auto_fallback:{type(exc).__name__}:{str(exc)[:200]}"
        errors.append(note)
        if result is not None and note not in result.errors:
            result.errors = [*result.errors, note]
        return None


def _fallback_reason(result: FetchResult) -> str:
    quality = result.extraction_quality or {}
    reasons = quality.get("reasons") or []
    status = quality.get("status") or "unknown"
    reason_text = "; ".join(str(item) for item in reasons) if isinstance(reasons, list) else str(reasons)
    return f"quality:{status}:{reason_text[:180]}"


def _visible_markdown_is_better(visible_markdown: str, extracted: str, quality: dict[str, Any] | None) -> bool:
    visible_chars = len(" ".join(str(visible_markdown or "").split()))
    extracted_chars = len(" ".join(str(extracted or "").split()))
    if visible_chars < 80:
        return False
    quality_status = str((quality or {}).get("status") or "")
    if quality_status in {"empty", "too_short", "suspect_shell", "suspect_incomplete"}:
        return visible_chars > extracted_chars
    return visible_chars >= max(250, int(extracted_chars * 1.25))


def _browser_visible_can_replace_extracted(visible_markdown: str, extracted: str) -> bool:
    visible_chars = len(" ".join(str(visible_markdown or "").split()))
    extracted_chars = len(" ".join(str(extracted or "").split()))
    if visible_chars < 80:
        return False
    if extracted_chars < 80:
        return True
    return visible_chars >= int(extracted_chars * 0.8)


def _browser_visible_quality(
    markdown: str,
    *,
    title: str = "",
    url: str = "",
    metadata: dict[str, Any] | None = None,
    original_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text_chars = len(" ".join(str(markdown or "").split()))
    quality = quality_for(markdown, title=title, url=url, engine="browser_visible_dom", metadata=metadata)
    if quality.get("status") == "ok" and text_chars < 200:
        quality = {
            **quality,
            "status": "too_short",
            "reasons": [*list(quality.get("reasons") or []), "browser visible markdown shorter than 200 chars"],
            "recommendation": "retry with browser/profile or a site-specific backend such as Crawl4AI",
        }
    return {**quality, "source_quality": original_quality or {}}


def _adopt_browser_image_assets(result: FetchResult) -> None:
    if not result.archive or not result.image_assets:
        return
    target_keep = archive_child_path(result.archive, "image_assets", ".keep")
    if not target_keep:
        return
    target_dir = target_keep.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    adopted = []
    for index, image in enumerate(result.image_assets, start=1):
        asset = dict(image)
        source_path = Path(str(asset.get("asset_path") or ""))
        if source_path.exists():
            target_path = target_dir / source_path.name
            if target_path.exists() and source_path.resolve() != target_path.resolve():
                stem = target_path.stem or f"image-{index}"
                target_path = target_dir / f"{stem}-{index}{target_path.suffix}"
            if source_path.resolve() != target_path.resolve():
                shutil.copyfile(source_path, target_path)
            asset["asset_path"] = str(target_path)
        adopted.append(asset)
    result.image_assets = adopted


def _cleanup_provisional_archive(provisional_archive: dict[str, Any] | None, result: FetchResult) -> None:
    if not provisional_archive or not result.archive:
        return
    provisional_path = Path(str(provisional_archive.get("archive_path") or ""))
    final_path = Path(str(result.archive.get("archive_path") or ""))
    if provisional_path and final_path and provisional_path.exists() and provisional_path.resolve() != final_path.resolve():
        shutil.rmtree(provisional_path, ignore_errors=True)


def markdown_structure_index(markdown: str) -> dict[str, Any]:
    outline: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    in_fence = False
    image_index = 0
    for line_number, line in enumerate(str(markdown or "").splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        if heading_match:
            outline.append(
                {
                    "line": line_number,
                    "level": len(heading_match.group(1)),
                    "text": heading_match.group(2).strip(),
                }
            )
        for match in MARKDOWN_IMAGE_RE.finditer(line):
            alt, src, suffix = _markdown_image_match_parts(match)
            image_index += 1
            images.append(
                {
                    "index": image_index,
                    "line": line_number,
                    "alt": alt,
                    "src": src,
                    "title": suffix.strip(),
                }
            )
    blocks = markdown_block_index(markdown, outline=outline, images=images)
    _attach_image_block_context(images, blocks)
    return {
        "outline": outline,
        "images": images,
        "heading_count": len(outline),
        "image_count": len(images),
        "blocks": blocks,
    }


def _summarize_neighbor_block(block: dict[str, Any] | None) -> dict[str, Any]:
    if not block:
        return {}
    summary = {
        "order": block.get("order", 0),
        "type": block.get("type", ""),
        "line": block.get("line", 0),
        "end_line": block.get("end_line", 0),
    }
    for key in ("level", "text", "alt", "src"):
        value = block.get(key)
        if value not in (None, ""):
            summary[key] = value
    return summary


def _image_blocks_match(image: dict[str, Any], block: dict[str, Any]) -> bool:
    return (
        str(block.get("type") or "") == "image"
        and int(block.get("line") or 0) == int(image.get("line") or 0)
        and str(block.get("src") or "") == str(image.get("src") or "")
        and str(block.get("alt") or "") == str(image.get("alt") or "")
    )


def _attach_image_block_context(images: list[dict[str, Any]], blocks: list[dict[str, Any]]) -> None:
    if not images or not blocks:
        return
    for image in images:
        for position, block in enumerate(blocks):
            if not _image_blocks_match(image, block):
                continue
            previous_block = next((item for item in reversed(blocks[:position]) if item.get("type") != "image"), None)
            next_block = next((item for item in blocks[position + 1 :] if item.get("type") != "image"), None)
            image["block_order"] = block.get("order", 0)
            image["previous_block"] = _summarize_neighbor_block(previous_block)
            image["next_block"] = _summarize_neighbor_block(next_block)
            break


def markdown_block_index(
    markdown: str,
    *,
    outline: list[dict[str, Any]] | None = None,
    images: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    try:
        from markdown_it import MarkdownIt

        tokens = MarkdownIt("commonmark").parse(markdown or "")
    except Exception:
        return _fallback_markdown_blocks(outline or [], images or [])
    blocks: list[dict[str, Any]] = []
    order = 0
    current_open: Any = None
    in_list_item = False
    for token in tokens:
        if token.type == "list_item_open":
            in_list_item = True
            current_open = token
            continue
        if token.type == "list_item_close":
            in_list_item = False
            current_open = None
            continue
        if token.type in {"heading_open", "paragraph_open", "blockquote_open", "list_item_open"}:
            current_open = token
            continue
        if token.type != "inline" or current_open is None:
            continue
        block_type = "paragraph"
        level = 0
        if current_open.type == "heading_open":
            block_type = "heading"
            level = int((current_open.tag or "h0").lstrip("h") or 0)
        elif current_open.type == "blockquote_open":
            block_type = "blockquote"
        elif current_open.type == "list_item_open" or in_list_item:
            block_type = "list_item"
        start_line = int((token.map or current_open.map or [0, 0])[0]) + 1
        end_line = int((token.map or current_open.map or [0, 0])[1])
        child_image_tokens = [child for child in (token.children or []) if child.type == "image"]
        image_matches = list(MARKDOWN_IMAGE_RE.finditer(token.content or ""))
        if child_image_tokens:
            for item in _inline_markdown_blocks(
                token.children or [],
                block_type=block_type,
                line=start_line,
                end_line=end_line,
                level=level,
            ):
                order += 1
                item["order"] = order
                blocks.append(item)
            current_open = None
            continue
        if block_type != "paragraph" or not image_matches:
            order += 1
            item = {
                "order": order,
                "type": block_type,
                "line": start_line,
                "end_line": end_line,
                "text": token.content,
            }
            if level:
                item["level"] = level
            blocks.append(item)
        for match in image_matches:
            alt, src, suffix = _markdown_image_match_parts(match)
            order += 1
            blocks.append(
                {
                    "order": order,
                    "type": "image",
                    "line": start_line,
                    "end_line": end_line,
                    "alt": alt,
                    "src": src,
                    "title": suffix.strip(),
                }
            )
        current_open = None
    return blocks


def _inline_markdown_blocks(
    children: list[Any],
    *,
    block_type: str,
    line: int,
    end_line: int,
    level: int = 0,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    text_parts: list[str] = []
    inline_index = 0

    def flush_text() -> None:
        nonlocal inline_index
        text = "".join(text_parts).strip()
        text_parts.clear()
        if not text:
            return
        inline_index += 1
        item = {
            "type": block_type,
            "parent_type": block_type,
            "inline_index": inline_index,
            "line": line,
            "end_line": end_line,
            "text": text,
        }
        if level:
            item["level"] = level
        blocks.append(item)

    for child in children:
        if child.type == "image":
            flush_text()
            inline_index += 1
            item = {
                "type": "image",
                "parent_type": block_type,
                "inline_index": inline_index,
                "line": line,
                "end_line": end_line,
                "alt": child.content or "",
                "src": _token_attr(child, "src"),
                "title": _token_attr(child, "title"),
            }
            if level:
                item["level"] = level
            blocks.append(item)
            continue
        if child.type in {"text", "code_inline", "html_inline"}:
            text_parts.append(child.content or "")
            continue
        if child.type in {"softbreak", "hardbreak"}:
            text_parts.append("\n")
            continue
        if child.content and child.type not in {"link_open", "link_close", "em_open", "em_close", "strong_open", "strong_close"}:
            text_parts.append(child.content)
    flush_text()
    return blocks


def _token_attr(token: Any, name: str) -> str:
    try:
        value = token.attrGet(name)
        if value is not None:
            return str(value)
    except Exception:
        pass
    attrs = getattr(token, "attrs", None) or {}
    if isinstance(attrs, dict):
        return str(attrs.get(name) or "")
    if isinstance(attrs, list):
        for item in attrs:
            if isinstance(item, (list, tuple)) and len(item) >= 2 and item[0] == name:
                return str(item[1] or "")
    return ""


def _fallback_markdown_blocks(outline: list[dict[str, Any]], images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for item in outline:
        blocks.append(
            {
                "type": "heading",
                "line": item.get("line", 0),
                "end_line": item.get("line", 0),
                "level": item.get("level", 0),
                "text": item.get("text", ""),
            }
        )
    for item in images:
        blocks.append(
            {
                "type": "image",
                "line": item.get("line", 0),
                "end_line": item.get("line", 0),
                "alt": item.get("alt", ""),
                "src": item.get("src", ""),
                "title": item.get("title", ""),
            }
        )
    blocks.sort(key=lambda item: (int(item.get("line") or 0), 0 if item.get("type") == "heading" else 1))
    for index, item in enumerate(blocks, start=1):
        item["order"] = index
    return blocks


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0 if numerator <= 0 else 0.0
    return round(numerator / denominator, 3)


def _norm_compare_text(value: object) -> str:
    text = re.sub(r"\s+", "", str(value or "").lower())
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _missing_headings(extracted_index: dict[str, Any], browser_visible_index: dict[str, Any]) -> list[dict[str, Any]]:
    extracted = {_norm_compare_text(item.get("text")) for item in extracted_index.get("outline") or []}
    missing: list[dict[str, Any]] = []
    for item in browser_visible_index.get("outline") or []:
        key = _norm_compare_text(item.get("text"))
        if not key or key in extracted:
            continue
        missing.append(
            {
                "level": int(item.get("level") or 0),
                "text": str(item.get("text") or ""),
                "line": int(item.get("line") or 0),
            }
        )
    return missing


def _image_compare_keys(item: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    src = _normalize_url_for_match(str(item.get("src") or ""))
    alt = _norm_compare_text(item.get("alt"))
    if src:
        keys.add(f"src:{src}")
        name = Path(urllib.parse.urlparse(src).path).name
        if name:
            keys.add(f"name:{name.lower()}")
    if alt:
        keys.add(f"alt:{alt}")
    return keys


def _missing_images(extracted_index: dict[str, Any], browser_visible_index: dict[str, Any]) -> list[dict[str, Any]]:
    extracted_keys: set[str] = set()
    for item in extracted_index.get("images") or []:
        extracted_keys.update(_image_compare_keys(item))
    missing: list[dict[str, Any]] = []
    for item in browser_visible_index.get("images") or []:
        keys = _image_compare_keys(item)
        if keys and extracted_keys.intersection(keys):
            continue
        missing.append(
            {
                "index": int(item.get("index") or 0),
                "line": int(item.get("line") or 0),
                "alt": str(item.get("alt") or ""),
                "src": str(item.get("src") or ""),
            }
        )
    return missing


def _completeness_check(
    *,
    extracted_text: str,
    extracted_index: dict[str, Any],
    browser_visible_markdown: str,
    browser_visible_index: dict[str, Any],
) -> dict[str, Any]:
    extracted_chars = len(" ".join((extracted_text or "").split()))
    visible_chars = len(" ".join((browser_visible_markdown or "").split()))
    extracted_blocks = len(extracted_index.get("blocks") or [])
    visible_blocks = len(browser_visible_index.get("blocks") or [])
    extracted_headings = int(extracted_index.get("heading_count") or 0)
    visible_headings = int(browser_visible_index.get("heading_count") or 0)
    extracted_images = int(extracted_index.get("image_count") or 0)
    visible_images = int(browser_visible_index.get("image_count") or 0)
    missing_headings = _missing_headings(extracted_index, browser_visible_index)
    missing_images = _missing_images(extracted_index, browser_visible_index)
    reasons: list[str] = []
    if visible_chars >= 200 and extracted_chars < int(visible_chars * 0.8):
        reasons.append("extracted text is much shorter than browser-visible markdown")
    if visible_blocks >= 3 and extracted_blocks < int(visible_blocks * 0.8):
        reasons.append("extracted markdown has fewer ordered blocks than browser-visible markdown")
    if visible_headings and extracted_headings < visible_headings:
        reasons.append("extracted markdown has fewer headings than browser-visible markdown")
    if visible_images and extracted_images < visible_images:
        reasons.append("extracted markdown has fewer images than browser-visible markdown")
    if missing_headings:
        reasons.append("extracted markdown is missing browser-visible heading text")
    if missing_images:
        reasons.append("extracted markdown is missing browser-visible image references")
    status = "no_browser_visible"
    if browser_visible_markdown:
        status = "suspect_incomplete" if reasons else "ok"
    return {
        "status": status,
        "reasons": reasons,
        "extracted_chars": extracted_chars,
        "browser_visible_chars": visible_chars,
        "char_ratio": _ratio(extracted_chars, visible_chars),
        "extracted_block_count": extracted_blocks,
        "browser_visible_block_count": visible_blocks,
        "block_ratio": _ratio(extracted_blocks, visible_blocks),
        "extracted_heading_count": extracted_headings,
        "browser_visible_heading_count": visible_headings,
        "heading_ratio": _ratio(extracted_headings, visible_headings),
        "extracted_image_count": extracted_images,
        "browser_visible_image_count": visible_images,
        "image_ratio": _ratio(extracted_images, visible_images),
        "missing_headings": missing_headings[:20],
        "missing_images": missing_images[:20],
    }


def browser_visible_dom_outline(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outline: list[dict[str, Any]] = []
    for block in blocks or []:
        if str(block.get("type") or "") != "heading":
            continue
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        item = {
            "order": int(block.get("order") or len(outline) + 1),
            "level": max(1, min(6, int(block.get("level") or block.get("aria_level") or 1))),
            "text": text,
            "tag": str(block.get("tag") or ""),
        }
        if block.get("role"):
            item["role"] = str(block.get("role") or "")
        if block.get("aria_level"):
            item["aria_level"] = str(block.get("aria_level") or "")
        outline.append(item)
    return outline


def _preferred_readable_artifact(
    result: FetchResult,
    *,
    completeness: dict[str, Any],
    markdown_index: dict[str, Any],
    browser_visible_markdown_index: dict[str, Any],
    preferred_markdown_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    archive = result.archive or {}
    preferred_index = preferred_markdown_index or markdown_index
    if archive.get("preferred_readable_html_path"):
        return {
            "role": "preferred_readable_html",
            "html_path": archive.get("preferred_readable_html_path", ""),
            "markdown_path": result.preferred_markdown_path or archive.get("preferred_markdown_path", ""),
            "source": archive.get("preferred_markdown_source", "") or result.extraction_engine or "preferred_markdown",
            "reason": f"rendered from preferred markdown: {archive.get('preferred_markdown_reason', '')}".strip(),
            "heading_count": preferred_index["heading_count"],
            "image_count": preferred_index["image_count"],
            "block_count": len(preferred_index["blocks"]),
        }
    prefer_visible = False
    reasons: list[str] = []
    if result.browser_visible_html_path and result.extraction_engine == "browser_visible_dom":
        prefer_visible = True
        reasons.append("browser-visible DOM was used as extracted text")
    if (
        result.browser_visible_html_path
        and completeness.get("status") == "suspect_incomplete"
        and _browser_visible_can_replace_extracted(result.browser_visible_markdown, result.extracted_text)
    ):
        prefer_visible = True
        reasons.extend(str(item) for item in completeness.get("reasons", []) if item)
    if result.browser_visible_html_path and not archive.get("readable_html_path"):
        prefer_visible = True
        reasons.append("standard readable HTML is unavailable")

    if prefer_visible:
        return {
            "role": "browser_visible_html",
            "html_path": result.browser_visible_html_path,
            "markdown_path": result.preferred_markdown_path or archive.get("preferred_markdown_path", "") or result.browser_visible_path,
            "source": "browser_visible_dom",
            "reason": "; ".join(dict.fromkeys(reasons)),
            "heading_count": browser_visible_markdown_index["heading_count"],
            "image_count": browser_visible_markdown_index["image_count"],
            "block_count": len(browser_visible_markdown_index["blocks"]),
        }
    return {
        "role": "readable_html",
        "html_path": archive.get("readable_html_path", ""),
        "markdown_path": result.preferred_markdown_path or archive.get("preferred_markdown_path", "") or archive.get("extracted_text_path", ""),
        "source": result.extraction_engine or "extracted_text",
        "reason": "standard extraction passed completeness checks",
        "heading_count": markdown_index["heading_count"],
        "image_count": markdown_index["image_count"],
        "block_count": len(markdown_index["blocks"]),
    }


def _review_recommendation(completeness: dict[str, Any], preferred_readable: dict[str, Any], asset_completeness: dict[str, Any] | None = None) -> dict[str, Any]:
    status = str(completeness.get("status") or "")
    asset_completeness = asset_completeness or {}
    asset_status = str(asset_completeness.get("status") or "")
    missing_headings = completeness.get("missing_headings") or []
    missing_images = completeness.get("missing_images") or []
    preferred_path = str(preferred_readable.get("html_path") or preferred_readable.get("markdown_path") or "")
    if status == "suspect_incomplete" and preferred_path:
        return {
            "status": "review_preferred_readable",
            "action": "open_preferred_readable",
            "path": preferred_path,
            "reason": "extracted markdown appears incomplete compared with browser-visible content",
            "missing_heading_count": len(missing_headings),
            "missing_image_count": len(missing_images),
        }
    if status == "suspect_incomplete":
        return {
            "status": "retry_with_browser",
            "action": "rerun_with_browser_profile",
            "path": "",
            "reason": "extracted markdown appears incomplete and no preferred readable artifact is available",
            "missing_heading_count": len(missing_headings),
            "missing_image_count": len(missing_images),
        }
    if asset_status in {"unverified_assets", "review_assets"}:
        return {
            "status": asset_status,
            "action": "open_image_gallery",
            "path": str(asset_completeness.get("gallery_path") or ""),
            "reason": "downloaded image assets need human review against the readable output",
            "missing_heading_count": len(missing_headings),
            "missing_image_count": len(missing_images),
        }
    if status == "ok":
        return {
            "status": "ok",
            "action": "accept_extracted_markdown",
            "path": preferred_path,
            "reason": "extracted markdown matches browser-visible structure checks",
            "missing_heading_count": 0,
            "missing_image_count": 0,
        }
    return {
        "status": status or "unknown",
        "action": "",
        "path": preferred_path,
        "reason": "",
        "missing_heading_count": len(missing_headings),
        "missing_image_count": len(missing_images),
    }


def _score_from_status(status: str, *, neutral: int = 70) -> int:
    if status == "ok":
        return 100
    if status in {"no_browser_visible", "no_downloaded_assets"}:
        return neutral
    if status in {"review_assets", "unverified_assets"}:
        return 60
    if status in {"suspect_incomplete", "too_short", "suspect_shell", "empty"}:
        return 30
    if not status:
        return neutral
    return 50


def _content_completeness(
    *,
    result: FetchResult,
    completeness: dict[str, Any],
    asset_completeness: dict[str, Any],
    preferred_markdown: dict[str, Any],
    attachments: list[dict[str, Any]],
    feishu_comparison: dict[str, Any],
    review_recommendation: dict[str, Any],
) -> dict[str, Any]:
    quality_status = str((result.extraction_quality or {}).get("status") or "")
    completeness_status = str(completeness.get("status") or "")
    asset_status = str(asset_completeness.get("status") or "")
    preferred_structure = preferred_markdown.get("structure") or {}
    reasons = []
    reasons.extend(str(item) for item in (result.extraction_quality or {}).get("reasons", []) if str(item))
    reasons.extend(str(item) for item in completeness.get("reasons", []) if str(item))
    reasons.extend(str(item) for item in asset_completeness.get("reasons", []) if str(item))
    reasons.extend(str(item) for item in feishu_comparison.get("reasons", []) if str(item))

    text_score = min(_score_from_status(quality_status), _score_from_status(completeness_status))
    image_score = _score_from_status(asset_status)
    structure_score = _score_from_status(completeness_status)
    if int(preferred_structure.get("heading_count") or 0) == 0 and int(preferred_structure.get("block_count") or 0) >= 5:
        structure_score = min(structure_score, 75)
        reasons.append("preferred markdown has content blocks but no headings")
    table_score = 100
    if feishu_comparison and feishu_comparison.get("status") in {"needs_review", "suspect_incomplete"}:
        text_score = min(text_score, 60)
        image_score = min(image_score, 60)
        structure_score = min(structure_score, 60)
    attachment_score = 100 if attachments else 90
    scores = [text_score, image_score, table_score, structure_score, attachment_score]
    minimum = min(scores)
    if minimum < 50:
        overall = "suspect_incomplete"
    elif minimum < 80 or reasons:
        overall = "needs_review"
    else:
        overall = "ok"
    action = str(review_recommendation.get("action") or "")
    action_path = str(review_recommendation.get("path") or "")
    if quality_status and quality_status != "ok" and action == "accept_extracted_markdown":
        action = "review_preferred_readable"
        action_path = str((review_recommendation.get("path") or (result.archive or {}).get("preferred_readable_html_path") or ""))
    return {
        "version": 1,
        "text_score": text_score,
        "image_score": image_score,
        "table_score": table_score,
        "structure_score": structure_score,
        "attachment_score": attachment_score,
        "overall_status": overall,
        "reasons": list(dict.fromkeys(reasons)),
        "recommended_next_action": action or "manual_review",
        "recommended_path": action_path,
        "source_checks": {
            "extraction_quality_status": quality_status,
            "completeness_status": completeness_status,
            "asset_completeness_status": asset_status,
            "feishu_comparison_status": feishu_comparison.get("status", ""),
        },
    }


def _reading_report_markdown(result: FetchResult, archive_index: dict[str, Any]) -> str:
    preferred_views = archive_index.get("preferred_views") or {}
    primary = preferred_views.get("primary") or {}
    preferred_readable = archive_index.get("preferred_readable") or {}
    preferred_markdown = archive_index.get("preferred_markdown") or {}
    completeness = archive_index.get("completeness_check") or {}
    asset_completeness = archive_index.get("asset_completeness_check") or {}
    review = archive_index.get("review_recommendation") or {}
    content_completeness = archive_index.get("content_completeness") or {}
    quality_gate = archive_index.get("quality_gate") or {}
    feishu_comparison = archive_index.get("feishu_comparison") or {}
    markdown_structure = archive_index.get("markdown_structure") or {}
    image_gallery = archive_index.get("image_gallery") or {}
    gallery_counts = image_gallery.get("counts") or {}
    browser_visible = archive_index.get("browser_visible") or {}
    browser_structure = browser_visible.get("markdown_structure") or {}
    attachments = archive_index.get("attachments") or []
    site_data = archive_index.get("site_data") or {}
    site_strategy = site_data.get("site_strategy") or {}
    api_response = archive_index.get("api_response") or {}
    endpoint_candidates = archive_index.get("endpoint_candidates") or []
    evidence_chain = archive_index.get("evidence_chain") or []

    def path_from(view: dict[str, Any], fallback: str = "") -> str:
        return str(view.get("path") or view.get("html_path") or view.get("markdown_path") or fallback or "")

    def reasons(items: Any) -> str:
        values = [str(item) for item in (items or []) if str(item)]
        return "\n".join(f"- {item}" for item in values) if values else "- none"

    human_path = path_from(primary.get("human") or {}, preferred_readable.get("html_path", ""))
    agent_path = path_from(primary.get("agent") or {}, preferred_markdown.get("path", ""))
    image_path = path_from(primary.get("images") or {}, str(image_gallery.get("path") or ""))
    action = str(review.get("action") or "")
    action_path = str(review.get("path") or "")
    quality_status = str(result.extraction_quality.get("status") or "")
    if quality_status and quality_status != "ok" and action == "accept_extracted_markdown":
        next_action = f"review_preferred_readable: {human_path}" if human_path else "review_extraction_quality"
    elif action and action_path:
        next_action = f"{action}: {action_path}"
    else:
        next_action = action or "manual_review"

    lines = [
        f"# Reading Report: {result.title or result.final_url or result.requested_url}",
        "",
        "## Summary",
        "",
        f"- Source URL: {result.final_url or result.requested_url}",
        f"- Transport: {result.transport}",
        f"- Extraction engine: {result.extraction_engine}",
        f"- Site strategy: {site_strategy.get('site_key', '') or 'generic'} / {site_strategy.get('kind', '') or ''}",
        f"- Extraction quality: {quality_status}",
        f"- Capture completeness: {result.capture_completeness.get('status', 'unknown')}",
        f"- Manual material entry: {(result.capture_completeness.get('manual_capture') or {}).get('action', 'none')}",
        f"- Supplement instructions: {(result.capture_completeness.get('manual_capture') or {}).get('instructions', '')}",
        f"- Missing DOM link labels: {len((result.capture_completeness.get('dom_links') or {}).get('missing') or [])}",
        f"- Original image availability: {(result.capture_completeness.get('body_images') or {}).get('original_availability', 'unknown')}",
        f"- SPA shell detected: {bool(result.extraction_quality.get('spa_shell_detected'))}",
        f"- Login redirect detected: {bool(result.extraction_quality.get('login_redirect_detected'))}",
        f"- JSON API response: {bool(api_response.get('is_json'))}",
        f"- Endpoint candidates: {len(endpoint_candidates)}",
        f"- Completeness: {completeness.get('status', 'unknown')}",
        f"- Overall content completeness: {content_completeness.get('overall_status', 'unknown')}",
        f"- Quality gate: {'passed' if quality_gate.get('passed') else 'failed'}",
        f"- Asset completeness: {asset_completeness.get('status', 'unknown')}",
        f"- Recommended next action: {quality_gate.get('recommended_next_action') or next_action}",
        f"- Rerun strategy: {quality_gate.get('rerun_strategy', '')}",
        f"- Strategy manual fallback: {site_strategy.get('recommended_manual_fallback', '') or 'none'}",
        "",
        "## Recommended Files",
        "",
        f"- Human readable: {human_path}",
        f"- Agent readable: {agent_path}",
        f"- Image gallery: {image_path}",
        f"- Structured JSON: {(result.archive or {}).get('structured_path', '')}",
        f"- JSON summary: {api_response.get('summary_markdown_path', '')}",
        f"- Endpoint candidates: {((result.site_data.get('endpoint_discovery') or {}) if isinstance(result.site_data, dict) else {}).get('candidates_markdown_path', '')}",
        "",
        "## Content Counts",
        "",
        f"- Extracted text chars: {len(result.extracted_text or '')}",
        f"- Preferred markdown chars: {preferred_markdown.get('chars', 0)}",
        f"- Preferred headings: {(preferred_markdown.get('structure') or {}).get('heading_count', 0)}",
        f"- Preferred blocks: {(preferred_markdown.get('structure') or {}).get('block_count', 0)}",
        f"- Preferred images: {(preferred_markdown.get('structure') or {}).get('image_count', 0)}",
        f"- Browser visible blocks: {browser_visible.get('block_count', 0)}",
        f"- Browser visible headings: {browser_structure.get('dom_heading_count') or browser_structure.get('heading_count') or 0}",
        f"- Browser visible images: {browser_structure.get('image_count', 0)}",
        f"- Downloaded image assets: {asset_completeness.get('downloaded_asset_count', len(result.image_assets or []))}",
        f"- Image gallery counts: {json.dumps(gallery_counts, ensure_ascii=False, sort_keys=True)}",
        f"- Table count: {markdown_structure.get('table_count', 0)}",
        f"- Attachment count: {len(attachments)}",
        f"- Scores: text={content_completeness.get('text_score', '')}, image={content_completeness.get('image_score', '')}, table={content_completeness.get('table_score', '')}, structure={content_completeness.get('structure_score', '')}, attachment={content_completeness.get('attachment_score', '')}",
        "",
        "## Quality Gate",
        "",
        f"- Passed: {bool(quality_gate.get('passed'))}",
        f"- Expected: {json.dumps(quality_gate.get('expected') or {}, ensure_ascii=False, sort_keys=True)}",
        f"- Actual: {json.dumps(quality_gate.get('actual') or {}, ensure_ascii=False, sort_keys=True)}",
        f"- Rerun command: {quality_gate.get('rerun_command', '')}",
        "",
        "## Gap Diagnosis",
        "",
        reasons([failure.get("code", "") for failure in quality_gate.get("failures", [])]),
        "",
        "## Risk Reasons",
        "",
        "### Extraction Quality",
        reasons(result.extraction_quality.get("reasons")),
        "",
        "### Completeness",
        reasons(completeness.get("reasons")),
        "",
        "### Asset Completeness",
        reasons(asset_completeness.get("reasons")),
    ]
    if api_response.get("is_json"):
        lines.extend(
            [
                "",
                "## JSON/API Response",
                "",
                f"- Pretty JSON: {api_response.get('pretty_json_path', '')}",
                f"- Summary: {api_response.get('summary_markdown_path', '')}",
                f"- Top-level keys: {', '.join(str(item) for item in api_response.get('top_level_keys', [])[:20]) or 'none'}",
                "- Redaction warnings:",
                reasons(api_response.get("redaction_warnings")),
            ]
        )
    if endpoint_candidates:
        lines.extend(
            [
                "",
                "## Endpoint Discovery",
                "",
                "- Policy: candidate_only_not_fetched",
                f"- Candidate count: {len(endpoint_candidates)}",
            ]
        )
        for item in endpoint_candidates[:10]:
            lines.append(f"- `{item.get('endpoint', '')}` ({item.get('matched_keyword', '')}, confidence={item.get('confidence', '')})")
    if evidence_chain:
        lines.extend(["", "## Evidence Chain", ""])
        for item in evidence_chain:
            lines.append(f"- {item.get('stage', '')}: {item.get('url', '') or item.get('path', '')} {item.get('sha256', '')}".rstrip())
    if site_strategy:
        lines.extend(
            [
                "",
                "## Site Strategy",
                "",
                f"- Site key: {site_strategy.get('site_key', '')}",
                f"- Kind: {site_strategy.get('kind', '')}",
                f"- Preferred backend order: {', '.join(str(item) for item in site_strategy.get('preferred_backend_order', []) or []) or 'none'}",
                f"- Allowed transports: {', '.join(str(item) for item in site_strategy.get('allowed_transports', []) or []) or 'none'}",
                f"- Login hint: {site_strategy.get('login_required_hint', '') or 'none'}",
                f"- Public fallbacks: {', '.join(str(item) for item in site_strategy.get('public_fallbacks', []) or []) or 'none'}",
                f"- Shell hints: {', '.join(str(item) for item in site_strategy.get('shell_detection_hints', []) or []) or 'none'}",
            ]
        )
    if feishu_comparison:
        lines.extend(
            [
                "",
                "### Feishu/Lark Export Comparison",
                f"- Status: {feishu_comparison.get('status', '')}",
                f"- DOCX path: {feishu_comparison.get('docx_path', '')}",
                reasons(feishu_comparison.get("reasons")),
            ]
        )
    missing_headings = completeness.get("missing_headings") or []
    missing_images = completeness.get("missing_images") or []
    if missing_headings or missing_images:
        lines.extend(["", "## Missing Samples", ""])
        if missing_headings:
            lines.append("### Missing Headings")
            lines.extend(f"- {item}" for item in missing_headings[:10])
            lines.append("")
        if missing_images:
            lines.append("### Missing Images")
            lines.extend(f"- {item}" for item in missing_images[:10])
    lines.append("")
    return "\n".join(lines)


def _write_reading_report(result: FetchResult) -> None:
    if not result.archive:
        return
    path = archive_child_path(result.archive, "reading_report.md")
    if not path:
        return
    archive_index = build_archive_index(result)
    report = _reading_report_markdown(result, archive_index)
    path.write_text(report, encoding="utf-8", newline="\n")
    result.reading_report_path = str(path)
    result.archive["reading_report_path"] = str(path)
    result.archive["reading_report_sha256"] = sha256_text(report)


def _view_artifact(
    *,
    role: str,
    path: str,
    media_type: str = "",
    label: str = "",
    source: str = "",
    description: str = "",
    quality: str = "",
    **extra: Any,
) -> dict[str, Any]:
    artifact = {
        "role": role,
        "path": path,
        "media_type": media_type,
        "label": label or role,
        "source": source,
        "description": description,
    }
    if quality:
        artifact["quality"] = quality
    for key, value in extra.items():
        if value not in ("", None, [], {}):
            artifact[key] = value
    return artifact


def _first_view(views: list[dict[str, Any]], *roles: str) -> dict[str, Any]:
    for role in roles:
        for item in views:
            if item.get("role") == role:
                return item
    return views[0] if views else {}


def _build_preferred_views(
    result: FetchResult,
    *,
    files: list[dict[str, Any]],
    preferred_readable: dict[str, Any],
    preferred_markdown: dict[str, Any],
    image_gallery: dict[str, Any],
    attachments: list[dict[str, Any]],
    completeness: dict[str, Any],
    asset_completeness: dict[str, Any],
) -> dict[str, Any]:
    archive = result.archive or {}
    file_by_role = {str(item.get("role") or ""): item for item in files if item.get("role")}
    human_readable: list[dict[str, Any]] = []
    agent_readable: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    media: list[dict[str, Any]] = []
    attachment_views: list[dict[str, Any]] = []

    preferred_html = str(preferred_readable.get("html_path") or "")
    if preferred_html:
        human_readable.append(
            _view_artifact(
                role=str(preferred_readable.get("role") or "preferred_readable_html"),
                path=preferred_html,
                media_type="text/html",
                label="Preferred readable HTML",
                source=str(preferred_readable.get("source") or ""),
                description=str(preferred_readable.get("reason") or "best human-readable HTML rendering"),
                quality=str(completeness.get("status") or ""),
                heading_count=preferred_readable.get("heading_count"),
                image_count=preferred_readable.get("image_count"),
                block_count=preferred_readable.get("block_count"),
            )
        )
    for role, label in (
        ("readable_html", "Readable HTML"),
        ("browser_visible_html", "Browser visible HTML"),
        ("crawl4ai_cleaned_html", "Crawl4AI cleaned HTML"),
        ("crawl4ai_html", "Crawl4AI HTML"),
        ("firecrawl_html", "Firecrawl HTML"),
        ("scrapling_html", "Scrapling HTML"),
        ("singlefile", "SingleFile HTML"),
    ):
        item = file_by_role.get(role) or {}
        path = str(item.get("path") or "")
        if path and path != preferred_html:
            human_readable.append(
                _view_artifact(
                    role=role,
                    path=path,
                    media_type=str(item.get("media_type") or "text/html"),
                    label=label,
                    source=str(item.get("source") or ""),
                )
            )

    feishu_export = (result.site_data.get("feishu_export") or {}) if isinstance(result.site_data, dict) else {}
    docx_path = str(feishu_export.get("docx_path") or "")
    if docx_path:
        human_readable.append(
            _view_artifact(
                role="docx_export",
                path=docx_path,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                label="DOCX export",
                source="feishu_export",
                description="official Feishu/Lark export preserved for human review and reprocessing",
                image_count=feishu_export.get("image_count"),
                bytes=feishu_export.get("bytes"),
            )
        )
    for item in files:
        role = str(item.get("role") or "")
        path = str(item.get("path") or "")
        media_type = str(item.get("media_type") or "")
        if not path:
            continue
        if role.endswith("pdf") or media_type == "application/pdf" or path.lower().endswith(".pdf"):
            human_readable.append(_view_artifact(role=role or "pdf", path=path, media_type=media_type or "application/pdf", label="PDF"))

    preferred_markdown_path = str(preferred_markdown.get("path") or "")
    if preferred_markdown_path:
        agent_readable.append(
            _view_artifact(
                role="preferred_markdown",
                path=preferred_markdown_path,
                media_type="text/markdown",
                label="Preferred Markdown",
                source=str(preferred_markdown.get("source") or ""),
                description=str(preferred_markdown.get("reason") or "best markdown/text artifact for LLM use"),
                chars=preferred_markdown.get("chars", 0),
                structure=preferred_markdown.get("structure") or {},
            )
        )
    for role, label, media_type in (
        ("structured", "Structured JSON", "application/json"),
        ("extracted_text", "Extracted text", "text/markdown"),
        ("crawl4ai_markdown", "Crawl4AI Markdown", "text/markdown"),
        ("firecrawl_markdown", "Firecrawl Markdown", "text/markdown"),
        ("scrapling_selector_text", "Scrapling selector text", "text/plain"),
        ("browser_visible", "Browser visible Markdown", "text/markdown"),
        ("metadata", "Metadata JSON", "application/json"),
    ):
        item = file_by_role.get(role) or {}
        path = str(item.get("path") or "")
        if path and path != preferred_markdown_path:
            agent_readable.append(
                _view_artifact(role=role, path=path, media_type=str(item.get("media_type") or media_type), label=label)
            )

    gallery_path = str((image_gallery.get("path") if isinstance(image_gallery, dict) else "") or archive.get("image_gallery_path") or "")
    if gallery_path:
        media.append(
            _view_artifact(
                role="image_gallery",
                path=gallery_path,
                media_type="text/html",
                label="Image gallery",
                source="archive",
                description="human review page for downloaded images and image references",
                quality=str(asset_completeness.get("status") or ""),
                counts=(image_gallery.get("counts") if isinstance(image_gallery, dict) else {}) or archive.get("image_gallery_counts", {}),
            )
        )
    for item in result.image_assets:
        path = str(item.get("asset_path") or "")
        if path:
            media.append(
                _view_artifact(
                    role="image_asset",
                    path=path,
                    media_type=str(item.get("content_type") or ""),
                    label=str(item.get("alt") or "Image asset"),
                    source=str(item.get("url") or ""),
                    sha256=item.get("asset_sha256"),
                    bytes=item.get("bytes"),
                )
            )

    for attachment in attachments:
        attachment_path = str(attachment.get("path") or attachment.get("asset_path") or attachment.get("download_path") or "")
        filename = str(attachment.get("filename") or Path(attachment_path).name or "attachment")
        media_type = str(attachment.get("media_type") or attachment.get("content_type") or "")
        attachment_views.append(
            _view_artifact(
                role="attachment",
                path=attachment_path,
                media_type=media_type,
                label=filename,
                source=str(attachment.get("source") or ""),
                description=str(attachment.get("description") or ""),
                filename=filename,
                extension=attachment.get("extension"),
                url=attachment.get("url"),
            )
        )

    for role, label in (
        ("reading_report", "Reading report"),
        ("archive_home", "Archive home"),
        ("raw_snapshot", "Raw snapshot"),
        ("crawl4ai_result", "Crawl4AI result JSON"),
        ("page_html", "Page HTML"),
        ("screenshot", "Screenshot"),
        ("crawl4ai_screenshot", "Crawl4AI screenshot"),
        ("firecrawl_result", "Firecrawl result JSON"),
        ("firecrawl_screenshot", "Firecrawl screenshot"),
        ("scrapling_result", "Scrapling result JSON"),
    ):
        item = file_by_role.get(role) or {}
        path = str(item.get("path") or "")
        if path:
            evidence.append(_view_artifact(role=role, path=path, media_type=str(item.get("media_type") or ""), label=label))

    all_views = human_readable + agent_readable + media + attachment_views + evidence
    return {
        "version": 1,
        "human_readable": human_readable,
        "agent_readable": agent_readable,
        "media": media,
        "attachments": attachment_views,
        "evidence": evidence,
        "all": all_views,
        "primary": {
            "human": _first_view(human_readable, "preferred_readable_html", "browser_visible_html", "readable_html", "docx_export", "singlefile"),
            "agent": _first_view(agent_readable, "structured", "preferred_markdown", "extracted_text"),
            "images": _first_view(media, "image_gallery"),
            "attachments": attachment_views[0] if attachment_views else {},
            "evidence": _first_view(evidence, "reading_report", "archive_home", "screenshot", "raw_snapshot"),
        },
    }


def build_archive_index(result: FetchResult) -> dict[str, Any]:
    archive = result.archive or {}
    files = []
    for key, role, media_type in (
        ("archive_home_path", "archive_home", "text/html"),
        ("raw_snapshot_path", "raw_snapshot", result.payload.get("detected_mime") or "text/html"),
        ("extracted_text_path", "extracted_text", "text/markdown"),
        ("preferred_markdown_path", "preferred_markdown", "text/markdown"),
        ("reading_report_path", "reading_report", "text/markdown"),
        ("preferred_readable_html_path", "preferred_readable_html", "text/html"),
        ("browser_visible_path", "browser_visible", "text/markdown"),
        ("browser_visible_html_path", "browser_visible_html", "text/html"),
        ("readable_html_path", "readable_html", "text/html"),
        ("image_gallery_path", "image_gallery", "text/html"),
        ("metadata_path", "metadata", "application/json"),
        ("structured_path", "structured", "application/json"),
    ):
        path = archive.get(key)
        if path:
            files.append({"role": role, "path": path, "media_type": media_type})
    for path, role, media_type in (
        (result.page_html_path, "page_html", "text/html"),
        (result.screenshot_path, "screenshot", "image/png"),
        (result.singlefile_path, "singlefile", "text/html"),
    ):
        if path:
            files.append({"role": role, "path": path, "media_type": media_type})
    image_assets = []
    for image in result.image_assets:
        asset = dict(image)
        if asset.get("asset_path"):
            files.append(
                {
                    "role": "image_asset",
                    "path": asset.get("asset_path"),
                    "media_type": asset.get("content_type") or "",
                    "source_url": asset.get("url", ""),
                    "sha256": asset.get("asset_sha256", ""),
                    "bytes": asset.get("bytes", 0),
                }
            )
        image_assets.append(asset)
    attachments = [dict(item) for item in (result.site_data.get("attachments", []) if isinstance(result.site_data, dict) else [])]
    seen_attachment_names = {str(item.get("filename") or "").lower() for item in attachments}
    for attachment in extract_attachment_mentions(result.extracted_text):
        key = str(attachment.get("filename") or "").lower()
        if key and key not in seen_attachment_names:
            seen_attachment_names.add(key)
            attachments.append({**attachment, "source": "extracted_text"})
    markdown_index = markdown_structure_index(result.extracted_text)
    browser_visible_markdown_index = markdown_structure_index(result.browser_visible_markdown)
    preferred_markdown_text = _preferred_markdown_text_for_index(result)
    _preferred_markdown, preferred_markdown_source, preferred_markdown_reason = _preferred_markdown_source(result)
    preferred_markdown_index = markdown_structure_index(preferred_markdown_text)
    browser_visible_outline = browser_visible_dom_outline(result.browser_visible_blocks)
    image_gallery = _image_gallery_groups(result)
    completeness = _completeness_check(
        extracted_text=result.extracted_text,
        extracted_index=markdown_index,
        browser_visible_markdown=result.browser_visible_markdown,
        browser_visible_index=browser_visible_markdown_index,
    )
    asset_completeness = _asset_completeness_check(
        result,
        gallery=image_gallery,
        markdown_index=markdown_index,
        browser_visible_index=browser_visible_markdown_index,
        completeness=completeness,
    )
    asset_completeness["gallery_path"] = archive.get("image_gallery_path", "")
    preferred_readable = _preferred_readable_artifact(
        result,
        completeness=completeness,
        markdown_index=markdown_index,
        browser_visible_markdown_index=browser_visible_markdown_index,
        preferred_markdown_index=preferred_markdown_index,
    )
    preferred_markdown = {
        "path": result.preferred_markdown_path or archive.get("preferred_markdown_path", ""),
        "source": archive.get("preferred_markdown_source", "") or preferred_markdown_source,
        "reason": archive.get("preferred_markdown_reason", "") or preferred_markdown_reason,
        "sha256": archive.get("preferred_markdown_sha256", ""),
        "chars": len(preferred_markdown_text),
        "outline": preferred_markdown_index["outline"],
        "images": preferred_markdown_index["images"],
        "blocks": preferred_markdown_index["blocks"],
        "structure": {
            "heading_count": preferred_markdown_index["heading_count"],
            "image_count": preferred_markdown_index["image_count"],
            "table_count": _markdown_table_count(preferred_markdown_text),
            "block_count": len(preferred_markdown_index["blocks"]),
        },
    }
    feishu_export = (result.site_data.get("feishu_export") or {}) if isinstance(result.site_data, dict) else {}
    feishu_export_metadata = (result.extracted_metadata.get("feishu_export") or {}) if isinstance(result.extracted_metadata, dict) else {}
    feishu_comparison: dict[str, Any] = {}
    if feishu_export or is_feishu_doc_url(result.final_url) or is_feishu_doc_url(result.requested_url):
        export_stats = dict(feishu_export.get("markdown_stats") or feishu_export_metadata.get("markdown_stats") or {})
        if not export_stats and any(feishu_export.get(key) is not None for key in ("text_chars", "heading_count", "image_count", "table_count")):
            export_stats = {
                "text_chars": int(feishu_export.get("text_chars") or 0),
                "heading_count": int(feishu_export.get("heading_count") or 0),
                "image_count": int(feishu_export.get("image_count") or 0),
                "table_count": int(feishu_export.get("table_count") or 0),
            }
        browser_reference_markdown = ""
        browser_source = ""
        if result.browser_visible_markdown:
            browser_reference_markdown = result.browser_visible_markdown
            browser_source = "browser_visible_markdown"
        elif result.transport != "feishu_export" and preferred_markdown_text:
            browser_reference_markdown = preferred_markdown_text
            browser_source = "preferred_markdown"
        feishu_comparison = compare_feishu_captures(
            browser_markdown=browser_reference_markdown,
            export_markdown="",
            browser_stats=markdown_capture_stats(browser_reference_markdown),
            export_stats=export_stats or None,
        )
        feishu_comparison.update(
            {
                "browser_source": browser_source,
                "export_source": "feishu_docx_export" if export_stats else "",
                "docx_path": feishu_export.get("docx_path", ""),
            }
        )
    if feishu_export.get("docx_path"):
        files.append(
            {
                "role": "docx_export",
                "path": feishu_export.get("docx_path"),
                "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "source": "feishu_export",
                "bytes": feishu_export.get("bytes", 0),
            }
        )
    xhs_data = (result.site_data.get("xhs") or {}) if isinstance(result.site_data, dict) else {}
    api_response = (result.site_data.get("api_response") or {}) if isinstance(result.site_data, dict) else {}
    for path_key, role, media_type in (
        ("pretty_json_path", "pretty_json", "application/json"),
        ("summary_markdown_path", "json_summary", "text/markdown"),
    ):
        path = api_response.get(path_key)
        if path:
            files.append({"role": role, "path": path, "media_type": media_type, "source": "json_api"})
    endpoint_discovery = (result.site_data.get("endpoint_discovery") or {}) if isinstance(result.site_data, dict) else {}
    for path_key, role, media_type in (
        ("candidates_json_path", "endpoint_candidates", "application/json"),
        ("candidates_markdown_path", "endpoint_candidates_markdown", "text/markdown"),
    ):
        path = endpoint_discovery.get(path_key)
        if path:
            files.append({"role": role, "path": path, "media_type": media_type, "source": "endpoint_discovery"})
    if xhs_data.get("raw_path"):
        files.append(
            {
                "role": "xhs_raw_tool_result",
                "path": xhs_data.get("raw_path"),
                "media_type": "application/json",
                "source": xhs_data.get("source", ""),
            }
        )
    firecrawl = (result.site_data.get("firecrawl") or {}) if isinstance(result.site_data, dict) else {}
    for key, role, media_type in (
        ("markdown_path", "firecrawl_markdown", "text/markdown"),
        ("html_path", "firecrawl_html", "text/html"),
        ("result_json_path", "firecrawl_result", "application/json"),
        ("screenshot_path", "firecrawl_screenshot", "image/png"),
    ):
        path = firecrawl.get(key)
        if path:
            files.append({"role": role, "path": path, "media_type": media_type, "source": "firecrawl"})
    monolith = (result.site_data.get("monolith") or {}) if isinstance(result.site_data, dict) else {}
    if monolith.get("output_path"):
        files.append(
            {
                "role": "monolith_snapshot",
                "path": monolith.get("output_path"),
                "media_type": "multipart/related" if (monolith.get("config") or {}).get("mhtml") else "text/html",
                "source": "monolith",
                "bytes": monolith.get("bytes", 0),
            }
        )
    scrapling = (result.site_data.get("scrapling") or {}) if isinstance(result.site_data, dict) else {}
    for key, role, media_type in (
        ("html_path", "scrapling_html", "text/html"),
        ("selector_text_path", "scrapling_selector_text", "text/plain"),
        ("result_json_path", "scrapling_result", "application/json"),
    ):
        path = scrapling.get(key)
        if path:
            files.append({"role": role, "path": path, "media_type": media_type, "source": "scrapling"})
    crawl4ai = (result.site_data.get("crawl4ai") or {}) if isinstance(result.site_data, dict) else {}
    for key, role, media_type in (
        ("markdown_path", "crawl4ai_markdown", "text/markdown"),
        ("cleaned_html_path", "crawl4ai_cleaned_html", "text/html"),
        ("html_path", "crawl4ai_html", "text/html"),
        ("result_json_path", "crawl4ai_result", "application/json"),
        ("screenshot_path", "crawl4ai_screenshot", "image/png"),
        ("pdf_path", "crawl4ai_pdf", "application/pdf"),
    ):
        path = crawl4ai.get(key)
        if path:
            files.append(
                {
                    "role": role,
                    "path": path,
                    "media_type": media_type,
                    "source": "crawl4ai",
                }
            )
    preferred_views = _build_preferred_views(
        result,
        files=files,
        preferred_readable=preferred_readable,
        preferred_markdown=preferred_markdown,
        image_gallery={
            "path": archive.get("image_gallery_path", ""),
            "sha256": archive.get("image_gallery_sha256", ""),
            "counts": archive.get("image_gallery_counts", {}) or image_gallery.get("counts", {}),
        },
        attachments=attachments,
        completeness=completeness,
        asset_completeness=asset_completeness,
    )
    review_recommendation = _review_recommendation(completeness, preferred_readable, asset_completeness)
    content_completeness = _content_completeness(
        result=result,
        completeness=completeness,
        asset_completeness=asset_completeness,
        preferred_markdown=preferred_markdown,
        attachments=attachments,
        feishu_comparison=feishu_comparison,
        review_recommendation=review_recommendation,
    )
    site_strategy = (result.site_data.get("site_strategy") or {}) if isinstance(result.site_data, dict) else {}
    quality_expected = (result.site_data.get("quality_expected") or {}) if isinstance(result.site_data, dict) else {}
    try:
        from .quality import evaluate_quality_gate

        quality_gate = evaluate_quality_gate(
            {
                "url": result.final_url or result.requested_url,
                "expected_strategy": site_strategy.get("site_key", ""),
                "expected_route": site_strategy.get("kind", ""),
                "login_required": bool(site_strategy.get("login_required_hint")),
                "minimum": quality_expected.get("minimum", {}),
            },
            {
                "status": result.status,
                "transport": result.transport,
                "strategy": site_strategy.get("site_key", ""),
                "route": site_strategy.get("kind", ""),
                "text_chars": preferred_markdown.get("chars", 0),
                "images": (preferred_markdown.get("structure") or {}).get("image_count", 0),
                "tables": _markdown_table_count(preferred_markdown_text or result.extracted_text),
                "headings": (preferred_markdown.get("structure") or {}).get("heading_count", 0),
                "preferred_view_count": len((preferred_views.get("all") or [])),
                "quality_status": (result.extraction_quality or {}).get("status", ""),
                "content_completeness_status": content_completeness.get("overall_status", ""),
                "archive_path": archive.get("archive_path", ""),
            },
            archive_dir=str(Path(str(archive.get("archive_path") or "")).parent if archive.get("archive_path") else ""),
        )
    except Exception as exc:
        quality_gate = {
            "version": 1,
            "passed": False,
            "failures": [{"code": "quality_gate_error", "actual": f"{type(exc).__name__}:{str(exc)[:200]}"}],
            "rerun_strategy": "manual_review",
            "recommended_next_action": "manual_review",
            "rerun_command": "",
        }
    return {
        "schema_version": result.schema_version,
        "requested_url": result.requested_url,
        "final_url": result.final_url,
        "title": result.title,
        "transport": result.transport,
        "fetched_at": result.fetched_at,
        "hashes": {
            "raw_sha256": result.raw_sha256,
            "extracted_sha256": result.extracted_sha256,
            "archive_hash": archive.get("archive_hash", ""),
        },
        "files": files,
        "page_snapshots": {
            "raw_snapshot_path": archive.get("raw_snapshot_path", ""),
            "page_html_path": result.page_html_path,
            "singlefile_path": result.singlefile_path,
            "screenshot_path": result.screenshot_path,
        },
        "preferred_views": preferred_views,
        "preferred_markdown": preferred_markdown,
        "browser_visible": {
            "path": result.browser_visible_path,
            "html_path": result.browser_visible_html_path,
            "block_count": len(result.browser_visible_blocks),
            "markdown_chars": len(" ".join((result.browser_visible_markdown or "").split())),
            "metrics": result.site_data.get("browser_visible", {}).get("metrics", {}) if isinstance(result.site_data, dict) else {},
            "blocks": result.browser_visible_blocks,
            "dom_blocks": result.browser_visible_blocks,
            "dom_outline": browser_visible_outline,
            "markdown_outline": browser_visible_markdown_index["outline"],
            "markdown_images": browser_visible_markdown_index["images"],
            "markdown_blocks": browser_visible_markdown_index["blocks"],
            "markdown_structure": {
                "heading_count": browser_visible_markdown_index["heading_count"],
                "dom_heading_count": len(browser_visible_outline),
                "image_count": browser_visible_markdown_index["image_count"],
                "block_count": len(browser_visible_markdown_index["blocks"]),
            },
        },
        "images": result.images,
        "image_assets": image_assets,
        "image_gallery": {
            "path": archive.get("image_gallery_path", ""),
            "sha256": archive.get("image_gallery_sha256", ""),
            "counts": archive.get("image_gallery_counts", {}) or image_gallery.get("counts", {}),
        },
        "markdown_outline": markdown_index["outline"],
        "markdown_images": markdown_index["images"],
        "markdown_blocks": markdown_index["blocks"],
        "markdown_structure": {
            "heading_count": markdown_index["heading_count"],
            "image_count": markdown_index["image_count"],
            "table_count": _markdown_table_count(result.extracted_text),
            "block_count": len(markdown_index["blocks"]),
        },
        "completeness_check": completeness,
        "asset_completeness_check": asset_completeness,
        "content_completeness": content_completeness,
        "quality_gate": quality_gate,
        "api_response": api_response,
        "endpoint_candidates": result.endpoint_candidates,
        "evidence_chain": _build_evidence_chain(result),
        "capture_completeness": result.capture_completeness,
        "feishu_comparison": feishu_comparison,
        "preferred_readable": preferred_readable,
        "review_recommendation": review_recommendation,
        "attachments": attachments,
        "links": result.links,
        "site_data": result.site_data,
        "extraction_engine": result.extraction_engine,
        "extraction_quality": result.extraction_quality,
        "extracted_metadata": result.extracted_metadata,
        "errors": result.errors,
    }


def _apply_capture_completeness(result: FetchResult, raw: str) -> None:
    report = inspect_capture(raw, result.extracted_text, page_url=result.requested_url,
                             final_url=result.final_url, http_status=result.http_status,
                             payload=result.payload, image_assets=result.image_assets)
    report["decoded_html_sha256"] = report["source_sha256"]
    report["source_sha256"] = result.raw_sha256
    if "html" not in result.content_type.lower() and report["access"]["status"] != "access_blocked":
        report["status"] = "not_applicable_non_html"
        report["manual_capture"] = {}
    for image in report["body_images"]["nodes"]:
        image["source_sha256"] = result.raw_sha256
    result.capture_completeness = report
    quality = result.extraction_quality
    if report["access"]["status"] == "access_blocked":
        result.status = "access_blocked"
        result.extracted_text = ""
        result.extracted_sha256 = sha256_text("")
        quality.update(status="access_blocked", access_blocked=True,
                       recommendation=report["manual_capture"]["instructions"],
                       recovery_hints=["manual_capture_required"])
    elif report["dom_links"]["missing"]:
        quality.update(status="suspect_incomplete", dom_link_text_missing=True,
                       recommendation=report["manual_capture"]["instructions"])
        if result.status == "ok":
            result.status = "partial"
    if report.get("text_blocks", {}).get("missing") or report.get("text_blocks", {}).get("budget_exceeded"):
        if result.status != "access_blocked":
            quality.update(status="suspect_incomplete", dom_body_text_missing=True,
                           recommendation=report["manual_capture"].get("instructions", "Review saved body blocks"))
            if result.status == "ok":
                result.status = "partial"
    result.assess_validity()


def structure_content(
    request: FetchRequest,
    raw: str,
    content_type: str,
    final_url: str,
    transport: str,
    errors: list[str],
    extras: dict[str, Any] | None = None,
) -> FetchResult:
    fetched_at = now_iso()
    extras = extras or {}
    payload = dict(getattr(raw, "payload", extras.get("payload", {})))
    original_bytes = getattr(raw, "raw_bytes", None)
    http_status = getattr(raw, "http_status", extras.get("http_status"))
    blocked = access_boundary(raw, final_url, http_status)["status"] == "access_blocked"
    if blocked:
        request = replace(request, check_robots=False, download_images=False, save_singlefile=False,
                          snapshot_backend="", compare_feishu_export=False)
    is_html = "html" in (content_type or "").lower() or "<html" in raw.lower() or "<body" in raw.lower()
    if is_html:
        root = parse_html(raw)
        title = extras.get("title") or title_from_html(raw, root)
        extracted_result = extract_content(raw, url=final_url, title=title, extractor=request.extractor)
        extracted = extracted_result.text
        links = collect_links(root, final_url)
        images = collect_images(root, final_url)
        selector_results = extract_selector_results(raw, final_url, request.selectors)
        site_data = extract_site_data(final_url, raw, root=root)
    else:
        title = str(extras.get("title") or final_url)
        json_summary = summarize_json_response(raw, url=final_url) if ("json" in (content_type or "").lower() or raw.lstrip().startswith(("{", "["))) else {"is_json": False}
        extracted = str(json_summary.get("summary_markdown") or raw)
        links = []
        images = list(extras.get("images") or [])
        selector_results = []
        site_data = dict(extras.get("site_data") or {})
        if json_summary.get("is_json"):
            site_data["api_response"] = {key: value for key, value in json_summary.items() if key not in {"pretty_json", "summary_markdown"}}
            extras["extracted_metadata"] = {**dict(extras.get("extracted_metadata") or {}), "api_response": json_summary}
        extracted_result = None

    browser_visible_blocks = list(extras.get("browser_visible_blocks") or [])
    browser_visible_markdown = str(extras.get("browser_visible_markdown") or "")
    browser_visible_metrics = dict(extras.get("browser_visible_metrics") or {})
    if browser_visible_blocks or browser_visible_markdown:
        site_data = dict(site_data or {})
        site_data["browser_visible"] = {
            "block_count": len(browser_visible_blocks),
            "markdown_chars": len(" ".join(browser_visible_markdown.split())),
            "metrics": browser_visible_metrics,
        }
    if isinstance(extras.get("site_data"), dict):
        site_data = {**dict(site_data or {}), **dict(extras.get("site_data") or {})}
    if request.site_strategy:
        site_data = dict(site_data or {})
        site_data["site_strategy"] = dict(request.site_strategy)

    extraction_engine = extracted_result.engine if extracted_result else "raw"
    extraction_quality = extracted_result.quality if extracted_result else quality_for(extracted, title=title, url=final_url, engine="raw")
    extracted_metadata = extracted_result.metadata if extracted_result else dict(extras.get("extracted_metadata") or {})
    if browser_visible_markdown and _visible_markdown_is_better(browser_visible_markdown, extracted, extraction_quality):
        extracted = browser_visible_markdown
        extraction_engine = "browser_visible_dom"
        extraction_quality = _browser_visible_quality(
            browser_visible_markdown,
            title=title,
            url=final_url,
            metadata={
                "block_count": len(browser_visible_blocks),
                "heading_count": int((browser_visible_metrics.get("type_counts") or {}).get("heading") or 0),
                "image_count": int((browser_visible_metrics.get("type_counts") or {}).get("image") or 0),
                "paragraph_count": int((browser_visible_metrics.get("type_counts") or {}).get("paragraph") or 0),
                "list_item_count": int((browser_visible_metrics.get("type_counts") or {}).get("list_item") or 0),
            },
            original_quality=extraction_quality,
        )
        extracted_metadata = {
            **dict(extracted_metadata or {}),
            "browser_visible_metrics": browser_visible_metrics,
            "browser_visible_dom": {
                "used_as_extracted_text": True,
                "block_count": len(browser_visible_blocks),
                "markdown_chars": len(" ".join(browser_visible_markdown.split())),
            },
        }
        errors = [*errors, "extraction:browser_visible_dom_used"]

    site_structure_quality = site_data.get("structure_quality", {}) if isinstance(site_data, dict) else {}
    virtual_placeholder_height = float(site_structure_quality.get("virtual_placeholder_height") or 0)
    site_quality_reasons: list[str] = []
    if virtual_placeholder_height >= 5000:
        site_quality_reasons.append(f"feishu virtualized document still has a large unrendered placeholder ({int(virtual_placeholder_height)}px)")
    if bool(site_structure_quality.get("anonymous_or_login_prompt")):
        site_quality_reasons.append("feishu/lark page shows an anonymous or login prompt; captured content may be a public preview")
    missing_ssr_heading_count = int(site_structure_quality.get("missing_ssr_heading_count") or 0)
    if missing_ssr_heading_count:
        missing_headings = site_data.get("missing_ssr_headings") if isinstance(site_data, dict) else []
        sample = ", ".join(str(item) for item in list(missing_headings or [])[:5])
        if sample:
            site_quality_reasons.append(f"feishu/lark rendered text is missing SSR catalog headings: {sample}")
        else:
            site_quality_reasons.append(f"feishu/lark rendered text is missing {missing_ssr_heading_count} SSR catalog headings")
    if site_quality_reasons and extraction_quality.get("status") not in {"empty", "too_short"}:
        extraction_quality = {
            **extraction_quality,
            "status": "suspect_incomplete",
            "reasons": [
                *list(extraction_quality.get("reasons") or []),
                *site_quality_reasons,
            ],
            "recommendation": "retry with an authenticated browser profile, Feishu export/API route, or a browser session that is already logged in",
        }

    robots = robots_hint(final_url, request.user_agent, timeout_seconds=min(request.timeout_seconds, 8)) if request.check_robots and not payload.get("binary") and (http_status is None or 200 <= http_status < 300) else {
        "checked": False,
        "allowed": None,
        "warning": "disabled",
    }

    extraction_errors = extracted_result.errors if extracted_result else []
    result = FetchResult(
        requested_url=request.url,
        final_url=final_url,
        status="ok",
        transport=transport,
        schema_version=RESULT_SCHEMA_VERSION,
        content_type=content_type,
        fetched_at=fetched_at,
        title=title,
        raw_snapshot=str(raw) if request.save_raw else "",
        extracted_text=extracted if request.save_extracted else "",
        links=links,
        images=images,
        selector_results=selector_results,
        site_data=site_data,
        extraction_engine=extraction_engine,
        extraction_quality=extraction_quality,
        extracted_metadata=extracted_metadata,
        raw_sha256=payload.get("sha256") or sha256_text(raw),
        extracted_sha256=sha256_text(extracted),
        robots=robots,
        errors=[*errors, *extraction_errors],
        page_html_path=extras.get("page_html_path", ""),
        screenshot_path=extras.get("screenshot_path", ""),
        browser_visible_markdown=browser_visible_markdown,
        browser_visible_blocks=browser_visible_blocks,
        endpoint_candidates=list((site_data.get("endpoint_discovery") or {}).get("candidates") or []),
        evidence_chain=list(extras.get("evidence_chain") or []),
        payload=payload,
        http_status=http_status,
        image_assets=list(extras.get("image_assets") or []),
    )
    _augment_quality_diagnostics(result, raw)
    if payload.get("binary"):
        result.extraction_engine = "binary_pending_conversion"
        result.extraction_quality.update(status="binary_pending_conversion", recommendation="convert the archived original using convert-attachment")
    if payload.get("truncated"):
        result.errors.append("payload_truncated:max_bytes")
    if http_status is not None and not 200 <= http_status < 300:
        result.errors.append(f"http_status:{http_status}")
    result.assess_validity()
    _apply_capture_completeness(result, raw)
    extracted = result.extracted_text

    if request.archive_dir:
        try:
            result.archive = write_archive(
                Path(request.archive_dir),
                stable_id("web", result.final_url, result.raw_sha256),
                url=result.final_url,
                title=result.title,
                raw_snapshot=raw,
                extracted_text=extracted,
                metadata=result.to_dict(include_raw=False),
                raw_bytes=original_bytes,
                binary_extension=payload.get("extension", ""),
            )
        except Exception as exc:
            result.errors.append(f"archive:{type(exc).__name__}:{str(exc)[:200]}")
        if request.use_browser:
            page_path = archive_child_path(result.archive, "page.html")
            if page_path:
                existing_page = Path(result.page_html_path) if result.page_html_path else None
                if existing_page and existing_page.exists() and existing_page.resolve() != page_path.resolve():
                    shutil.copyfile(existing_page, page_path)
                else:
                    page_path.write_text(raw or "", encoding="utf-8", newline="\n")
                result.page_html_path = str(page_path)
            if result.screenshot_path:
                screenshot_path = archive_child_path(result.archive, "screenshot.png")
                existing_screenshot = Path(result.screenshot_path)
                if screenshot_path and existing_screenshot.exists() and existing_screenshot.resolve() != screenshot_path.resolve():
                    shutil.copyfile(existing_screenshot, screenshot_path)
                    result.screenshot_path = str(screenshot_path)
        source_docx_path = str(((extras.get("site_data") or {}).get("feishu_export") or {}).get("docx_path") or "")
        if source_docx_path:
            export_path = archive_child_path(result.archive, "feishu_export", Path(source_docx_path).name)
            if export_path and Path(source_docx_path).exists() and Path(source_docx_path).resolve() != export_path.resolve():
                shutil.copyfile(source_docx_path, export_path)
                result.site_data.setdefault("feishu_export", {})["docx_path"] = str(export_path)
                result.extracted_metadata.setdefault("feishu_export", {})["docx_path"] = str(export_path)
        _write_crawl4ai_artifacts(result, extras)
        _write_firecrawl_artifacts(result, extras)
        _write_scrapling_artifacts(result, extras)
        _write_json_api_artifacts(result)
        _write_endpoint_candidate_artifacts(result)
        browser_image_assets = extras.get("image_assets") or []
        if browser_image_assets:
            result.image_assets = browser_image_assets
            _adopt_browser_image_assets(result)
        elif not blocked and request.download_images and result.images:
            assets_path = archive_child_path(result.archive, "image_assets", ".keep")
            if assets_path:
                assets_path.unlink(missing_ok=True)
                result.image_assets = download_images(
                    result.images,
                    assets_path.parent,
                    timeout_seconds=request.timeout_seconds,
                    user_agent=request.user_agent,
                )
        _apply_capture_completeness(result, raw)
        _rewrite_markdown_image_links(result)
        _write_browser_visible_markdown(result)
        _write_browser_visible_html(result)
        _write_preferred_markdown(result)
        _write_readable_html(result)
        _write_preferred_readable_html(result)
        _attach_feishu_export_comparison(result, request)
        _write_image_gallery(result)
        _write_reading_report(result)
        _write_archive_home(result)
        if request.save_singlefile:
            _run_singlefile(request, result)
        _run_monolith_snapshot(request, result)
        _refresh_archive_metadata(result)
    if request.cache_dir:
        written_path = write_cache(request.cache_dir, request.url, result.to_dict(include_raw=False))
        if written_path:
            result.cache["written_path"] = written_path
        else:
            result.cache["write_warning"] = "cache_write_failed"
    return result


def fetch_content(request: FetchRequest) -> FetchResult:
    normalized_backend = normalize_fetch_backend(request.backend)
    if normalized_backend is None:
        supported = ",".join(item["name"] for item in backend_catalog() if item["selectable_fetch_backend"])
        return FetchResult(
            requested_url=request.url,
            final_url=request.url,
            status="failed",
            transport="none",
            schema_version=RESULT_SCHEMA_VERSION,
            fetched_at=now_iso(),
            errors=[f"unsupported_backend:{request.backend}: supported={supported}"],
        )
    request.backend = normalized_backend
    profiled = apply_profile(
        request.fetch_profile,
        {
            "selectors": request.selectors,
            "timeout_seconds": request.timeout_seconds,
            "browser_wait_until": request.browser_wait_until,
            "browser_scroll_steps": request.browser_scroll_steps,
            "browser_settle_ms": request.browser_settle_ms,
        },
    )
    request.selectors = profiled.get("selectors") or request.selectors
    request.timeout_seconds = int(profiled.get("timeout_seconds") or request.timeout_seconds)
    request.use_browser = bool(profiled.get("use_browser", request.use_browser))
    request.browser_auto_scroll = bool(profiled.get("browser_auto_scroll", request.browser_auto_scroll))
    request.browser_wait_until = str(profiled.get("browser_wait_until") or request.browser_wait_until)
    request.browser_scroll_steps = int(profiled.get("browser_scroll_steps") or request.browser_scroll_steps)
    request.browser_settle_ms = int(profiled.get("browser_settle_ms") or request.browser_settle_ms)

    if request.backend == "browser":
        request.use_browser = True
    elif request.backend in {"direct", "crawl4ai", "firecrawl", "scrapling", "werss", "wechat_download_api", "feishu_export", "xhs_redbook_cli", "xhs_browser_state", "xhs_auto"}:
        request.use_browser = False
        request.use_web_access = False

    if request.archive_dir:
        preflight = preflight_archive_dir(request.archive_dir)
        if not preflight.get("ok"):
            return FetchResult(
                requested_url=request.url,
                final_url=request.url,
                status="failed",
                transport="none",
                schema_version=RESULT_SCHEMA_VERSION,
                fetched_at=now_iso(),
                extraction_quality={"status": "archive_unavailable", "reasons": [preflight.get("error", "archive_dir_not_writable")]},
                errors=[f"archive_preflight:{preflight.get('error_code')}: {preflight.get('error')}"],
            )

    if request.cache_dir:
        cached = read_cache(request.cache_dir, request.url, max_age_seconds=request.cache_max_age_seconds)
        if cached and int(cached.get("schema_version") or 0) >= RESULT_SCHEMA_VERSION:
            cached["cache"] = {**(cached.get("cache") or {}), "hit": True}
            return FetchResult(**{k: v for k, v in cached.items() if k in FetchResult.__dataclass_fields__})

    errors: list[str] = []
    provisional_archive: dict[str, Any] | None = None

    if request.discover_endpoints_only:
        try:
            raw, content_type, final_url = direct_fetch(request)
            sources, discovery_errors = _discover_endpoint_sources(raw, final_url, request)
            candidates = discover_endpoint_candidates(sources, page_url=final_url)
            errors.extend(discovery_errors)
            extras = {
                "site_data": {
                    "endpoint_discovery": {
                        "mode": "dry_run",
                        "policy": "candidate_only_not_fetched",
                        "source_count": len(sources),
                        "candidate_count": len(candidates),
                        "sources": [{key: value for key, value in source.items() if key != "content"} for source in sources],
                        "candidates": candidates,
                    }
                },
                "evidence_chain": [{"stage": "endpoint_discovery_dry_run", "url": final_url, "policy": "candidate_only_not_fetched"}],
            }
            return structure_content(request, raw, content_type, final_url, "endpoint_discovery", errors, extras)
        except Exception as exc:
            errors.append(f"endpoint_discovery:{type(exc).__name__}:{str(exc)[:200]}")
            return FetchResult(
                requested_url=request.url,
                final_url=request.url,
                status="failed",
                transport="endpoint_discovery",
                schema_version=RESULT_SCHEMA_VERSION,
                fetched_at=now_iso(),
                errors=errors,
            )
    should_try_xhs = request.backend in {"xhs_redbook_cli", "xhs_browser_state", "xhs_auto"} or (
        request.backend == "auto" and is_xhs_url(request.url)
    )
    if should_try_xhs:
        provisional_xhs_archive = None
        if request.archive_dir:
            provisional_xhs_archive = {
                "archive_path": str(Path(request.archive_dir) / safe_browser_archive_id(f"xhs:{request.url}")),
            }
            Path(provisional_xhs_archive["archive_path"]).mkdir(parents=True, exist_ok=True)
        try:
            if request.backend == "xhs_redbook_cli":
                raw, content_type, final_url, transport, extras = xhs_redbook_cli_fetch(request, provisional_xhs_archive)
            elif request.backend == "xhs_browser_state":
                raw, content_type, final_url, transport, extras = xhs_browser_state_fetch(request, provisional_xhs_archive)
            else:
                raw, content_type, final_url, transport, extras = xhs_auto_fetch(request, provisional_xhs_archive)
            return structure_content(request, raw, content_type, final_url, transport, errors, extras)
        except Exception as exc:
            errors.append(f"xhs:{type(exc).__name__}:{str(exc)[:300]}")
            if request.backend in {"xhs_redbook_cli", "xhs_browser_state", "xhs_auto"}:
                return FetchResult(
                    requested_url=request.url,
                    final_url=request.url,
                    status="failed",
                    transport="none",
                    schema_version=RESULT_SCHEMA_VERSION,
                    fetched_at=now_iso(),
                    errors=errors,
                )

    should_try_feishu_export = request.backend == "feishu_export" or (
        request.backend == "auto" and is_feishu_doc_url(request.url) and feishu_credentials_available()
    )
    if should_try_feishu_export:
        try:
            provisional_export_archive = None
            if request.archive_dir:
                provisional_export_archive = {
                    "archive_path": str(Path(request.archive_dir) / safe_browser_archive_id(f"feishu-export:{request.url}")),
                }
                Path(provisional_export_archive["archive_path"]).mkdir(parents=True, exist_ok=True)
            raw, content_type, final_url, transport, extras = feishu_export_fetch(request, provisional_export_archive)
            return structure_content(request, raw, content_type, final_url, transport, errors, extras)
        except FeishuExportUnavailable as exc:
            errors.append(f"feishu_export:{str(exc)[:200]}")
            if request.backend == "feishu_export":
                return FetchResult(
                    requested_url=request.url,
                    final_url=request.url,
                    status="failed",
                    transport="none",
                    schema_version=RESULT_SCHEMA_VERSION,
                    fetched_at=now_iso(),
                    errors=errors,
                )
        except Exception as exc:
            errors.append(f"feishu_export:{type(exc).__name__}:{str(exc)[:200]}")
            if request.backend == "feishu_export":
                return FetchResult(
                    requested_url=request.url,
                    final_url=request.url,
                    status="failed",
                    transport="none",
                    schema_version=RESULT_SCHEMA_VERSION,
                    fetched_at=now_iso(),
                    errors=errors,
                )

    if request.archive_dir and request.use_browser:
        provisional_archive = {
            "archive_path": str(Path(request.archive_dir) / safe_browser_archive_id(request.url)),
        }
        Path(provisional_archive["archive_path"]).mkdir(parents=True, exist_ok=True)

    if request.use_browser:
        try:
            raw, content_type, final_url, transport, extras = browser_fetch(request, provisional_archive)
            result = structure_content(request, raw, content_type, final_url, transport, errors, extras)
            _cleanup_provisional_archive(provisional_archive, result)
            if _quality_needs_browser_fallback(result):
                fallback_request = replace(request, archive_dir="") if (
                    _is_feishu_reader_fallback_url(request.url) and _browser_result_has_preservable_evidence(result)
                ) else request
                fallback = _try_jina_reader_fallback(fallback_request, [*result.errors, _fallback_reason(result)])
                if fallback:
                    if fallback_request.archive_dir == "":
                        result.errors = [
                            *result.errors,
                            "fallback:jina_reader_available_but_browser_capture_preserved",
                            *[item for item in fallback.errors if item not in result.errors],
                        ]
                        return result
                    if not _quality_needs_browser_fallback(fallback):
                        _cleanup_provisional_archive(provisional_archive, fallback)
                        return fallback
                    result.errors = [*result.errors, *[item for item in fallback.errors if item not in result.errors]]
            return result
        except Exception as exc:
            diagnostic = normalize_browser_error(f"{type(exc).__name__}:{str(exc)[:300]}")
            errors.append(f"browser:{type(exc).__name__}:{diagnostic['code']}:{diagnostic['message']}")

    if request.backend == "crawl4ai":
        try:
            raw, content_type, final_url, transport, extras = crawl4ai_fetch(request)
            return structure_content(request, raw, content_type, final_url, transport, errors, extras)
        except Exception as exc:
            errors.append(f"crawl4ai:{type(exc).__name__}:{str(exc)[:200]}")
            return FetchResult(
                requested_url=request.url,
                final_url=request.url,
                status="failed",
                transport="none",
                schema_version=RESULT_SCHEMA_VERSION,
                fetched_at=now_iso(),
                errors=errors,
            )

    if request.backend == "firecrawl":
        try:
            raw, content_type, final_url, transport, extras = firecrawl_fetch(request)
            return structure_content(request, raw, content_type, final_url, transport, errors, extras)
        except Exception as exc:
            errors.append(f"firecrawl:{type(exc).__name__}:{str(exc)[:200]}")
            return FetchResult(
                requested_url=request.url,
                final_url=request.url,
                status="failed",
                transport="none",
                schema_version=RESULT_SCHEMA_VERSION,
                fetched_at=now_iso(),
                errors=errors,
            )

    if request.backend == "scrapling":
        try:
            raw, content_type, final_url, transport, extras = scrapling_fetch(request)
            return structure_content(request, raw, content_type, final_url, transport, errors, extras)
        except Exception as exc:
            errors.append(f"scrapling:{type(exc).__name__}:{str(exc)[:300]}")
            return FetchResult(
                requested_url=request.url,
                final_url=request.url,
                status="failed",
                transport="none",
                fetched_at=now_iso(),
                errors=errors,
            )

    if request.backend == "wechat_download_api":
        try:
            article = fetch_wechat_article(
                request.url,
                base_url=request.wechat_api_base_url,
                timeout_seconds=max(30, request.timeout_seconds),
            )
            metadata = {
                "provider": article.get("provider", "wechat-download-api"),
                "api_base_url": article.get("api_base_url", ""),
                "author": article.get("author", ""),
                "publish_time": article.get("publish_time", 0),
                "publish_time_str": article.get("publish_time_str", ""),
                "image_count": len(article.get("images") or []),
            }
            extras = {
                "title": str(article.get("title") or ""),
                "images": [{"url": url, "alt": ""} for url in article.get("images") or []],
                "site_data": {"wechat_download_api": metadata},
                "extracted_metadata": {"wechat_download_api": metadata},
                "evidence_chain": [
                    {
                        "stage": "wechat_download_api_article",
                        "url": str(article.get("url") or request.url),
                        "provider": "wechat-download-api",
                    }
                ],
            }
            return structure_content(
                request,
                str(article["content"]),
                "text/html; charset=utf-8",
                str(article.get("url") or request.url),
                "wechat_download_api",
                errors,
                extras,
            )
        except Exception as exc:
            errors.append(f"wechat_download_api:{type(exc).__name__}:{str(exc)[:300]}")
            return FetchResult(
                requested_url=request.url,
                final_url=request.url,
                status="failed",
                transport="none",
                fetched_at=now_iso(),
                errors=errors,
            )

    if request.backend == "werss":
        try:
            werss = run_werss(
                WerssRequest(
                    url=request.url,
                    article_id=request.werss_article_id,
                    timeout_seconds=request.timeout_seconds,
                    base_url=request.werss_base_url,
                    api_key_env=request.werss_api_key_env,
                )
            )
            article = dict(werss.get("article") or {})
            extras = {
                "title": str(article.get("title") or ""),
                "site_data": {"werss": {"config": werss.get("config") or {}, "status": werss.get("status_code", 0), "article": article}},
                "extracted_metadata": {"werss": {"config": werss.get("config") or {}, "status": werss.get("status_code", 0), "article": article}},
            }
            return structure_content(request, str(werss["html"]), "text/html", str(werss["url"]), "werss", errors, extras)
        except Exception as exc:
            errors.append(f"werss:{type(exc).__name__}:{str(exc)[:300]}")
            return FetchResult(requested_url=request.url, final_url=request.url, status="failed", transport="none", fetched_at=now_iso(), errors=errors)
    try:
        raw, content_type, final_url = direct_fetch(request)
        direct_request = request
        if request.backend == "auto" and not request.use_browser:
            direct_request = replace(request, archive_dir="")
        direct_result = structure_content(direct_request, raw, content_type, final_url, "direct", errors)
        if not direct_result.transport_ok or direct_result.payload.get("binary") or direct_result.payload.get("truncated"):
            if request.archive_dir and not direct_result.archive:
                return structure_content(request, raw, content_type, final_url, "direct", direct_result.errors)
            return direct_result
        if _quality_needs_browser_fallback(direct_result):
            fallback = _try_jina_reader_fallback(request, [*direct_result.errors, _fallback_reason(direct_result)])
            if fallback:
                return fallback
        if request.backend == "auto" and not request.use_browser and _quality_needs_browser_fallback(direct_result):
            fallback_errors = [*direct_result.errors, _fallback_reason(direct_result), "fallback:browser_after_low_quality_direct"]
            try:
                browser_request = replace(request, use_browser=True)
                provisional_archive = None
                if browser_request.archive_dir:
                    provisional_archive = {
                        "archive_path": str(Path(browser_request.archive_dir) / safe_browser_archive_id(browser_request.url)),
                    }
                    Path(provisional_archive["archive_path"]).mkdir(parents=True, exist_ok=True)
                raw, content_type, final_url, transport, extras = browser_fetch(browser_request, provisional_archive)
                result = structure_content(browser_request, raw, content_type, final_url, transport, fallback_errors, extras)
                _cleanup_provisional_archive(provisional_archive, result)
                return result
            except Exception as exc:
                diagnostic = normalize_browser_error(f"{type(exc).__name__}:{str(exc)[:300]}")
                direct_result.errors.append(f"browser_fallback:{type(exc).__name__}:{diagnostic['code']}:{diagnostic['message']}")
            firecrawl_fallback = _try_auto_firecrawl_fallback(request, [*direct_result.errors, _fallback_reason(direct_result)], direct_result)
            if firecrawl_fallback and not _quality_needs_browser_fallback(firecrawl_fallback):
                return firecrawl_fallback
        if request.archive_dir and not direct_result.archive:
            direct_result = structure_content(request, raw, content_type, final_url, "direct", direct_result.errors)
        return direct_result
    except Exception as exc:
        errors.append(f"direct:{type(exc).__name__}:{str(exc)[:200]}")

    if request.use_web_access:
        try:
            raw, content_type, final_url, transport = web_access_fetch(request)
            return structure_content(request, raw, content_type, final_url, transport, errors)
        except Exception as exc:
            errors.append(f"web_access:{type(exc).__name__}:{str(exc)[:200]}")

    firecrawl_fallback = _try_auto_firecrawl_fallback(request, [*errors, "fallback:firecrawl_auto_after_fetch_failure"])
    if firecrawl_fallback and not _quality_needs_browser_fallback(firecrawl_fallback):
        return firecrawl_fallback

    return FetchResult(
        requested_url=request.url,
        final_url=request.url,
        status="failed",
        transport="none",
        schema_version=RESULT_SCHEMA_VERSION,
        fetched_at=now_iso(),
        errors=errors,
    )


def safe_browser_archive_id(url: str) -> str:
    return stable_id("browser-pre", url)
