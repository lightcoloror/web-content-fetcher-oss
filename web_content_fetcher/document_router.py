from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

from .core import FetchRequest, FetchResult, direct_fetch, fetch_content, structure_content
from .site_strategies import classify_site_strategy
from .zsxq_cli import ZsxqRequest, run_zsxq, topic_images, topic_payload, topic_to_markdown


@dataclass
class DocumentRoute:
    url: str
    kind: str
    fetch_profile: str = "generic"
    use_browser: bool = False
    browser_auto_scroll: bool = False
    browser_wait_until: str = "load"
    browser_scroll_steps: int = 8
    browser_settle_ms: int = 700
    browser_dismiss_obstructive_overlays: bool = True
    download_images: bool = False
    save_screenshot: bool = False
    reason: str = ""


def classify_document_url(url: str) -> DocumentRoute:
    strategy = classify_site_strategy(url)
    options = strategy.route_options
    return DocumentRoute(
        url=url,
        kind=strategy.kind,
        fetch_profile=strategy.fetch_profile,
        use_browser=bool(options.get("use_browser", False)),
        browser_auto_scroll=bool(options.get("browser_auto_scroll", False)),
        browser_wait_until=str(options.get("browser_wait_until", "load")),
        browser_scroll_steps=int(options.get("browser_scroll_steps", 8)),
        browser_settle_ms=int(options.get("browser_settle_ms", 700)),
        browser_dismiss_obstructive_overlays=bool(options.get("browser_dismiss_obstructive_overlays", True)),
        download_images=bool(options.get("download_images", False)),
        save_screenshot=bool(options.get("save_screenshot", False)),
        reason=strategy.reason,
    )


def _zsxq_topic_id_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("topic_id", "topicId", "topic"):
        values = query.get(key)
        if values and values[0]:
            return str(values[0])
    match = re.search(r"(?:topic_id=|topicdetail/|topic/)(\d{8,})", url)
    return match.group(1) if match else ""


def _resolve_zsxq_topic_id(url: str, *, timeout_seconds: int) -> tuple[str, str, list[str]]:
    topic_id = _zsxq_topic_id_from_url(url)
    if topic_id:
        return topic_id, url, []
    errors: list[str] = []
    try:
        _raw, _content_type, final_url = direct_fetch(
            FetchRequest(
                url=url,
                timeout_seconds=max(15, timeout_seconds),
                check_robots=False,
            )
        )
        topic_id = _zsxq_topic_id_from_url(final_url)
        return topic_id, final_url, errors
    except Exception as exc:
        errors.append(f"zsxq_resolve:{type(exc).__name__}:{str(exc)[:200]}")
    return "", url, errors


def _fetch_zsxq_topic_link(
    url: str,
    *,
    archive_dir: str,
    timeout_seconds: int,
    check_robots: bool,
    extractor: str,
) -> FetchResult | None:
    topic_id, final_url, resolve_errors = _resolve_zsxq_topic_id(url, timeout_seconds=timeout_seconds)
    if not topic_id:
        return None
    zsxq = run_zsxq(
        ZsxqRequest(
            action="get_topic",
            topic_id=topic_id,
            timeout_seconds=timeout_seconds,
            archive_dir="",
        )
    )
    if zsxq.status != "ok":
        return None
    topic = topic_payload(zsxq.data)
    if not topic:
        return None
    markdown = topic_to_markdown(topic)
    request = FetchRequest(
        url=url,
        fetch_profile="generic",
        timeout_seconds=timeout_seconds,
        extractor=extractor,
        archive_dir=archive_dir,
        check_robots=False,
        site_strategy=classify_site_strategy(url).to_dict(),
    )
    result = structure_content(
        request,
        markdown,
        "text/markdown; charset=utf-8",
        f"zsxq://topic/{topic_id}",
        "zsxq-cli",
        resolve_errors,
        {
            "title": str(topic.get("title") or f"知识星球主题 {topic_id}"),
            "images": topic_images(topic),
            "site_data": {
                "zsxq": {
                    "source_url": url,
                    "resolved_url": final_url,
                    "topic_id": topic_id,
                    "resolved_from_link": True,
                    "group": topic.get("group") or {},
                    "owner": topic.get("owner") or {},
                    "counts": topic.get("counts") or {},
                    "create_time": topic.get("create_time") or "",
                    "transport": "zsxq-cli",
                    "raw_topic": topic,
                }
            },
            "extracted_metadata": {
                "zsxq": {
                    "topic_id": topic_id,
                    "source_url": url,
                    "resolved_url": final_url,
                    "transport": "zsxq-cli",
                }
            },
        },
    )
    result.site_data = {
        **(result.site_data or {}),
        "document_route": classify_document_url(url).__dict__,
        "site_strategy": classify_site_strategy(url).to_dict(),
    }
    result.robots = {
        "checked": False,
        "allowed": None,
        "warning": "not_applicable:official_zsxq_cli",
    }
    return result


def build_fetch_request(
    url: str,
    *,
    archive_dir: str = "",
    timeout_seconds: int = 30,
    browser_profile: str = "",
    browser_user_data_dir: str = "",
    browser_executable_path: str = "",
    browser_storage_state: str = "",
    headless: bool = False,
    check_robots: bool = True,
    extractor: str = "auto",
    backend: str = "auto",
    crawl4ai_wait_for: str = "",
    crawl4ai_css_selector: str = "",
    crawl4ai_screenshot: bool = False,
    crawl4ai_pdf: bool = False,
    crawl4ai_cache_mode: str = "bypass",
    firecrawl_api_url: str = "",
    firecrawl_api_key_env: str = "FIRECRAWL_API_KEY",
    firecrawl_formats: list[str] | None = None,
    firecrawl_only_main_content: bool = True,
    firecrawl_wait_for_ms: int = 0,
    scrapling_mode: str = "static",
    scrapling_headless: bool = True,
    scrapling_network_idle: bool = False,
    scrapling_wait_ms: int = 0,
    scrapling_wait_selector: str = "",
    scrapling_real_chrome: bool = False,
    scrapling_user_data_dir: str = "",
    scrapling_python_path: str = "",
    scrapling_python_executable: str = "",
    compare_feishu_export: bool = False,
    browser_dismiss_obstructive_overlays: bool = True,
    wechat_api_base_url: str = "",
) -> FetchRequest:
    route = classify_document_url(url)
    strategy = classify_site_strategy(url)
    return FetchRequest(
        url=url,
        fetch_profile=route.fetch_profile,
        timeout_seconds=timeout_seconds,
        extractor=extractor,
        backend=backend,
        use_browser=route.use_browser,
        browser_headless=headless,
        browser_profile=browser_profile,
        browser_user_data_dir=browser_user_data_dir,
        browser_executable_path=browser_executable_path,
        browser_storage_state=browser_storage_state,
        browser_wait_until=route.browser_wait_until,
        browser_auto_scroll=route.browser_auto_scroll,
        browser_scroll_steps=route.browser_scroll_steps,
        browser_settle_ms=route.browser_settle_ms,
        browser_dismiss_obstructive_overlays=browser_dismiss_obstructive_overlays and route.browser_dismiss_obstructive_overlays,
        save_screenshot=route.save_screenshot,
        download_images=route.download_images,
        archive_dir=archive_dir,
        check_robots=check_robots,
        crawl4ai_wait_for=crawl4ai_wait_for,
        crawl4ai_css_selector=crawl4ai_css_selector,
        crawl4ai_screenshot=crawl4ai_screenshot,
        crawl4ai_pdf=crawl4ai_pdf,
        crawl4ai_cache_mode=crawl4ai_cache_mode,
        firecrawl_api_url=firecrawl_api_url,
        firecrawl_api_key_env=firecrawl_api_key_env,
        firecrawl_formats=firecrawl_formats or ["markdown", "html", "links"],
        firecrawl_only_main_content=firecrawl_only_main_content,
        firecrawl_wait_for_ms=firecrawl_wait_for_ms,
        scrapling_mode=scrapling_mode,
        scrapling_headless=scrapling_headless,
        scrapling_network_idle=scrapling_network_idle,
        scrapling_wait_ms=scrapling_wait_ms,
        scrapling_wait_selector=scrapling_wait_selector,
        scrapling_real_chrome=scrapling_real_chrome,
        scrapling_user_data_dir=scrapling_user_data_dir,
        scrapling_python_path=scrapling_python_path,
        scrapling_python_executable=scrapling_python_executable,
        compare_feishu_export=compare_feishu_export,
        wechat_api_base_url=wechat_api_base_url,
        site_strategy=strategy.to_dict(),
    )


def fetch_document_link(
    url: str,
    *,
    archive_dir: str = "",
    timeout_seconds: int = 30,
    browser_profile: str = "",
    browser_user_data_dir: str = "",
    browser_executable_path: str = "",
    browser_storage_state: str = "",
    headless: bool = False,
    check_robots: bool = True,
    extractor: str = "auto",
    backend: str = "auto",
    crawl4ai_wait_for: str = "",
    crawl4ai_css_selector: str = "",
    crawl4ai_screenshot: bool = False,
    crawl4ai_pdf: bool = False,
    crawl4ai_cache_mode: str = "bypass",
    firecrawl_api_url: str = "",
    firecrawl_api_key_env: str = "FIRECRAWL_API_KEY",
    firecrawl_formats: list[str] | None = None,
    firecrawl_only_main_content: bool = True,
    firecrawl_wait_for_ms: int = 0,
    scrapling_mode: str = "static",
    scrapling_headless: bool = True,
    scrapling_network_idle: bool = False,
    scrapling_wait_ms: int = 0,
    scrapling_wait_selector: str = "",
    scrapling_real_chrome: bool = False,
    scrapling_user_data_dir: str = "",
    scrapling_python_path: str = "",
    scrapling_python_executable: str = "",
    compare_feishu_export: bool = False,
    browser_dismiss_obstructive_overlays: bool = True,
    wechat_api_base_url: str = "",
) -> FetchResult:
    route = classify_document_url(url)
    strategy = classify_site_strategy(url)
    if route.kind == "zsxq" and backend == "auto":
        result = _fetch_zsxq_topic_link(
            url,
            archive_dir=archive_dir,
            timeout_seconds=timeout_seconds,
            check_robots=check_robots,
            extractor=extractor,
        )
        if result is not None:
            return result
    request = build_fetch_request(
        url,
        archive_dir=archive_dir,
        timeout_seconds=timeout_seconds,
        browser_profile=browser_profile,
        browser_user_data_dir=browser_user_data_dir,
        browser_executable_path=browser_executable_path,
        browser_storage_state=browser_storage_state,
        headless=headless,
        check_robots=check_robots,
        extractor=extractor,
        backend=backend,
        crawl4ai_wait_for=crawl4ai_wait_for,
        crawl4ai_css_selector=crawl4ai_css_selector,
        crawl4ai_screenshot=crawl4ai_screenshot,
        crawl4ai_pdf=crawl4ai_pdf,
        crawl4ai_cache_mode=crawl4ai_cache_mode,
        firecrawl_api_url=firecrawl_api_url,
        firecrawl_api_key_env=firecrawl_api_key_env,
        firecrawl_formats=firecrawl_formats or ["markdown", "html", "links"],
        firecrawl_only_main_content=firecrawl_only_main_content,
        firecrawl_wait_for_ms=firecrawl_wait_for_ms,
        scrapling_mode=scrapling_mode,
        scrapling_headless=scrapling_headless,
        scrapling_network_idle=scrapling_network_idle,
        scrapling_wait_ms=scrapling_wait_ms,
        scrapling_wait_selector=scrapling_wait_selector,
        scrapling_real_chrome=scrapling_real_chrome,
        scrapling_user_data_dir=scrapling_user_data_dir,
        scrapling_python_path=scrapling_python_path,
        scrapling_python_executable=scrapling_python_executable,
        compare_feishu_export=compare_feishu_export,
        browser_dismiss_obstructive_overlays=browser_dismiss_obstructive_overlays,
        wechat_api_base_url=wechat_api_base_url,
    )
    result = fetch_content(request)
    result.site_data = {
        **(result.site_data or {}),
        "document_route": route.__dict__,
        "site_strategy": strategy.to_dict(),
    }
    return result
