from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .browser_fetch import COMMON_LOGIN_URLS, BrowserProfileLoginOptions, browser_profile_status, default_profile_dir, open_browser_profile_for_login
from .artifact_lifecycle import LifecycleAdapterError, capture_disk_preflight
from .backend_registry import fetch_backend_choices
from .config import load_config, web_access_base_url, wechat_download_api_base_url
from .core import FetchRequest, fetch_content
from .document_router import fetch_document_link
from .extractors import EXTRACTOR_CHOICES
from .zsxq_cli import ZsxqRequest, run_zsxq

BACKEND_CHOICES = fetch_backend_choices()


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _block_context_label(block: dict) -> str:
    if not isinstance(block, dict) or not block:
        return ""
    block_type = str(block.get("type") or "")
    line = str(block.get("line") or "")
    text = str(block.get("text") or block.get("alt") or block.get("src") or "")
    prefix = f"{block_type} L{line}".strip()
    return f"{prefix}: {text}" if text else prefix


def _large_artifact_disk_gate(args: argparse.Namespace) -> dict | None:
    """Return a disk receipt for durable large-output commands, or block early."""

    command = args.command
    target = ""
    operation = ""
    if command == "fetch" and (
        args.download_images
        or args.browser
        or args.prefer_browser
        or args.backend in {"browser", "crawl4ai", "scrapling"}
    ):
        target, operation = args.archive_dir or args.cache_dir, "browser_or_image_capture"
    elif command in {"fetch-doc", "fetch-and-rebuild"}:
        target, operation = args.archive_dir, "document_browser_or_visual_capture"
    elif command == "rebuild-archives":
        target, operation = args.archive_dir, "batch_visual_conversion"
    elif command == "browsertrix":
        target, operation = args.output_dir or args.archive_dir, "batch_browser_archive"
    elif command == "archivebox":
        target, operation = args.archive_dir or args.archivebox_dir, "batch_web_archive"
    elif command == "quality" and args.quality_command == "smoke":
        target, operation = args.output_root, "quality_smoke_batch_archive"
    elif command == "zsxq" and args.zsxq_command == "export-group":
        target, operation = args.archive_dir, "batch_topic_archive"
    elif command == "archive" and args.archive_command == "rerun-batch":
        target, operation = args.archive_dir, "batch_archive_rerun"
    if not target:
        return None
    return capture_disk_preflight(
        target,
        operation=operation,
        estimated_output_bytes=getattr(args, "estimated_output_bytes", None),
    )


def add_output_estimate_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--estimated-output-bytes",
        type=int,
        default=None,
        help=(
            "Expected durable output bytes for disk preflight. Required for batch or unknown-size "
            "capture; single-page browser/image capture has a conservative default."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    config = load_config()
    parser = argparse.ArgumentParser(prog="wcf", description="Fetch and structure web content.")
    sub = parser.add_subparsers(dest="command", required=True)
    body_store = sub.add_parser("body-store", help="Import, plan, recover and export retained public bodies")
    body_store.add_argument("body_arguments", nargs=argparse.REMAINDER)

    fetch = sub.add_parser("fetch", help="Fetch one URL and output structured content.")
    add_output_estimate_argument(fetch)
    fetch.add_argument("url")
    fetch.add_argument("--fetch-profile", default="generic", choices=["generic", "forum", "official_notice", "github_release", "feishu_doc"])
    fetch.add_argument("--backend", default="auto", choices=BACKEND_CHOICES)
    fetch.add_argument("--extractor", default="auto", choices=sorted(EXTRACTOR_CHOICES))
    fetch.add_argument("--timeout", type=int, default=15)
    fetch.add_argument("--selector", action="append", default=[], help="CSS-like selector. Can be repeated.")
    fetch.add_argument("--use-web-access", action="store_true")
    fetch.add_argument("--web-access-url", default=os.environ.get("WEB_ACCESS_API_URL", web_access_base_url(config)))
    fetch.add_argument("--prefer-browser", action="store_true")
    fetch.add_argument("--browser", action="store_true", help="Use local Playwright browser fetch before direct HTTP.")
    fetch.add_argument("--headless", action="store_true", help="Run browser fetch headlessly.")
    fetch.add_argument("--profile", default="", help="Chrome profile directory name, e.g. Default or Profile 1.")
    fetch.add_argument("--chrome-user-data-dir", default="", help="Chrome User Data dir to reuse logged-in state.")
    fetch.add_argument("--browser-executable", default="", help="Browser executable path.")
    fetch.add_argument("--storage-state", default="", help="Playwright storageState JSON file.")
    fetch.add_argument("--wait-until", default="load", choices=["load", "domcontentloaded", "networkidle", "commit"])
    fetch.add_argument("--auto-scroll", action="store_true", help="Scroll the browser page before extraction.")
    fetch.add_argument("--dismiss-obstructive-overlays", dest="dismiss_obstructive_overlays", action="store_true", default=True, help="Close obvious ad/promo overlays before extraction; default on.")
    fetch.add_argument("--no-dismiss-obstructive-overlays", dest="dismiss_obstructive_overlays", action="store_false", help="Do not close obstructive overlays before extraction.")
    fetch.add_argument("--scroll-steps", type=int, default=8)
    fetch.add_argument("--screenshot", action="store_true", help="Save a full-page screenshot when using browser mode.")
    fetch.add_argument("--download-images", action="store_true", help="Download discovered image assets into the archive.")
    fetch.add_argument("--singlefile", action="store_true", help="Try to save a SingleFile HTML snapshot if single-file is installed.")
    fetch.add_argument("--snapshot-backend", choices=["none", "monolith"], default="none", help="Optional local snapshot bridge after fetch.")
    fetch.add_argument("--monolith-executable", default="", help="Optional Monolith executable override.")
    fetch.add_argument("--monolith-mhtml", action="store_true", help="Write Monolith snapshot as MHTML.")
    fetch.add_argument("--discover-endpoints-only", action="store_true", help="Dry-run scan HTML/JS for candidate official frontend API endpoints; do not fetch business endpoints.")
    fetch.add_argument("--compare-feishu-export", action="store_true", help="For Feishu/Lark docs, also try official DOCX export and compare it with the browser capture.")
    add_crawl4ai_arguments(fetch)
    add_firecrawl_arguments(fetch)
    add_scrapling_arguments(fetch)
    add_wechat_api_arguments(fetch, wechat_download_api_base_url(config))
    fetch.add_argument("--auth-profile", default="")
    fetch.add_argument("--proxy-session", default="")
    fetch.add_argument("--archive-dir", default="")
    fetch.add_argument("--cache-dir", default="")
    fetch.add_argument("--cache-max-age", type=int, default=0, help="Use cached JSON if newer than this many seconds.")
    fetch.add_argument("--no-robots-check", action="store_true", help="Disable robots.txt compliance hint.")
    fetch.add_argument("--include-raw", action="store_true")
    fetch.add_argument("--format", choices=["json", "markdown"], default="json")

    fetch_doc = sub.add_parser("fetch-doc", help="Fetch a document-like URL with automatic routing.")
    add_output_estimate_argument(fetch_doc)
    fetch_doc.add_argument("url")
    fetch_doc.add_argument("--backend", default="auto", choices=BACKEND_CHOICES)
    fetch_doc.add_argument("--extractor", default="auto", choices=sorted(EXTRACTOR_CHOICES))
    fetch_doc.add_argument("--timeout", type=int, default=30)
    fetch_doc.add_argument("--profile", default="", help="Chrome profile directory name, e.g. Default or Profile 1.")
    fetch_doc.add_argument("--chrome-user-data-dir", default="", help="Chrome User Data dir to reuse logged-in state.")
    fetch_doc.add_argument("--browser-executable", default="", help="Browser executable path.")
    fetch_doc.add_argument("--storage-state", default="", help="Playwright storageState JSON file.")
    fetch_doc.add_argument("--headless", action="store_true")
    fetch_doc.add_argument("--dismiss-obstructive-overlays", dest="dismiss_obstructive_overlays", action="store_true", default=True, help="Close obvious ad/promo overlays before extraction; default on.")
    fetch_doc.add_argument("--no-dismiss-obstructive-overlays", dest="dismiss_obstructive_overlays", action="store_false", help="Do not close obstructive overlays before extraction.")
    fetch_doc.add_argument("--compare-feishu-export", action="store_true", help="Also try official Feishu/Lark DOCX export and write a browser-vs-export comparison.")
    add_crawl4ai_arguments(fetch_doc)
    add_firecrawl_arguments(fetch_doc)
    add_scrapling_arguments(fetch_doc)
    add_wechat_api_arguments(fetch_doc, wechat_download_api_base_url(config))
    fetch_doc.add_argument("--archive-dir", default="")
    fetch_doc.add_argument("--no-robots-check", action="store_true")
    fetch_doc.add_argument("--include-raw", action="store_true")
    fetch_doc.add_argument("--format", choices=["json", "markdown"], default="json")

    fetch_and_rebuild = sub.add_parser("fetch-and-rebuild", help="Fetch a document URL, run archive rebuild, optional visual check, and rebuild final outputs.")
    add_output_estimate_argument(fetch_and_rebuild)
    fetch_and_rebuild.add_argument("url")
    fetch_and_rebuild.add_argument("--backend", default="auto", choices=BACKEND_CHOICES)
    fetch_and_rebuild.add_argument("--extractor", default="auto", choices=sorted(EXTRACTOR_CHOICES))
    fetch_and_rebuild.add_argument("--timeout", type=int, default=30)
    fetch_and_rebuild.add_argument("--profile", default="", help="Chrome profile directory name, e.g. Default or Profile 1.")
    fetch_and_rebuild.add_argument("--chrome-user-data-dir", default="", help="Chrome User Data dir to reuse logged-in state.")
    fetch_and_rebuild.add_argument("--browser-executable", default="", help="Browser executable path.")
    fetch_and_rebuild.add_argument("--storage-state", default="", help="Playwright storageState JSON file.")
    fetch_and_rebuild.add_argument("--headless", action="store_true")
    fetch_and_rebuild.add_argument("--dismiss-obstructive-overlays", dest="dismiss_obstructive_overlays", action="store_true", default=True, help="Close obvious ad/promo overlays before extraction; default on.")
    fetch_and_rebuild.add_argument("--no-dismiss-obstructive-overlays", dest="dismiss_obstructive_overlays", action="store_false", help="Do not close obstructive overlays before extraction.")
    fetch_and_rebuild.add_argument("--compare-feishu-export", action="store_true", help="Also try official Feishu/Lark DOCX export before rebuilding the archive.")
    add_crawl4ai_arguments(fetch_and_rebuild)
    add_firecrawl_arguments(fetch_and_rebuild)
    add_scrapling_arguments(fetch_and_rebuild)
    fetch_and_rebuild.add_argument("--archive-dir", default="")
    fetch_and_rebuild.add_argument("--no-robots-check", action="store_true")
    fetch_and_rebuild.add_argument("--with-visual-check", action="store_true", default=True)
    fetch_and_rebuild.add_argument("--no-visual-check", dest="with_visual_check", action="store_false")
    fetch_and_rebuild.add_argument("--visual-command", default="", help="Path to ebook_markdown_pipeline process-web-archive command.")
    fetch_and_rebuild.add_argument("--visual-timeout", type=int, default=180)
    fetch_and_rebuild.add_argument("--format", choices=["json", "summary"], default="summary")

    rebuild_existing = sub.add_parser("rebuild-existing-archive", help="Run archive rebuild, ebook_markdown_pipeline visual check, and final rebuild for an existing archive.")
    rebuild_existing.add_argument("archive_path")
    rebuild_existing.add_argument("--with-visual-check", action="store_true", default=True)
    rebuild_existing.add_argument("--no-visual-check", dest="with_visual_check", action="store_false")
    rebuild_existing.add_argument("--visual-command", default="", help="Path to ebook_markdown_pipeline process-web-archive command.")
    rebuild_existing.add_argument("--visual-timeout", type=int, default=180)
    rebuild_existing.add_argument("--format", choices=["json", "summary"], default="summary")

    rebuild_archives = sub.add_parser("rebuild-archives", help="Run rebuild_existing_archive for archives under an evidence root.")
    add_output_estimate_argument(rebuild_archives)
    rebuild_archives.add_argument("archive_dir")
    rebuild_archives.add_argument("--limit", type=int, default=20)
    rebuild_archives.add_argument("--with-visual-check", action="store_true", default=True)
    rebuild_archives.add_argument("--no-visual-check", dest="with_visual_check", action="store_false")
    rebuild_archives.add_argument("--visual-command", default="", help="Path to ebook_markdown_pipeline process-web-archive command.")
    rebuild_archives.add_argument("--visual-timeout", type=int, default=180)
    rebuild_archives.add_argument("--stop-on-error", action="store_true")
    rebuild_archives.add_argument("--format", choices=["json", "summary"], default="summary")

    browser_profile = sub.add_parser("browser-profile", help="Prepare or inspect the tool-owned browser login profile.")
    browser_profile_sub = browser_profile.add_subparsers(dest="browser_profile_command", required=True)
    browser_profile_login = browser_profile_sub.add_parser("login", help="Open a persistent browser profile so you can log in once and reuse it later.")
    browser_profile_login.add_argument("url", nargs="?", default="https://my.feishu.cn", help="URL to open for login.")
    browser_profile_login.add_argument("--user-data-dir", default="", help="Profile directory. Defaults to ~/.web-content-fetcher/browser-profile.")
    browser_profile_login.add_argument("--profile", default="", help="Chrome profile directory name when using a Chrome User Data dir.")
    browser_profile_login.add_argument("--browser-executable", default="", help="Browser executable path.")
    browser_profile_login.add_argument("--timeout", type=int, default=600, help="Seconds to wait before closing automatically.")
    browser_profile_login.add_argument("--format", choices=["json", "summary"], default="summary")
    browser_profile_login_all = browser_profile_sub.add_parser("login-all", help="Open common document/social sites in one persistent profile for manual login.")
    browser_profile_login_all.add_argument("--url", action="append", default=[], help="Extra URL to open for login. Can be repeated.")
    browser_profile_login_all.add_argument("--user-data-dir", default="", help="Profile directory. Defaults to ~/.web-content-fetcher/browser-profile.")
    browser_profile_login_all.add_argument("--profile", default="", help="Chrome profile directory name when using a Chrome User Data dir.")
    browser_profile_login_all.add_argument("--browser-executable", default="", help="Browser executable path.")
    browser_profile_login_all.add_argument("--timeout", type=int, default=900, help="Seconds to wait before closing automatically.")
    browser_profile_login_all.add_argument("--format", choices=["json", "summary"], default="summary")
    browser_profile_show = browser_profile_sub.add_parser("show", help="Show the default tool-owned browser profile path.")
    browser_profile_show.add_argument("--format", choices=["json", "summary"], default="summary")
    browser_profile_check = browser_profile_sub.add_parser("check", help="Check whether the persistent browser profile exists and has a Cookies database.")
    browser_profile_check.add_argument("--user-data-dir", default="", help="Profile directory. Defaults to ~/.web-content-fetcher/browser-profile.")
    browser_profile_check.add_argument("--profile", default="", help="Chrome profile directory name when using a Chrome User Data dir.")
    browser_profile_check.add_argument("--format", choices=["json", "summary"], default="summary")

    doctor = sub.add_parser("doctor", help="Readiness checks for optional external bridges.")
    doctor_sub = doctor.add_subparsers(dest="doctor_command", required=True)
    doctor_bridges = doctor_sub.add_parser("bridges", help="Check Crawl4AI, MarkItDown, ArchiveBox, and ebook_markdown_pipeline availability.")
    doctor_bridges.add_argument("--archivebox-executable", default="archivebox")
    doctor_bridges.add_argument("--visual-command", default="")
    doctor_bridges.add_argument("--format", choices=["json", "summary"], default="summary")

    quality = sub.add_parser("quality", help="Run and inspect opt-in real-link quality gates.")
    quality_sub = quality.add_subparsers(dest="quality_command", required=True)
    quality_smoke = quality_sub.add_parser("smoke", help="Run the real-link corpus and write quality gate reports.")
    add_output_estimate_argument(quality_smoke)
    quality_smoke.add_argument("--corpus", default="")
    quality_smoke.add_argument("--output-root", default="")
    quality_smoke.add_argument("--timeout", type=int, default=90)
    quality_smoke.add_argument("--backend", default="auto", choices=BACKEND_CHOICES)
    quality_smoke.add_argument("--extractor", default="auto", choices=sorted(EXTRACTOR_CHOICES))
    quality_smoke.add_argument("--limit", type=int, default=0)
    quality_smoke.add_argument("--compare-feishu-export", action="store_true")
    quality_smoke.add_argument("--headless", action="store_true")
    quality_smoke.add_argument("--baseline", default="")
    quality_smoke.add_argument("--recovery-pack", action="store_true", help="Also write manual_recovery.md/json and rerun-urls.txt for failed items.")
    quality_smoke.add_argument("--format", choices=["json", "summary"], default="summary")
    quality_report = quality_sub.add_parser("report", help="Inspect a quality run directory or smoke_summary.json.")
    quality_report.add_argument("run_path")
    quality_report.add_argument("--format", choices=["json", "summary"], default="summary")
    quality_compare = quality_sub.add_parser("compare", help="Compare two quality smoke summaries and report regressions.")
    quality_compare.add_argument("current")
    quality_compare.add_argument("baseline")
    quality_compare.add_argument("--format", choices=["json", "summary"], default="summary")
    quality_recovery = quality_sub.add_parser("recovery-pack", help="Write manual_recovery.md/json and rerun-urls.txt for a quality run.")
    quality_recovery.add_argument("run_path")
    quality_recovery.add_argument("--output-dir", default="")
    quality_recovery.add_argument("--format", choices=["json", "summary"], default="summary")

    fetch_batch_parser = sub.add_parser("fetch-batch", help="Fetch multiple URLs and output a stable summary for review/agents.")
    fetch_batch_parser.add_argument("--url", action="append", default=[], help="URL to fetch. Can be repeated.")
    fetch_batch_parser.add_argument("--urls-file", default="", help="Text file with one URL per line. # comments are ignored.")
    fetch_batch_parser.add_argument("--backend", default="auto", choices=BACKEND_CHOICES)
    fetch_batch_parser.add_argument("--extractor", default="auto", choices=sorted(EXTRACTOR_CHOICES))
    fetch_batch_parser.add_argument("--timeout", type=int, default=30)
    fetch_batch_parser.add_argument("--resume-from", default="", help="Existing batch directory or checkpoint.json; request options must match.")
    fetch_batch_parser.add_argument("--max-attempts", type=int, default=100, help="Cumulative attempt budget across resumes.")
    fetch_batch_parser.add_argument("--max-batch-seconds", type=float, default=600, help="Cumulative cooperative time budget; prevents admission of another item.")
    fetch_batch_parser.add_argument("--same-error-limit", type=int, default=3, help="Stop after this many consecutive failures with one cause.")
    fetch_batch_parser.add_argument("--profile", default="", help="Chrome profile directory name, e.g. Default or Profile 1.")
    fetch_batch_parser.add_argument("--chrome-user-data-dir", default="", help="Chrome User Data dir to reuse logged-in state.")
    fetch_batch_parser.add_argument("--browser-executable", default="", help="Browser executable path.")
    fetch_batch_parser.add_argument("--storage-state", default="", help="Playwright storageState JSON file.")
    fetch_batch_parser.add_argument("--headless", action="store_true")
    fetch_batch_parser.add_argument("--dismiss-obstructive-overlays", dest="dismiss_obstructive_overlays", action="store_true", default=True, help="Close obvious ad/promo overlays before extraction; default on.")
    fetch_batch_parser.add_argument("--no-dismiss-obstructive-overlays", dest="dismiss_obstructive_overlays", action="store_false", help="Do not close obstructive overlays before extraction.")
    fetch_batch_parser.add_argument("--archive-dir", default="")
    fetch_batch_parser.add_argument("--compare-feishu-export", action="store_true", help="For Feishu/Lark docs, also try official DOCX export and compare captures.")
    fetch_batch_parser.add_argument("--plain-url", action="store_true", help="Use plain fetch_content instead of document routing.")
    fetch_batch_parser.add_argument("--no-robots-check", action="store_true")
    fetch_batch_parser.add_argument("--include-results", action="store_true", help="Include each full FetchResult without raw snapshots.")

    wechat_search = sub.add_parser("wechat-search", help="Discover publicly indexed WeChat articles, then fetch and archive them with WCF.")
    wechat_search.add_argument("account_name", help="Public account name to search.")
    wechat_search.add_argument("--archive-dir", required=True, help="Evidence root for discovery and article archives.")
    wechat_search.add_argument("--provider-path", default="", help="get-WeChat-OA repo directory or collect_by_name.py path; defaults to WEB_CONTENT_FETCHER_WECHAT_DISCOVERY_PATH.")
    wechat_search.add_argument("--discovery-provider", default="auto", choices=["auto", "wechat_download_api", "get_wechat_oa"])
    wechat_search.add_argument("--article-query", default="", help="Optional keyword search within each matched public account.")
    wechat_search.add_argument("--wechat-api-url", default=os.environ.get("WEB_CONTENT_FETCHER_WECHAT_API_URL", wechat_download_api_base_url(config)))
    wechat_search.add_argument("--account-limit", type=int, default=5)
    wechat_search.add_argument("--begin", type=int, default=0)
    wechat_search.add_argument("--engine", default="sogou", choices=["sogou", "bing", "duckduckgo", "all"])
    wechat_search.add_argument("--pages", type=int, default=2)
    wechat_search.add_argument("--limit", type=int, default=20)
    wechat_search.add_argument("--delay", type=float, default=1.0)
    wechat_search.add_argument("--discovery-timeout", type=int, default=300)
    wechat_search.add_argument("--discover-only", action="store_true", help="Archive candidate URLs without fetching article bodies.")
    wechat_search.add_argument("--backend", default="auto", choices=BACKEND_CHOICES)
    wechat_search.add_argument("--extractor", default="auto", choices=sorted(EXTRACTOR_CHOICES))
    wechat_search.add_argument("--timeout", type=int, default=30)
    wechat_search.add_argument("--profile", default="")
    wechat_search.add_argument("--chrome-user-data-dir", default="")
    wechat_search.add_argument("--browser-executable", default="")
    wechat_search.add_argument("--storage-state", default="")
    wechat_search.add_argument("--headless", action="store_true")
    wechat_search.add_argument("--no-robots-check", action="store_true")

    batch = sub.add_parser("batch", help="Review and rerun saved batch reports.")
    batch_sub = batch.add_subparsers(dest="batch_command", required=True)
    batch_review = batch_sub.add_parser("review", help="Read or rebuild batch_review.json for a saved batch report.")
    batch_review.add_argument("batch_path", help="Batch report directory, summary.json, or batch_review.json path.")
    batch_review.add_argument("--format", choices=["json", "summary"], default="summary")
    batch_rerun = batch_sub.add_parser("rerun-incomplete", help="Rerun failed or low-quality URLs from a saved batch report.")
    batch_rerun.add_argument("batch_path", nargs="?", default="", help="Batch report directory or summary.json path.")
    batch_rerun.add_argument("--rerun-urls", default="", help="Explicit rerun-urls.txt path.")
    batch_rerun.add_argument("--archive-dir", default="", help="Override target evidence root for the new run.")
    batch_rerun.add_argument("--strategy", default="", choices=["", "browser", "crawl4ai", "feishu_export", "manual"], help="Convenience backend strategy for the rerun.")
    batch_rerun.add_argument("--backend", default="auto", choices=BACKEND_CHOICES)
    batch_rerun.add_argument("--extractor", default="auto", choices=sorted(EXTRACTOR_CHOICES))
    batch_rerun.add_argument("--timeout", type=int, default=30)
    batch_rerun.add_argument("--profile", default="", help="Chrome profile directory name, e.g. Default or Profile 1.")
    batch_rerun.add_argument("--chrome-user-data-dir", default="", help="Chrome User Data dir to reuse logged-in state.")
    batch_rerun.add_argument("--browser-executable", default="", help="Browser executable path.")
    batch_rerun.add_argument("--storage-state", default="", help="Playwright storageState JSON file.")
    batch_rerun.add_argument("--headless", action="store_true")
    batch_rerun.add_argument("--dismiss-obstructive-overlays", dest="dismiss_obstructive_overlays", action="store_true", default=True, help="Close obvious ad/promo overlays before extraction; default on.")
    batch_rerun.add_argument("--no-dismiss-obstructive-overlays", dest="dismiss_obstructive_overlays", action="store_false", help="Do not close obstructive overlays before extraction.")
    batch_rerun.add_argument("--compare-feishu-export", action="store_true", help="For Feishu/Lark docs, also try official DOCX export and compare captures.")
    batch_rerun.add_argument("--plain-url", action="store_true", help="Use plain fetch_content instead of document routing.")
    batch_rerun.add_argument("--no-robots-check", action="store_true")
    batch_rerun.add_argument("--include-results", action="store_true")
    batch_rerun.add_argument("--format", choices=["json", "summary"], default="json")

    fetch_zsxq = sub.add_parser("fetch-zsxq", help="Search a 知识星球 group and archive the most relevant topic.")
    fetch_zsxq.add_argument("--group-id", required=True)
    fetch_zsxq.add_argument("--query", required=True)
    fetch_zsxq.add_argument("--limit", type=int, default=20)
    fetch_zsxq.add_argument("--timeout", type=int, default=30)
    fetch_zsxq.add_argument("--archive-dir", default="")
    fetch_zsxq.add_argument("--cli-path", default="zsxq-cli")
    fetch_zsxq.add_argument("--include-raw", action="store_true")

    archive = sub.add_parser("archive", help="Inspect Web Content Fetcher evidence archives.")
    archive_sub = archive.add_subparsers(dest="archive_command", required=True)
    archive_list = archive_sub.add_parser("list", help="List archives in an evidence root directory.")
    archive_list.add_argument("archive_dir")
    archive_list.add_argument("--limit", type=int, default=50)
    archive_list.add_argument("--format", choices=["json", "summary"], default="summary")
    archive_inspect = archive_sub.add_parser("inspect", help="Read structured.json or metadata.json from an archive.")
    archive_inspect.add_argument("archive_path")
    archive_inspect.add_argument("--format", choices=["json", "summary"], default="json")
    archive_pack = archive_sub.add_parser("pack", help="Zip one evidence archive directory.")
    archive_pack.add_argument("archive_path")
    archive_pack.add_argument("--output", default="")
    archive_compare_feishu = archive_sub.add_parser("compare-feishu-export", help="Add official Feishu/Lark DOCX export comparison to an existing archive.")
    archive_compare_feishu.add_argument("archive_path")
    archive_compare_feishu.add_argument("--timeout", type=int, default=60)
    archive_compare_feishu.add_argument("--format", choices=["json", "summary"], default="summary")
    archive_rebuild = archive_sub.add_parser("rebuild", help="Build final human/agent artifacts from a fetched evidence archive.")
    archive_rebuild.add_argument("archive_path")
    archive_rebuild.add_argument("--with-visual-check", action="store_true", help="Write visual_check_request.json for ebook_markdown_pipeline.")
    archive_rebuild.add_argument("--visual-dir", default="", help="Directory containing layout_ocr.md and visual JSON outputs.")
    archive_rebuild.add_argument("--format", choices=["json", "summary"], default="json")
    rebuild_archive = sub.add_parser("rebuild-archive", help="Alias for `archive rebuild`: build final human/agent artifacts.")
    rebuild_archive.add_argument("archive_path")
    rebuild_archive.add_argument("--with-visual-check", action="store_true", help="Write visual_check_request.json for ebook_markdown_pipeline.")
    rebuild_archive.add_argument("--visual-dir", default="", help="Directory containing layout_ocr.md and visual JSON outputs.")
    rebuild_archive.add_argument("--format", choices=["json", "summary"], default="json")
    archive_batch_reports = archive_sub.add_parser("batch-reports", help="List resumable batch reports in an evidence root directory.")
    archive_batch_reports.add_argument("archive_dir")
    archive_batch_reports.add_argument("--limit", type=int, default=20)
    archive_batch_reports.add_argument("--format", choices=["json", "summary"], default="summary")
    archive_rerun_batch = archive_sub.add_parser("rerun-batch", help="Rerun URLs from a previous batch report.")
    add_output_estimate_argument(archive_rerun_batch)
    archive_rerun_batch.add_argument("--batch-path", default="", help="Batch report directory or summary.json path.")
    archive_rerun_batch.add_argument("--rerun-urls", default="", help="Explicit rerun-urls.txt path.")
    archive_rerun_batch.add_argument("--archive-dir", default="", help="Override target evidence root for the new run.")
    archive_rerun_batch.add_argument("--backend", default="auto", choices=BACKEND_CHOICES)
    archive_rerun_batch.add_argument("--extractor", default="auto", choices=sorted(EXTRACTOR_CHOICES))
    archive_rerun_batch.add_argument("--timeout", type=int, default=30)
    archive_rerun_batch.add_argument("--profile", default="", help="Chrome profile directory name, e.g. Default or Profile 1.")
    archive_rerun_batch.add_argument("--chrome-user-data-dir", default="", help="Chrome User Data dir to reuse logged-in state.")
    archive_rerun_batch.add_argument("--browser-executable", default="", help="Browser executable path.")
    archive_rerun_batch.add_argument("--storage-state", default="", help="Playwright storageState JSON file.")
    archive_rerun_batch.add_argument("--headless", action="store_true")
    archive_rerun_batch.add_argument("--dismiss-obstructive-overlays", dest="dismiss_obstructive_overlays", action="store_true", default=True, help="Close obvious ad/promo overlays before extraction; default on.")
    archive_rerun_batch.add_argument("--no-dismiss-obstructive-overlays", dest="dismiss_obstructive_overlays", action="store_false", help="Do not close obstructive overlays before extraction.")
    archive_rerun_batch.add_argument("--compare-feishu-export", action="store_true", help="For Feishu/Lark docs, also try official DOCX export and compare captures.")
    archive_rerun_batch.add_argument("--plain-url", action="store_true", help="Use plain fetch_content instead of document routing.")
    archive_rerun_batch.add_argument("--no-robots-check", action="store_true")
    archive_rerun_batch.add_argument("--include-results", action="store_true")

    lifecycle = archive_sub.add_parser(
        "lifecycle",
        help="Create and inspect plan-only evidence lifecycle manifests through artifact-lifecycle-core.",
    )
    lifecycle_sub = lifecycle.add_subparsers(dest="lifecycle_command", required=True)
    lifecycle_doctor = lifecycle_sub.add_parser("doctor", help="Check the external research_evidence profile without scanning artifacts.")
    lifecycle_doctor.add_argument("--core-root", default="", help="artifact-lifecycle-core root, or set WEB_CONTENT_FETCHER_ARTIFACT_LIFECYCLE_CORE.")
    lifecycle_doctor.add_argument("--target-root", default="", help="Optional explicit artifact root for the disk gate.")
    lifecycle_doctor.add_argument("--format", choices=["json", "summary"], default="json")
    lifecycle_scan = lifecycle_sub.add_parser("scan", help="Project one explicit WCF evidence root into a bounded plan-only manifest.")
    lifecycle_scan.add_argument("artifact_root")
    lifecycle_scan.add_argument("--output-dir", required=True, help="Manifest/checkpoint directory outside artifact_root.")
    lifecycle_scan.add_argument("--closure-run-id", required=True)
    lifecycle_scan.add_argument("--owner-lane", default="web-content-fetcher")
    lifecycle_scan.add_argument("--core-root", default="")
    lifecycle_scan.add_argument("--max-items", type=int, default=200)
    lifecycle_scan.add_argument("--max-depth", type=int, default=6)
    lifecycle_scan.add_argument("--format", choices=["json", "summary"], default="json")
    lifecycle_explain = lifecycle_sub.add_parser("explain", help="Explain why one manifest artifact is protected or a candidate.")
    lifecycle_explain.add_argument("--manifest", required=True)
    lifecycle_explain.add_argument("--artifact-id", required=True)
    lifecycle_explain.add_argument("--core-root", default="")
    lifecycle_explain.add_argument("--format", choices=["json", "summary"], default="json")
    lifecycle_plan = lifecycle_sub.add_parser("plan-gc", help="Write a non-authorizing cleanup proposal; no WCF mutation is exposed.")
    lifecycle_plan.add_argument("--manifest", required=True)
    lifecycle_plan.add_argument("--artifact-root", required=True)
    lifecycle_plan.add_argument("--quarantine-root", required=True)
    lifecycle_plan.add_argument("--output", required=True)
    lifecycle_plan.add_argument("--owner-state", default="unknown")
    lifecycle_plan.add_argument("--process-state", default="unknown")
    lifecycle_plan.add_argument("--dispatch-state", default="unknown")
    lifecycle_plan.add_argument("--core-root", default="")
    lifecycle_plan.add_argument("--format", choices=["json", "summary"], default="json")
    lifecycle_verify = lifecycle_sub.add_parser("verify", help="Verify an immutable plan against its current bounded artifact root.")
    lifecycle_verify.add_argument("--plan", required=True)
    lifecycle_verify.add_argument("--manifest", required=True)
    lifecycle_verify.add_argument("--artifact-root", required=True)
    lifecycle_verify.add_argument("--quarantine-root", required=True)
    lifecycle_verify.add_argument("--core-root", default="")
    lifecycle_verify.add_argument("--format", choices=["json", "summary"], default="json")
    lifecycle_restore = lifecycle_sub.add_parser("restore-synthetic", help="Restore only a marked synthetic fixture root; never available for production archives.")
    lifecycle_restore.add_argument("--plan", required=True)
    lifecycle_restore.add_argument("--receipt", required=True)
    lifecycle_restore.add_argument("--artifact-root", required=True)
    lifecycle_restore.add_argument("--quarantine-root", required=True)
    lifecycle_restore.add_argument("--output", required=True)
    lifecycle_restore.add_argument("--core-root", default="")
    lifecycle_restore.add_argument("--execute", action="store_true")
    lifecycle_restore.add_argument("--format", choices=["json", "summary"], default="json")

    paper = sub.add_parser("discover-paper", help="Discover an OA candidate for an exact DOI; network lookup, no PDF download.")
    paper.add_argument("doi")
    paper.add_argument("--script", default="", help="Reviewed paper-fetch-oa script, or WCF_PAPER_OA_SCRIPT.")
    paper.add_argument("--timeout", type=int, default=120)

    convert_attachment = sub.add_parser("convert-attachment", help="Convert a PDF/DOCX/PPTX/etc. attachment to Markdown with MarkItDown.")
    convert_attachment.add_argument("input_path")
    convert_attachment.add_argument("--output", default="")

    archivebox = sub.add_parser("archivebox", help="Submit URLs to an external ArchiveBox CLI and optionally index the result.")
    archivebox_sub = archivebox.add_subparsers(dest="archivebox_command", required=True)
    archivebox_add = archivebox_sub.add_parser("add", help="Run `archivebox add` for one or more URLs.")
    add_output_estimate_argument(archivebox_add)
    archivebox_add.add_argument("--url", action="append", default=[], help="URL to add. Can be repeated.")
    archivebox_add.add_argument("--urls-file", default="", help="Text file with one URL per line. # comments are ignored.")
    archivebox_add.add_argument("--archivebox-dir", default="", help="ArchiveBox collection directory to use as cwd.")
    archivebox_add.add_argument("--archive-dir", default="", help="Web Content Fetcher evidence root for indexing command output.")
    archivebox_add.add_argument("--executable", default="archivebox")
    archivebox_add.add_argument("--timeout", type=int, default=120)
    archivebox_add.add_argument("--extra-arg", action="append", default=[], help="Extra argument passed after `archivebox add`; repeat as needed.")

    readeck = sub.add_parser("readeck", help="Submit a URL or captured HTML to a self-hosted Readeck instance.")
    readeck_sub = readeck.add_subparsers(dest="readeck_command", required=True)
    readeck_add = readeck_sub.add_parser("add", help="Create a Readeck bookmark and optionally archive the receipt.")
    readeck_add.add_argument("url")
    readeck_add.add_argument("--html-file", default="", help="Optional captured HTML file. Readeck receives this HTML instead of fetching the URL itself.")
    readeck_add.add_argument("--title", default="")
    readeck_add.add_argument("--label", action="append", default=[])
    readeck_add.add_argument("--base-url", default="", help="Defaults to READECK_BASE_URL.")
    readeck_add.add_argument("--token-env", default="READECK_TOKEN", help="Environment variable name only; its value is never printed.")
    readeck_add.add_argument("--archive-dir", default="")
    readeck_add.add_argument("--timeout", type=int, default=30)
    readeck_add.add_argument("--wait", type=int, default=0, help="Optional seconds before requesting Readeck Markdown/HTML exports.")
    browsertrix = sub.add_parser("browsertrix", help="Create a high-fidelity WACZ archive through local Docker Browsertrix Crawler.")
    browsertrix_sub = browsertrix.add_subparsers(dest="browsertrix_command", required=True)
    browsertrix_archive = browsertrix_sub.add_parser("archive", help="Capture one or more URLs as WACZ, pages.jsonl, text, and screenshots.")
    add_output_estimate_argument(browsertrix_archive)
    browsertrix_archive.add_argument("--url", action="append", default=[])
    browsertrix_archive.add_argument("--output-dir", required=True)
    browsertrix_archive.add_argument("--collection", default="wcf-capture")
    browsertrix_archive.add_argument("--archive-dir", default="")
    browsertrix_archive.add_argument("--docker-executable", default="docker")
    browsertrix_archive.add_argument("--image", default="webrecorder/browsertrix-crawler")
    browsertrix_archive.add_argument("--timeout", type=int, default=900)
    browsertrix_archive.add_argument("--profile-path", default="", help="Optional Browsertrix profile tar.gz created through its authorized login flow.")
    browsertrix_archive.add_argument("--extra-arg", action="append", default=[])
    browsertrix_archive.add_argument("--reference-artifacts", action="store_true", help="Reference WACZ/pages from output-dir instead of copying them into the WCF evidence archive.")
    zsxq = sub.add_parser("zsxq", help="Call official zsxq-cli and output structured JSON.")
    zsxq_sub = zsxq.add_subparsers(dest="zsxq_command", required=True)
    def add_zsxq_common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--timeout", type=int, default=30)
        command.add_argument("--archive-dir", default="")
        command.add_argument("--cli-path", default="zsxq-cli")
        command.add_argument("--include-raw", action="store_true")

    auth_status = zsxq_sub.add_parser("auth-status", help="Show zsxq-cli login status.")
    add_zsxq_common(auth_status)
    list_groups = zsxq_sub.add_parser("list-groups", help="List joined 知识星球 groups.")
    list_groups.add_argument("--limit", type=int, default=20)
    add_zsxq_common(list_groups)
    list_topics = zsxq_sub.add_parser("list-topics", help="List recent topics in one group.")
    list_topics.add_argument("--group-id", required=True)
    list_topics.add_argument("--limit", type=int, default=20)
    list_topics.add_argument("--end-time", default="")
    add_zsxq_common(list_topics)
    search_topics = zsxq_sub.add_parser("search-topics", help="Search topics in one group.")
    search_topics.add_argument("--group-id", required=True)
    search_topics.add_argument("--query", required=True)
    add_zsxq_common(search_topics)
    fetch_topic = zsxq_sub.add_parser("fetch-topic", help="Search topics and fetch the most relevant topic detail.")
    fetch_topic.add_argument("--group-id", required=True)
    fetch_topic.add_argument("--query", required=True)
    fetch_topic.add_argument("--limit", type=int, default=20)
    add_zsxq_common(fetch_topic)
    get_topic = zsxq_sub.add_parser("get-topic", help="Get full topic details.")
    get_topic.add_argument("--topic-id", required=True)
    add_zsxq_common(get_topic)
    export_group = zsxq_sub.add_parser("export-group", help="Page through a 知识星球 group and write a local archive/index.")
    add_output_estimate_argument(export_group)
    export_group.add_argument("--group-id", required=True)
    export_group.add_argument("--limit", type=int, default=30)
    export_group.add_argument("--end-time", default="")
    export_group.add_argument("--max-pages", type=int, default=0, help="Stop after this many pages. 0 means until the official API has no more pages.")
    export_group.add_argument("--with-detail", action="store_true", help="Fetch topic detail for each listed topic. Slower and uses one extra API call per topic.")
    export_group.add_argument("--with-comments", action="store_true", help="Fetch comments for each topic through zsxq-cli api call get_topic_comments.")
    add_zsxq_common(export_group)
    classify_export = zsxq_sub.add_parser("classify-export", help="Write generic sidecar classification indexes for one or more zsxq export directories.")
    classify_export.add_argument("export_path", nargs="+", help="zsxq export directory or summary.json path.")
    classify_export.add_argument("--output-dir", default="", help="Where to write classification_index.json/md. Defaults to the first export directory.")
    classify_export.add_argument("--taxonomy", default="", help="Optional JSON taxonomy. The built-in taxonomy is generic and contains no business-specific rules.")
    classify_export.add_argument("--format", choices=["json", "summary"], default="summary")
    merge_exports = zsxq_sub.add_parser("merge-exports", help="Merge segmented zsxq export directories into one deduplicated final archive.")
    merge_exports.add_argument("export_path", nargs="+", help="zsxq export directory or summary.json path.")
    merge_exports.add_argument("--output-dir", default="", help="Final merged directory. Defaults to zsxq_group_<id>_full_complete_<timestamp> beside the first export.")
    merge_exports.add_argument("--format", choices=["json", "summary"], default="summary")
    sub.add_parser("mcp", help="Run the thin stdio MCP server.")
    return parser


def add_wechat_api_arguments(parser: argparse.ArgumentParser, default_base_url: str) -> None:
    parser.add_argument(
        "--wechat-api-url",
        default=os.environ.get("WEB_CONTENT_FETCHER_WECHAT_API_URL", default_base_url),
        help="Local wechat-download-api base URL used by backend=wechat_download_api.",
    )


def add_firecrawl_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--firecrawl-api-url", default="", help="Firecrawl scrape API URL. Defaults to FIRECRAWL_API_URL or https://api.firecrawl.dev/v1/scrape.")
    parser.add_argument("--firecrawl-api-key-env", default="FIRECRAWL_API_KEY", help="Environment variable that contains the Firecrawl API key; value is never printed or archived.")
    parser.add_argument("--firecrawl-format", action="append", default=[], help="Firecrawl format to request. Repeat for markdown/html/links/screenshot. Defaults to markdown, html, links.")
    parser.add_argument("--firecrawl-include-full-page", action="store_true", help="Set onlyMainContent=false for Firecrawl. Use only for pages you are allowed to send to the configured API.")
    parser.add_argument("--firecrawl-wait-for-ms", type=int, default=0, help="Firecrawl waitFor in milliseconds when supported by the configured API.")


def add_scrapling_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scrapling-mode", default="static", choices=["static", "dynamic", "stealthy"], help="Scrapling fetcher mode. Explicit backend only; not used by auto.")
    parser.add_argument("--scrapling-headful", action="store_true", help="Run Scrapling browser modes visibly instead of headless.")
    parser.add_argument("--scrapling-network-idle", action="store_true", help="Ask Scrapling browser modes to wait for network idle.")
    parser.add_argument("--scrapling-wait-ms", type=int, default=0, help="Extra Scrapling wait time in milliseconds after load.")
    parser.add_argument("--scrapling-wait-selector", default="", help="CSS selector for Scrapling browser modes to wait for.")
    parser.add_argument("--scrapling-real-chrome", action="store_true", help="Ask Scrapling browser modes to launch real Chrome when supported.")
    parser.add_argument("--scrapling-user-data-dir", default="", help="User data dir for Scrapling stealthy mode when explicitly authorized.")
    parser.add_argument("--scrapling-python-path", default=os.environ.get("WEB_CONTENT_FETCHER_SCRAPLING_PATH", ""), help="Optional local Scrapling checkout path to prepend via PYTHONPATH.")
    parser.add_argument("--scrapling-python-executable", default=os.environ.get("WEB_CONTENT_FETCHER_SCRAPLING_PYTHON", ""), help="Optional Python executable from the local Scrapling environment.")


def add_crawl4ai_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--crawl4ai-wait-for", default="", help="Crawl4AI wait_for selector/condition.")
    parser.add_argument("--crawl4ai-css-selector", default="", help="Crawl4AI CSS selector for focused extraction.")
    parser.add_argument("--crawl4ai-screenshot", action="store_true", help="Ask Crawl4AI to capture a screenshot.")
    parser.add_argument("--crawl4ai-pdf", action="store_true", help="Ask Crawl4AI to capture a PDF.")
    parser.add_argument("--crawl4ai-cache-mode", default="bypass", choices=["bypass", "enabled"], help="Crawl4AI cache mode.")


def markdown(result) -> str:
    lines = [
        f"# {result.title or result.final_url}",
        "",
        f"- URL: {result.final_url}",
        f"- Status: {result.status}",
        f"- Transport: {result.transport}",
        f"- Fetched at: {result.fetched_at}",
        "",
        "## Extracted Text",
        "",
        result.extracted_text or "",
    ]
    if result.selector_results:
        lines.extend(["", "## Selector Results", ""])
        for item in result.selector_results:
            lines.append(f"- {item.get('name')}: {item.get('text')}")
    if result.images:
        lines.extend(["", "## Images", ""])
        for image in result.images[:20]:
            lines.append(f"- {image.get('url')}")
    if result.page_html_path or result.screenshot_path or result.singlefile_path:
        lines.extend(["", "## Browser Artifacts", ""])
        if result.page_html_path:
            lines.append(f"- HTML: {result.page_html_path}")
        if result.screenshot_path:
            lines.append(f"- Screenshot: {result.screenshot_path}")
        if result.singlefile_path:
            lines.append(f"- SingleFile: {result.singlefile_path}")
        if result.browser_visible_path:
            lines.append(f"- Browser visible markdown: {result.browser_visible_path}")
        if result.browser_visible_html_path:
            lines.append(f"- Browser visible HTML: {result.browser_visible_html_path}")
    if result.archive:
        lines.extend(["", "## Evidence Archive", "", f"- Archive: {result.archive.get('archive_path')}", f"- Hash: {result.archive.get('archive_hash')}"])
        if result.archive.get("readable_html_path"):
            lines.append(f"- Readable HTML: {result.archive.get('readable_html_path')}")
        if result.archive.get("preferred_markdown_path"):
            lines.append(f"- Preferred markdown: {result.archive.get('preferred_markdown_path')}")
        if result.archive.get("browser_visible_path"):
            lines.append(f"- Browser visible markdown: {result.archive.get('browser_visible_path')}")
        if result.archive.get("browser_visible_html_path"):
            lines.append(f"- Browser visible HTML: {result.archive.get('browser_visible_html_path')}")
    if result.errors:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {error}" for error in result.errors)
    return "\n".join(lines).rstrip() + "\n"


def _run_archive_rebuild(args: argparse.Namespace) -> int:
    from .archive_rebuilder import RebuildArchiveRequest, rebuild_archive

    data = rebuild_archive(
        RebuildArchiveRequest(
            archive_path=args.archive_path,
            with_visual_check=args.with_visual_check,
            visual_dir=args.visual_dir,
        )
    )
    if args.format == "summary":
        sys.stdout.write("Archive rebuild complete.\n")
        sys.stdout.write(f"Final readable: {data.get('final_readable_html_path') or ''}\n")
        sys.stdout.write(f"Final agent markdown: {data.get('final_agent_markdown_path') or ''}\n")
        sys.stdout.write(f"Final structured: {data.get('final_structured_json_path') or ''}\n")
        sys.stdout.write(f"Completeness report: {data.get('completeness_report_path') or ''}\n")
        sys.stdout.write(f"Quality: {(data.get('quality') or {}).get('status') or ''}\n")
        recommendation = (data.get("quality") or {}).get("recommendation") or ""
        if recommendation:
            sys.stdout.write(f"Recommendation: {recommendation}\n")
    else:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return 0


def _print_pipeline_rebuild_summary(data: dict) -> None:
    sys.stdout.write(f"Status: {data.get('status') or ''}\n")
    sys.stdout.write(f"Archive: {data.get('archive_path') or ''}\n")
    fetch_info = data.get("fetch") or {}
    if fetch_info:
        sys.stdout.write(f"Fetch: {fetch_info.get('status') or ''} | {fetch_info.get('transport') or ''} | {fetch_info.get('extraction_engine') or ''}\n")
    visual = data.get("visual_check") or {}
    sys.stdout.write(f"Visual check: {visual.get('status') or ''}\n")
    sys.stdout.write(f"Final readable: {data.get('final_readable_html_path') or ''}\n")
    sys.stdout.write(f"Final agent markdown: {data.get('final_agent_markdown_path') or ''}\n")
    sys.stdout.write(f"Final structured: {data.get('final_structured_json_path') or ''}\n")
    sys.stdout.write(f"Completeness report: {data.get('completeness_report_path') or ''}\n")
    quality = data.get("quality") or {}
    sys.stdout.write(f"Quality: {quality.get('status') or ''}\n")
    if quality.get("recommendation"):
        sys.stdout.write(f"Recommendation: {quality.get('recommendation')}\n")
    for error in data.get("errors") or []:
        sys.stdout.write(f"Error: {error}\n")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    if args.command == "body-store":
        from .public_bodies import main as body_main
        return body_main(args.body_arguments)
    if args.command == "discover-paper":
        from .paper_oa import discover_paper
        result = discover_paper(args.doi, script=args.script, timeout=args.timeout)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return 0 if result["status"] == "candidate_found" else 2
    try:
        _large_artifact_disk_gate(args)
    except LifecycleAdapterError as exc:
        sys.stdout.write(
            json.dumps(
                {
                    "status": "blocked",
                    "error_code": str(exc).split(":", 1)[0],
                    "detail": str(exc),
                    "filesystem_mutation_performed": False,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        return 2
    if args.command == "rebuild-archive":
        return _run_archive_rebuild(args)
    if args.command == "fetch":
        request = FetchRequest(
            url=args.url,
            fetch_profile=args.fetch_profile,
            backend=args.backend,
            extractor=args.extractor,
            timeout_seconds=args.timeout,
            selectors=[{"selector": selector, "name": selector} for selector in args.selector],
            use_web_access=args.use_web_access,
            web_access_base_url=args.web_access_url,
            prefer_browser=args.prefer_browser,
            use_browser=args.browser,
            browser_headless=args.headless,
            browser_profile=args.profile,
            browser_user_data_dir=args.chrome_user_data_dir,
            browser_executable_path=args.browser_executable,
            browser_storage_state=args.storage_state,
            browser_wait_until=args.wait_until,
            browser_auto_scroll=args.auto_scroll,
            browser_scroll_steps=args.scroll_steps,
            browser_dismiss_obstructive_overlays=args.dismiss_obstructive_overlays,
            save_screenshot=args.screenshot,
            download_images=args.download_images,
            save_singlefile=args.singlefile,
            snapshot_backend="" if args.snapshot_backend == "none" else args.snapshot_backend,
            monolith_executable=args.monolith_executable,
            monolith_mhtml=args.monolith_mhtml,
            auth_profile=args.auth_profile,
            proxy_session=args.proxy_session,
            archive_dir=args.archive_dir,
            cache_dir=args.cache_dir,
            cache_max_age_seconds=args.cache_max_age,
            check_robots=not args.no_robots_check,
            crawl4ai_wait_for=args.crawl4ai_wait_for,
            crawl4ai_css_selector=args.crawl4ai_css_selector,
            crawl4ai_screenshot=args.crawl4ai_screenshot,
            crawl4ai_pdf=args.crawl4ai_pdf,
            crawl4ai_cache_mode=args.crawl4ai_cache_mode,
            firecrawl_api_url=args.firecrawl_api_url,
            firecrawl_api_key_env=args.firecrawl_api_key_env,
            firecrawl_formats=args.firecrawl_format or ["markdown", "html", "links"],
            firecrawl_only_main_content=not args.firecrawl_include_full_page,
            firecrawl_wait_for_ms=args.firecrawl_wait_for_ms,
            scrapling_mode=args.scrapling_mode,
            scrapling_headless=not args.scrapling_headful,
            scrapling_network_idle=args.scrapling_network_idle,
            scrapling_wait_ms=args.scrapling_wait_ms,
            scrapling_wait_selector=args.scrapling_wait_selector,
            scrapling_real_chrome=args.scrapling_real_chrome,
            scrapling_user_data_dir=args.scrapling_user_data_dir,
            scrapling_python_path=args.scrapling_python_path,
            scrapling_python_executable=args.scrapling_python_executable,
            compare_feishu_export=args.compare_feishu_export,
            discover_endpoints_only=args.discover_endpoints_only,
            wechat_api_base_url=args.wechat_api_url,
        )
        result = fetch_content(request)
        if args.format == "markdown":
            sys.stdout.write(markdown(result))
        else:
            sys.stdout.write(json.dumps(result.to_dict(include_raw=args.include_raw), ensure_ascii=False, indent=2) + "\n")
        return 0 if result.status == "ok" else 2
    if args.command == "fetch-doc":
        result = fetch_document_link(
            args.url,
            archive_dir=args.archive_dir,
            timeout_seconds=args.timeout,
            browser_profile=args.profile,
            browser_user_data_dir=args.chrome_user_data_dir,
            browser_executable_path=args.browser_executable,
            browser_storage_state=args.storage_state,
            headless=args.headless,
            check_robots=not args.no_robots_check,
            extractor=args.extractor,
            backend=args.backend,
            crawl4ai_wait_for=args.crawl4ai_wait_for,
            crawl4ai_css_selector=args.crawl4ai_css_selector,
            crawl4ai_screenshot=args.crawl4ai_screenshot,
            crawl4ai_pdf=args.crawl4ai_pdf,
            crawl4ai_cache_mode=args.crawl4ai_cache_mode,
            firecrawl_api_url=args.firecrawl_api_url,
            firecrawl_api_key_env=args.firecrawl_api_key_env,
            firecrawl_formats=args.firecrawl_format or ["markdown", "html", "links"],
            firecrawl_only_main_content=not args.firecrawl_include_full_page,
            firecrawl_wait_for_ms=args.firecrawl_wait_for_ms,
            scrapling_mode=args.scrapling_mode,
            scrapling_headless=not args.scrapling_headful,
            scrapling_network_idle=args.scrapling_network_idle,
            scrapling_wait_ms=args.scrapling_wait_ms,
            scrapling_wait_selector=args.scrapling_wait_selector,
            scrapling_real_chrome=args.scrapling_real_chrome,
            scrapling_user_data_dir=args.scrapling_user_data_dir,
            scrapling_python_path=args.scrapling_python_path,
            scrapling_python_executable=args.scrapling_python_executable,
            compare_feishu_export=args.compare_feishu_export,
            wechat_api_base_url=args.wechat_api_url,
        )
        if args.format == "markdown":
            sys.stdout.write(markdown(result))
        else:
            sys.stdout.write(json.dumps(result.to_dict(include_raw=args.include_raw), ensure_ascii=False, indent=2) + "\n")
        return 0 if result.status == "ok" else 2
    if args.command == "fetch-and-rebuild":
        from .pipeline import DEFAULT_VISUAL_COMMAND, FetchAndRebuildRequest, fetch_and_rebuild

        data = fetch_and_rebuild(
            FetchAndRebuildRequest(
                url=args.url,
                archive_dir=args.archive_dir,
                timeout_seconds=args.timeout,
                browser_profile=args.profile,
                browser_user_data_dir=args.chrome_user_data_dir,
                browser_executable_path=args.browser_executable,
                browser_storage_state=args.storage_state,
                headless=args.headless,
                check_robots=not args.no_robots_check,
                extractor=args.extractor,
                backend=args.backend,
                crawl4ai_wait_for=args.crawl4ai_wait_for,
                crawl4ai_css_selector=args.crawl4ai_css_selector,
                crawl4ai_screenshot=args.crawl4ai_screenshot,
                crawl4ai_pdf=args.crawl4ai_pdf,
                crawl4ai_cache_mode=args.crawl4ai_cache_mode,
                firecrawl_api_url=args.firecrawl_api_url,
                firecrawl_api_key_env=args.firecrawl_api_key_env,
                firecrawl_formats=args.firecrawl_format or ["markdown", "html", "links"],
                firecrawl_only_main_content=not args.firecrawl_include_full_page,
                firecrawl_wait_for_ms=args.firecrawl_wait_for_ms,
                scrapling_mode=args.scrapling_mode,
                scrapling_headless=not args.scrapling_headful,
                scrapling_network_idle=args.scrapling_network_idle,
                scrapling_wait_ms=args.scrapling_wait_ms,
                scrapling_wait_selector=args.scrapling_wait_selector,
                scrapling_real_chrome=args.scrapling_real_chrome,
                scrapling_user_data_dir=args.scrapling_user_data_dir,
                scrapling_python_path=args.scrapling_python_path,
                scrapling_python_executable=args.scrapling_python_executable,
                compare_feishu_export=args.compare_feishu_export,
                with_visual_check=args.with_visual_check,
                visual_command=args.visual_command or DEFAULT_VISUAL_COMMAND,
                visual_timeout_seconds=args.visual_timeout,
            )
        )
        if args.format == "summary":
            _print_pipeline_rebuild_summary(data)
        else:
            sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return 0 if data.get("status") == "ok" else 2
    if args.command == "rebuild-existing-archive":
        from .pipeline import DEFAULT_VISUAL_COMMAND, RebuildExistingArchiveRequest, rebuild_existing_archive

        data = rebuild_existing_archive(
            RebuildExistingArchiveRequest(
                archive_path=args.archive_path,
                with_visual_check=args.with_visual_check,
                visual_command=args.visual_command or DEFAULT_VISUAL_COMMAND,
                visual_timeout_seconds=args.visual_timeout,
            )
        )
        if args.format == "summary":
            _print_pipeline_rebuild_summary(data)
        else:
            sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return 0 if data.get("status") == "ok" else 2
    if args.command == "rebuild-archives":
        from .pipeline import DEFAULT_VISUAL_COMMAND, RebuildArchiveBatchRequest, rebuild_archive_batch

        data = rebuild_archive_batch(
            RebuildArchiveBatchRequest(
                archive_dir=args.archive_dir,
                limit=args.limit,
                with_visual_check=args.with_visual_check,
                visual_command=args.visual_command or DEFAULT_VISUAL_COMMAND,
                visual_timeout_seconds=args.visual_timeout,
                continue_on_error=not args.stop_on_error,
            )
        )
        if args.format == "summary":
            sys.stdout.write(f"Status: {data.get('status') or ''}\n")
            sys.stdout.write(f"Archive dir: {data.get('archive_dir') or ''}\n")
            sys.stdout.write(f"Total: {data.get('total') or 0} | OK: {data.get('ok') or 0} | Failed: {data.get('failed') or 0}\n")
            report = data.get("rebuild_batch_report") or {}
            if report.get("batch_path"):
                sys.stdout.write(f"Report: {report.get('batch_path')}\n")
                sys.stdout.write(f"Review: {report.get('review_markdown_path') or ''}\n")
            for item in data.get("items") or []:
                sys.stdout.write(
                    f"- #{item.get('index')} {item.get('status')} {item.get('title') or item.get('archive_path')} "
                    f"visual={item.get('visual_status') or ''} readable={item.get('final_readable_html_path') or ''}\n"
                )
                for error in item.get("errors") or []:
                    sys.stdout.write(f"  Error: {error}\n")
        else:
            sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return 0 if data.get("failed", 0) == 0 else 2
    if args.command == "browser-profile":
        if args.browser_profile_command == "show":
            data = {
                "default_user_data_dir": str(default_profile_dir()),
                "common_login_urls": COMMON_LOGIN_URLS,
                "login_all_command": "python -m web_content_fetcher.cli browser-profile login-all",
            }
        elif args.browser_profile_command == "check":
            data = browser_profile_status(args.user_data_dir, profile=args.profile)
        else:
            urls = COMMON_LOGIN_URLS + list(args.url or []) if args.browser_profile_command == "login-all" else [args.url]
            data = open_browser_profile_for_login(
                BrowserProfileLoginOptions(
                    url=urls[0] if urls else "about:blank",
                    urls=urls,
                    timeout_seconds=args.timeout,
                    user_data_dir=args.user_data_dir,
                    executable_path=args.browser_executable,
                    profile=args.profile,
                )
            )
        if args.format == "json":
            sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        else:
            if args.browser_profile_command == "show":
                sys.stdout.write(f"Default browser profile: {data['default_user_data_dir']}\n")
                sys.stdout.write(f"Login all: {data['login_all_command']}\n")
            elif args.browser_profile_command == "check":
                sys.stdout.write(f"Browser profile status: {data['status']}\n")
                sys.stdout.write(f"User data dir: {data['user_data_dir']}\n")
                sys.stdout.write(f"Profile path: {data['profile_path']}\n")
                sys.stdout.write(f"Cookies DB: {'yes' if data.get('has_cookies_db') else 'no'} ({data.get('cookies_db_count', 0)})\n")
                sys.stdout.write(f"Modified at: {data.get('modified_at', '')}\n")
                if data.get("possibly_anonymous"):
                    sys.stdout.write("Warning: profile may be anonymous; run browser-profile login-all and log in manually.\n")
            else:
                sys.stdout.write(
                    "Browser profile ready.\n"
                    f"User data dir: {data.get('user_data_dir', '')}\n"
                    "Future browser fetches, including fetch-doc, reuse this profile by default when --chrome-user-data-dir is omitted.\n"
                )
                opened = data.get("urls") or []
                if opened:
                    sys.stdout.write("Opened login URLs:\n")
                    for url in opened:
                        sys.stdout.write(f"- {url}\n")
                for error in data.get("navigation_errors") or []:
                    sys.stdout.write(f"Navigation warning: {error}\n")
        return 0
    if args.command == "doctor":
        if args.doctor_command == "bridges":
            from .bridge_doctor import bridge_doctor

            data = bridge_doctor(archivebox_executable=args.archivebox_executable, visual_command=args.visual_command)
            if args.format == "json":
                sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            else:
                sys.stdout.write(f"Bridge doctor: {data.get('status')}\n")
                for item in data.get("checks", []):
                    sys.stdout.write(
                        f"- {item.get('name')}: {item.get('status')} "
                        f"{item.get('resolved_path') or item.get('origin') or item.get('path') or ''}\n"
                    )
                    if item.get("install_hint"):
                        sys.stdout.write(f"  hint: {item.get('install_hint')}\n")
            return 0 if data.get("status") in {"ready", "partial"} else 2
    if args.command == "quality":
        from .quality import QualitySmokeRequest, compare_quality_runs, inspect_quality_run, run_quality_smoke

        if args.quality_command == "smoke":
            data = run_quality_smoke(
                QualitySmokeRequest(
                    corpus_path=args.corpus,
                    output_root=args.output_root,
                    timeout_seconds=args.timeout,
                    backend=args.backend,
                    extractor=args.extractor,
                    limit=args.limit,
                    compare_feishu_export=args.compare_feishu_export,
                    headless=args.headless,
                    baseline_path=args.baseline,
                )
            )
            if args.recovery_pack:
                from .recovery import make_manual_recovery_pack

                data["manual_recovery"] = make_manual_recovery_pack(data["output_root"])
            if args.format == "json":
                sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            else:
                report = data.get("quality_report") or {}
                recovery = data.get("manual_recovery") or {}
                recovery_paths = recovery.get("paths") or {}
                sys.stdout.write(f"Quality smoke: {data.get('status') or ''}\n")
                sys.stdout.write(f"Run: {data.get('output_root') or ''}\n")
                sys.stdout.write(f"Total: {data.get('total') or 0} | OK: {data.get('ok') or 0} | Failed: {data.get('failed') or 0} | Quality failed: {data.get('quality_failed') or 0}\n")
                sys.stdout.write(f"Summary: {report.get('smoke_summary_markdown_path') or ''}\n")
                sys.stdout.write(f"Failures: {report.get('quality_failures_json_path') or ''}\n")
                sys.stdout.write(f"Rerun plan: {report.get('rerun_plan_json_path') or ''}\n")
                if recovery_paths:
                    sys.stdout.write(f"Recovery pack: {recovery_paths.get('manual_recovery_markdown_path') or ''}\n")
            return 0 if int(data.get("failed") or 0) == 0 and int(data.get("quality_failed") or 0) == 0 else 1
        if args.quality_command == "report":
            data = inspect_quality_run(args.run_path)
            if args.format == "json":
                sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            else:
                report = data.get("quality_report") or {}
                sys.stdout.write(f"Quality run: {data.get('run_id') or ''}\n")
                sys.stdout.write(f"Total: {data.get('total') or 0} | Failed: {data.get('failed') or 0} | Quality failed: {data.get('quality_failed') or 0}\n")
                sys.stdout.write(f"Rerun plan: {report.get('rerun_plan_json_path') or ''}\n")
                for item in data.get("items", []):
                    gate = item.get("quality_gate") or {}
                    marker = "ok" if gate.get("passed") else "failed"
                    sys.stdout.write(f"- {marker} | {item.get('id') or item.get('url') or ''} | {gate.get('rerun_strategy') or ''} | {item.get('archive_path') or ''}\n")
            return 0 if int(data.get("quality_failed") or 0) == 0 else 1
        if args.quality_command == "compare":
            data = compare_quality_runs(inspect_quality_run(args.current), inspect_quality_run(args.baseline))
            if args.format == "json":
                sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            else:
                sys.stdout.write(f"Quality compare: {data.get('status') or ''}\n")
                sys.stdout.write(f"Regressions: {data.get('regression_count') or 0}\n")
                for item in data.get("regressions", []):
                    sys.stdout.write(f"- {item.get('id')}: {item.get('code')} {item.get('previous')} -> {item.get('current')}\n")
            return 0 if data.get("status") == "ok" else 1
        if args.quality_command == "recovery-pack":
            from .recovery import make_manual_recovery_pack

            data = make_manual_recovery_pack(args.run_path, output_dir=args.output_dir)
            if args.format == "json":
                sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            else:
                paths = data.get("paths") or {}
                sys.stdout.write(f"Manual recovery pack: {data.get('status') or ''}\n")
                sys.stdout.write(f"Total: {data.get('total') or 0}\n")
                sys.stdout.write(f"Markdown: {paths.get('manual_recovery_markdown_path') or ''}\n")
                sys.stdout.write(f"JSON: {paths.get('manual_recovery_json_path') or ''}\n")
                sys.stdout.write(f"Rerun URLs: {paths.get('rerun_urls_path') or ''}\n")
            return 0
    if args.command == "wechat-search":
        from .wechat_search import WechatSearchArchiveRequest, search_and_archive_wechat

        data = search_and_archive_wechat(
            WechatSearchArchiveRequest(
                account_name=args.account_name,
                archive_dir=args.archive_dir,
                provider_path=args.provider_path,
                discovery_provider=args.discovery_provider,
                article_query=args.article_query,
                wechat_api_base_url=args.wechat_api_url,
                account_limit=args.account_limit,
                begin=args.begin,
                engine=args.engine,
                pages=args.pages,
                limit=args.limit,
                delay_seconds=args.delay,
                discovery_timeout_seconds=args.discovery_timeout,
                fetch_articles=not args.discover_only,
                timeout_seconds=args.timeout,
                backend=args.backend,
                extractor=args.extractor,
                browser_profile=args.profile,
                browser_user_data_dir=args.chrome_user_data_dir,
                browser_executable_path=args.browser_executable,
                browser_storage_state=args.storage_state,
                headless=args.headless,
                check_robots=not args.no_robots_check,
            )
        )
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return 0 if data.get("status") in {"ok", "partial", "discovered", "not_found"} else 2
    if args.command == "fetch-batch":
        from .batch import BatchFetchRequest, fetch_batch, read_url_list

        urls = list(args.url or [])
        if args.urls_file:
            urls.extend(read_url_list(args.urls_file))
        if not urls:
            raise SystemExit("fetch-batch requires --url or --urls-file")
        data = fetch_batch(
            BatchFetchRequest(
                urls=urls,
                archive_dir=args.archive_dir,
                timeout_seconds=args.timeout,
                backend=args.backend,
                extractor=args.extractor,
                as_documents=not args.plain_url,
                resume_from=args.resume_from,
                max_attempts=args.max_attempts,
                max_batch_seconds=args.max_batch_seconds,
                same_error_limit=args.same_error_limit,
                browser_profile=args.profile,
                browser_user_data_dir=args.chrome_user_data_dir,
                browser_executable_path=args.browser_executable,
                browser_storage_state=args.storage_state,
                headless=args.headless,
                check_robots=not args.no_robots_check,
                compare_feishu_export=args.compare_feishu_export,
                browser_dismiss_obstructive_overlays=args.dismiss_obstructive_overlays,
                include_results=args.include_results,
            )
        )
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return 0 if data.get("status") in {"ok", "partial"} else 2
    if args.command == "batch":
        if args.batch_command == "review":
            from .batch import read_batch_review

            data = read_batch_review(args.batch_path)
            if args.format == "json":
                sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            else:
                sys.stdout.write(
                    f"Batch review: {data.get('status') or ''}\n"
                    f"Total: {int(data.get('total') or 0)} | OK: {int(data.get('ok') or 0)} | "
                    f"Failed: {int(data.get('failed') or 0)} | Low quality: {int(data.get('low_quality') or 0)}\n"
                )
                for item in data.get("items", []):
                    marker = "rerun" if item.get("needs_rerun") else "ok"
                    sys.stdout.write(
                        f"- {marker} | {item.get('status') or ''} | {item.get('transport') or ''} | "
                        f"{item.get('content_completeness_status') or item.get('quality_status') or ''} | "
                        f"{item.get('requested_url') or ''} | action={item.get('recommended_next_action') or ''} | "
                        f"archive={item.get('archive_path') or ''}\n"
                    )
            return 0
        if args.batch_command == "rerun-incomplete":
            from .batch import read_batch_review, rerun_batch_report

            strategy_backend = {
                "browser": "browser",
                "crawl4ai": "crawl4ai",
                "feishu_export": "feishu_export",
            }.get(args.strategy, "")
            backend = strategy_backend or args.backend
            if args.strategy == "manual":
                data = read_batch_review(args.batch_path or args.rerun_urls)
                sys.stdout.write(json.dumps({"status": "manual_review", "rerun_urls": data.get("rerun_urls", []), "review": data}, ensure_ascii=False, indent=2) + "\n")
                return 0
            data = rerun_batch_report(
                batch_path=args.batch_path,
                rerun_urls_path=args.rerun_urls,
                archive_dir=args.archive_dir,
                timeout_seconds=args.timeout,
                backend=backend,
                extractor=args.extractor,
                as_documents=not args.plain_url,
                browser_profile=args.profile,
                browser_user_data_dir=args.chrome_user_data_dir,
                browser_executable_path=args.browser_executable,
                browser_storage_state=args.storage_state,
                headless=args.headless,
                check_robots=not args.no_robots_check,
                compare_feishu_export=args.compare_feishu_export,
                browser_dismiss_obstructive_overlays=args.dismiss_obstructive_overlays,
                include_results=args.include_results,
            )
            if args.format == "summary":
                sys.stdout.write(
                    f"Rerun: {data.get('status') or ''}\n"
                    f"Total: {int(data.get('total') or 0)} | OK: {int(data.get('ok') or 0)} | "
                    f"Failed: {int(data.get('failed') or 0)} | Low quality: {int(data.get('low_quality') or 0)}\n"
                    f"Archive dir: {(data.get('rerun') or {}).get('archive_dir') or ''}\n"
                )
            else:
                sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            return 0 if data.get("status") in {"ok", "partial", "empty"} else 2
    if args.command == "fetch-zsxq":
        result = run_zsxq(
            ZsxqRequest(
                action="fetch_topic_by_query",
                group_id=args.group_id,
                query=args.query,
                limit=args.limit,
                timeout_seconds=args.timeout,
                archive_dir=args.archive_dir,
                cli_path=args.cli_path,
            )
        )
        sys.stdout.write(json.dumps(result.to_dict(include_raw=args.include_raw), ensure_ascii=False, indent=2) + "\n")
        return 0 if result.status == "ok" else 2
    if args.command == "archive":
        if args.archive_command == "lifecycle":
            from . import artifact_lifecycle
            try:
                if args.lifecycle_command == "doctor":
                    data = artifact_lifecycle.doctor(core_root=args.core_root, target_root=args.target_root)
                elif args.lifecycle_command == "scan":
                    data = artifact_lifecycle.scan(
                        args.artifact_root,
                        output_dir=args.output_dir,
                        closure_run_id=args.closure_run_id,
                        owner_lane=args.owner_lane,
                        core_root=args.core_root,
                        max_items=args.max_items,
                        max_depth=args.max_depth,
                    )
                elif args.lifecycle_command == "explain":
                    data = artifact_lifecycle.explain(args.manifest, args.artifact_id, core_root=args.core_root)
                elif args.lifecycle_command == "plan-gc":
                    data = artifact_lifecycle.plan_gc(
                        args.manifest,
                        artifact_root=args.artifact_root,
                        quarantine_root=args.quarantine_root,
                        output_path=args.output,
                        owner_state=args.owner_state,
                        process_state=args.process_state,
                        dispatch_state=args.dispatch_state,
                        core_root=args.core_root,
                    )
                elif args.lifecycle_command == "verify":
                    data = artifact_lifecycle.verify(
                        args.plan,
                        args.manifest,
                        artifact_root=args.artifact_root,
                        quarantine_root=args.quarantine_root,
                        core_root=args.core_root,
                    )
                else:
                    data = artifact_lifecycle.restore_synthetic(
                        args.plan,
                        args.receipt,
                        artifact_root=args.artifact_root,
                        quarantine_root=args.quarantine_root,
                        output_path=args.output,
                        execute=args.execute,
                        core_root=args.core_root,
                    )
            except artifact_lifecycle.LifecycleAdapterError as exc:
                data = {
                    "status": "blocked",
                    "error_code": str(exc).split(":", 1)[0],
                    "detail": str(exc),
                    "cleanup_mode": "plan_only",
                    "filesystem_mutation_performed": False,
                }
            if args.format == "summary":
                sys.stdout.write(
                    f"Status: {data.get('status') or ''}\n"
                    f"Profile: {data.get('lifecycle_profile') or 'research_evidence'}\n"
                    f"Mutation scope: {data.get('filesystem_mutation_scope') or 'none'}\n"
                )
                if data.get("manifest_path"):
                    sys.stdout.write(f"Manifest: {data['manifest_path']}\n")
                if data.get("plan_path"):
                    sys.stdout.write(f"Plan: {data['plan_path']}\n")
                if data.get("reason_codes"):
                    sys.stdout.write(f"Reasons: {'; '.join(str(value) for value in data['reason_codes'])}\n")
            else:
                sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            return 0 if data.get("status") in {"ok", "ready"} else 2
        if args.archive_command == "list":
            from .archive import list_archives

            items = list_archives(args.archive_dir, limit=args.limit)
            if args.format == "json":
                sys.stdout.write(json.dumps({"archives": items}, ensure_ascii=False, indent=2) + "\n")
            else:
                sys.stdout.write(f"Archives: {len(items)}\n")
                for item in items:
                    sys.stdout.write(
                        f"- {item.get('created_at') or ''} | {item.get('status') or ''} | "
                        f"{item.get('transport') or ''} | {item.get('title') or item.get('url') or item.get('archive_path')} | "
                        f"complete={item.get('completeness_status') or ''} "
                        f"chars={item.get('completeness_char_ratio') or ''} "
                        f"blocks={item.get('completeness_block_ratio') or ''} | "
                        f"visible={item.get('browser_visible_path') or ''} | "
                        f"visible_html={item.get('browser_visible_html_path') or ''} | "
                        f"preferred={item.get('preferred_readable_path') or ''} | "
                        f"preferred_md={item.get('preferred_markdown_path') or ''} | "
                        f"action={item.get('review_action') or ''} | "
                        f"readable={item.get('readable_html_path') or ''}\n"
                    )
            return 0
        if args.archive_command == "inspect":
            from .archive import read_archive_index

            data = read_archive_index(args.archive_path)
            if args.format == "summary":
                archive_index = data.get("archive_index") or {}
                files = archive_index.get("files") or []
                sys.stdout.write(f"Title: {archive_index.get('title') or data.get('title') or ''}\n")
                sys.stdout.write(f"URL: {archive_index.get('final_url') or data.get('final_url') or ''}\n")
                sys.stdout.write(f"Transport: {archive_index.get('transport') or data.get('transport') or ''}\n")
                readable_path = ""
                evidence = data.get("evidence_archive") or data.get("archive") or {}
                readable_path = evidence.get("readable_html_path") or ""
                preferred_markdown_path = evidence.get("preferred_markdown_path") or ""
                browser_visible_path = evidence.get("browser_visible_path") or ""
                browser_visible_html_path = evidence.get("browser_visible_html_path") or ""
                if not readable_path:
                    for item in files:
                        if item.get("role") == "readable_html":
                            readable_path = item.get("path") or ""
                            break
                if not browser_visible_path:
                    for item in files:
                        if item.get("role") == "browser_visible":
                            browser_visible_path = item.get("path") or ""
                            break
                if not browser_visible_html_path:
                    for item in files:
                        if item.get("role") == "browser_visible_html":
                            browser_visible_html_path = item.get("path") or ""
                            break
                if not preferred_markdown_path:
                    preferred_markdown = archive_index.get("preferred_markdown") or {}
                    preferred_markdown_path = preferred_markdown.get("path") or ""
                if not preferred_markdown_path:
                    for item in files:
                        if item.get("role") == "preferred_markdown":
                            preferred_markdown_path = item.get("path") or ""
                            break
                preferred_readable = archive_index.get("preferred_readable") or {}
                preferred_readable_path = preferred_readable.get("html_path") or readable_path or browser_visible_html_path
                review_recommendation = archive_index.get("review_recommendation") or {}
                feishu_comparison = archive_index.get("feishu_comparison") or {}
                sys.stdout.write(f"Readable HTML: {readable_path}\n")
                sys.stdout.write(f"Preferred markdown: {preferred_markdown_path}\n")
                sys.stdout.write(f"Preferred readable: {preferred_readable_path}\n")
                sys.stdout.write(f"Preferred role: {preferred_readable.get('role') or ''}\n")
                if feishu_comparison:
                    sys.stdout.write(f"Feishu comparison: {feishu_comparison.get('status') or ''}\n")
                    sys.stdout.write(f"Feishu DOCX: {feishu_comparison.get('docx_path') or ''}\n")
                    sys.stdout.write(f"Feishu reasons: {'; '.join(str(item) for item in feishu_comparison.get('reasons', []))}\n")
                preferred_images = (archive_index.get("preferred_markdown") or {}).get("images") or []
                for image in preferred_images[:3]:
                    sys.stdout.write(
                        "Preferred image "
                        f"#{image.get('index') or ''}: "
                        f"line={image.get('line') or ''} "
                        f"alt={image.get('alt') or ''} "
                        f"src={image.get('src') or ''} "
                        f"prev={_block_context_label(image.get('previous_block') or {})} "
                        f"next={_block_context_label(image.get('next_block') or {})}\n"
                    )
                sys.stdout.write(f"Recommended action: {review_recommendation.get('action') or ''}\n")
                sys.stdout.write(f"Recommended path: {review_recommendation.get('path') or ''}\n")
                browser_visible = archive_index.get("browser_visible") or {}
                sys.stdout.write(f"Browser visible: {browser_visible_path}\n")
                sys.stdout.write(f"Browser visible HTML: {browser_visible_html_path}\n")
                sys.stdout.write(
                    f"Browser visible blocks: {browser_visible.get('block_count', 0)} | chars: {browser_visible.get('markdown_chars', 0)}\n"
                )
                sys.stdout.write(f"Files: {len(files)}\n")
                for item in files:
                    sys.stdout.write(f"- {item.get('role')}: {item.get('path')}\n")
            else:
                sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            return 0
        if args.archive_command == "pack":
            from .archive import pack_archive

            data = pack_archive(args.archive_path, args.output)
            sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            return 0
        if args.archive_command == "compare-feishu-export":
            from .archive import compare_feishu_export_for_archive

            data = compare_feishu_export_for_archive(args.archive_path, timeout_seconds=args.timeout)
            if args.format == "json":
                sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            else:
                comparison = data.get("comparison") or {}
                sys.stdout.write(f"Status: {data.get('status') or ''}\n")
                sys.stdout.write(f"Archive: {data.get('archive_path') or ''}\n")
                sys.stdout.write(f"DOCX: {data.get('docx_path') or ''}\n")
                sys.stdout.write(f"Comparison: {comparison.get('status') or ''}\n")
                sys.stdout.write(f"Reasons: {'; '.join(str(item) for item in comparison.get('reasons', []))}\n")
            return 0
        if args.archive_command == "rebuild":
            return _run_archive_rebuild(args)
        if args.archive_command == "batch-reports":
            from .batch import list_batch_reports

            reports = list_batch_reports(args.archive_dir, limit=args.limit)
            if args.format == "json":
                sys.stdout.write(json.dumps({"batch_reports": reports}, ensure_ascii=False, indent=2) + "\n")
            else:
                sys.stdout.write(f"Batch reports: {len(reports)}\n")
                for item in reports:
                    sys.stdout.write(
                        f"- {item.get('created_at') or ''} | {item.get('status') or ''} | "
                        f"{int(item.get('total') or 0)} total / {int(item.get('failed') or 0)} failed / "
                        f"{int(item.get('low_quality') or 0)} low quality | review: {item.get('review_markdown_path') or ''} | "
                        f"rerun: {item.get('rerun_urls_path') or ''}\n"
                    )
            return 0
        if args.archive_command == "rerun-batch":
            from .batch import rerun_batch_report

            data = rerun_batch_report(
                batch_path=args.batch_path,
                rerun_urls_path=args.rerun_urls,
                archive_dir=args.archive_dir,
                timeout_seconds=args.timeout,
                backend=args.backend,
                extractor=args.extractor,
                as_documents=not args.plain_url,
                browser_profile=args.profile,
                browser_user_data_dir=args.chrome_user_data_dir,
                browser_executable_path=args.browser_executable,
                browser_storage_state=args.storage_state,
                headless=args.headless,
                check_robots=not args.no_robots_check,
                compare_feishu_export=args.compare_feishu_export,
                browser_dismiss_obstructive_overlays=args.dismiss_obstructive_overlays,
                include_results=args.include_results,
            )
            sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            return 0 if data.get("status") in {"ok", "partial", "empty"} else 2
    if args.command == "convert-attachment":
        from .attachments import convert_attachment

        data = convert_attachment(args.input_path, args.output)
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return 0
    if args.command == "archivebox":
        from .archivebox_backend import ArchiveBoxRequest, run_archivebox_add
        from .batch import read_url_list

        urls = list(args.url or [])
        if args.urls_file:
            urls.extend(read_url_list(args.urls_file))
        if not urls:
            raise SystemExit("archivebox add requires --url or --urls-file")
        result = run_archivebox_add(
            ArchiveBoxRequest(
                urls=urls,
                archivebox_dir=args.archivebox_dir,
                archive_dir=args.archive_dir,
                executable=args.executable,
                timeout_seconds=args.timeout,
                extra_args=list(args.extra_arg or []),
            )
        )
        sys.stdout.write(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n")
        return 0 if result.status == "ok" else 2
    if args.command == "browsertrix":
        from .browsertrix_backend import BrowsertrixRequest, run_browsertrix

        if not args.url:
            raise SystemExit("browsertrix archive requires --url")
        result = run_browsertrix(
            BrowsertrixRequest(
                urls=list(args.url),
                output_dir=args.output_dir,
                collection=args.collection,
                archive_dir=args.archive_dir,
                docker_executable=args.docker_executable,
                image=args.image,
                timeout_seconds=args.timeout,
                profile_path=args.profile_path,
                extra_args=list(args.extra_arg or []),
                copy_artifacts_to_archive=not bool(args.reference_artifacts),
            )
        )
        sys.stdout.write(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n")
        return 0 if result.status in {"ok", "partial"} else 2
    if args.command == "readeck":
        from .readeck_backend import ReadeckRequest, run_readeck

        html = Path(args.html_file).read_text(encoding="utf-8") if args.html_file else ""
        result = run_readeck(
            ReadeckRequest(
                url=args.url,
                html=html,
                title=args.title,
                labels=list(args.label or []),
                base_url=args.base_url,
                token_env=args.token_env,
                archive_dir=args.archive_dir,
                timeout_seconds=args.timeout,
                wait_seconds=args.wait,
            )
        )
        sys.stdout.write(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n")
        return 0 if result.status == "accepted" else 2
    if args.command == "zsxq":
        if args.zsxq_command == "classify-export":
            from .zsxq_classify import classify_exports

            result = classify_exports(
                args.export_path,
                output_dir=args.output_dir,
                taxonomy_path=args.taxonomy,
            )
            if args.format == "json":
                sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            else:
                sys.stdout.write(
                    "\n".join(
                        [
                            f"Status: {result['status']}",
                            f"Topics: {result['topic_count']}",
                            f"Needs human review: {result['needs_human_review_count']}",
                            f"JSON: {result['classification_json_path']}",
                            f"Markdown: {result['classification_markdown_path']}",
                        ]
                    )
                    + "\n"
                )
            return 0 if result.get("status") == "ok" else 2
        if args.zsxq_command == "merge-exports":
            from .zsxq_merge import merge_exports

            result = merge_exports(args.export_path, output_dir=args.output_dir)
            if args.format == "json":
                sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            else:
                sys.stdout.write(
                    "\n".join(
                        [
                            f"Status: {result['status']}",
                            f"Topics: {result['topic_count']}",
                            f"Completed: {result['completed']}",
                            f"Oldest: {result['oldest_topic_time']}",
                            f"Newest: {result['newest_topic_time']}",
                            f"Archive: {result['archive_path']}",
                            f"Summary: {result['summary_path']}",
                        ]
                    )
                    + "\n"
                )
            return 0 if result.get("status") in {"ok", "partial"} else 2
        action_by_command = {
            "auth-status": "auth_status",
            "list-groups": "list_groups",
            "list-topics": "list_topics",
            "search-topics": "search_topics",
            "get-topic": "get_topic",
            "fetch-topic": "fetch_topic_by_query",
            "export-group": "export_group",
        }
        request = ZsxqRequest(
            action=action_by_command[args.zsxq_command],
            group_id=getattr(args, "group_id", ""),
            topic_id=getattr(args, "topic_id", ""),
            query=getattr(args, "query", ""),
            limit=getattr(args, "limit", 20),
            end_time=getattr(args, "end_time", ""),
            timeout_seconds=args.timeout,
            archive_dir=args.archive_dir,
            cli_path=args.cli_path,
            max_pages=getattr(args, "max_pages", 0),
            with_detail=getattr(args, "with_detail", False),
            with_comments=getattr(args, "with_comments", False),
        )
        result = run_zsxq(request)
        sys.stdout.write(json.dumps(result.to_dict(include_raw=args.include_raw), ensure_ascii=False, indent=2) + "\n")
        return 0 if result.status in {"ok", "partial"} and args.zsxq_command == "export-group" else 0 if result.status == "ok" else 2
    if args.command == "mcp":
        from .mcp_server import main as mcp_main

        return mcp_main()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
