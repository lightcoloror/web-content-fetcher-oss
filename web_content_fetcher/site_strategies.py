from __future__ import annotations

import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SiteStrategy:
    site_key: str
    kind: str
    fetch_profile: str = "generic"
    allowed_transports: list[str] = field(default_factory=lambda: ["direct", "browser"])
    preferred_backend_order: list[str] = field(default_factory=lambda: ["direct", "browser"])
    login_required_hint: str = ""
    public_fallbacks: list[str] = field(default_factory=list)
    shell_detection_hints: list[str] = field(default_factory=list)
    completeness_expectations: dict[str, Any] = field(default_factory=dict)
    recommended_manual_fallback: str = ""
    route_options: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _strategy(
    *,
    site_key: str,
    kind: str,
    fetch_profile: str = "generic",
    allowed_transports: list[str] | None = None,
    preferred_backend_order: list[str] | None = None,
    login_required_hint: str = "",
    public_fallbacks: list[str] | None = None,
    shell_detection_hints: list[str] | None = None,
    completeness_expectations: dict[str, Any] | None = None,
    recommended_manual_fallback: str = "",
    route_options: dict[str, Any] | None = None,
    reason: str = "",
) -> SiteStrategy:
    return SiteStrategy(
        site_key=site_key,
        kind=kind,
        fetch_profile=fetch_profile,
        allowed_transports=allowed_transports or ["direct", "browser"],
        preferred_backend_order=preferred_backend_order or ["direct", "browser"],
        login_required_hint=login_required_hint,
        public_fallbacks=public_fallbacks or [],
        shell_detection_hints=shell_detection_hints or [],
        completeness_expectations=completeness_expectations or {},
        recommended_manual_fallback=recommended_manual_fallback,
        route_options=route_options or {},
        reason=reason,
    )


def classify_site_strategy(url: str) -> SiteStrategy:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path
    if ("feishu.cn" in host or "larksuite.com" in host) and re.search(r"/docx?/", path):
        return _strategy(
            site_key="feishu_lark",
            kind="feishu_doc",
            fetch_profile="feishu_doc",
            allowed_transports=["browser", "feishu_export", "jina_reader", "direct"],
            preferred_backend_order=["feishu_export", "browser", "jina_reader", "direct"],
            login_required_hint="Private Feishu/Lark docs require the local browser profile or official API/export credentials.",
            public_fallbacks=["jina_reader"],
            shell_detection_hints=["login prompt", "anonymous preview", "virtual list placeholder", "Feishu shell text"],
            completeness_expectations={"images": "compare browser/export/gallery counts", "tables": "compare browser/export table counts"},
            recommended_manual_fallback="Use official Feishu export/API or open with logged-in browser profile.",
            route_options={
                "use_browser": True,
                "browser_auto_scroll": True,
                "browser_wait_until": "domcontentloaded",
                "browser_scroll_steps": 36,
                "browser_settle_ms": 1200,
                "download_images": True,
                "save_screenshot": True,
            },
            reason="Feishu docx links need browser rendering, export/API comparison, and session-aware image downloads.",
        )
    if ("feishu.cn" in host or "larksuite.com" in host) and re.search(r"/wiki/", path):
        return _strategy(
            site_key="feishu_lark",
            kind="feishu_wiki",
            fetch_profile="feishu_doc",
            allowed_transports=["browser", "feishu_export", "jina_reader", "direct"],
            preferred_backend_order=["feishu_export", "browser", "jina_reader", "direct"],
            login_required_hint="Private Feishu/Lark wiki pages require the local browser profile or official API/export credentials.",
            public_fallbacks=["jina_reader"],
            shell_detection_hints=["login prompt", "anonymous preview", "virtual list placeholder", "Feishu shell text"],
            completeness_expectations={"images": "compare browser/export/gallery counts", "tables": "compare browser/export table counts"},
            recommended_manual_fallback="Use official Feishu export/API or open with logged-in browser profile.",
            route_options={
                "use_browser": True,
                "browser_auto_scroll": True,
                "browser_wait_until": "domcontentloaded",
                "browser_scroll_steps": 40,
                "browser_settle_ms": 1200,
                "download_images": True,
                "save_screenshot": True,
            },
            reason="Feishu wiki links need browser rendering and logged-in session state.",
        )
    if "zsxq.com" in host:
        return _strategy(
            site_key="zsxq",
            kind="zsxq",
            allowed_transports=["zsxq-cli", "browser"],
            preferred_backend_order=["zsxq-cli", "browser"],
            login_required_hint="Knowledge Planet topics require official zsxq-cli auth or logged-in browser state.",
            shell_detection_hints=["web app shell", "login wall", "group permission prompt"],
            completeness_expectations={"images": "topic images should be rendered in markdown/html metadata"},
            recommended_manual_fallback="Run zsxq-cli auth login, then fetch by resolved topic_id.",
            route_options={
                "use_browser": True,
                "browser_auto_scroll": True,
                "browser_wait_until": "domcontentloaded",
                "browser_scroll_steps": 10,
                "browser_settle_ms": 900,
                "save_screenshot": True,
            },
            reason="Knowledge Planet web links should prefer official zsxq-cli by topic ID; browser shell is auxiliary evidence.",
        )
    if "mp.weixin.qq.com" in host and path.startswith("/s"):
        return _strategy(
            site_key="wechat_mp",
            kind="wechat_article",
            allowed_transports=["browser", "direct"],
            preferred_backend_order=["browser", "direct"],
            login_required_hint="Use only authorized logged-in browser state when WeChat limits public article rendering.",
            shell_detection_hints=["security check", "environment abnormal", "image lazy-load placeholders"],
            completeness_expectations={"images": "download lazy images after scrolling", "tables": "preserve article tables if present"},
            recommended_manual_fallback="Open with persistent browser profile and rerun with image download.",
            route_options={
                "use_browser": True,
                "browser_auto_scroll": True,
                "browser_wait_until": "domcontentloaded",
                "browser_scroll_steps": 14,
                "browser_settle_ms": 900,
                "download_images": True,
                "save_screenshot": True,
            },
            reason="WeChat public account articles need browser scrolling and image downloads for full text/image archives.",
        )
    if "yuque.com" in host:
        return _strategy(
            site_key="yuque",
            kind="yuque_doc",
            allowed_transports=["browser", "crawl4ai", "direct"],
            preferred_backend_order=["browser", "crawl4ai", "direct"],
            login_required_hint="Private Yuque pages require logged-in browser state.",
            shell_detection_hints=["client-side shell", "login prompt", "permission prompt"],
            completeness_expectations={"images": "image gallery is mandatory", "tables": "tables must render in readable HTML"},
            recommended_manual_fallback="Rerun with browser profile or Crawl4AI selector/wait options.",
            route_options={
                "use_browser": True,
                "browser_auto_scroll": True,
                "browser_wait_until": "domcontentloaded",
                "browser_scroll_steps": 12,
                "browser_settle_ms": 900,
                "download_images": True,
                "save_screenshot": True,
            },
            reason="Yuque pages are commonly rendered client-side and need browser/Crawl4AI fallback for full text.",
        )
    if "kdocs.cn" in host or "wps.cn" in host:
        return _strategy(
            site_key="kdocs",
            kind="kdocs_doc",
            allowed_transports=["browser", "direct"],
            preferred_backend_order=["browser", "direct"],
            login_required_hint="Private KDocs pages require local browser login state.",
            shell_detection_hints=["WPS shell", "permission prompt", "login prompt"],
            completeness_expectations={"images": "use screenshot/export when inline assets are hidden", "tables": "prefer browser/export for sheets"},
            recommended_manual_fallback="Use logged-in browser profile or manual export from KDocs.",
            route_options={
                "use_browser": True,
                "browser_auto_scroll": True,
                "browser_wait_until": "domcontentloaded",
                "browser_scroll_steps": 12,
                "browser_settle_ms": 1000,
                "download_images": True,
                "save_screenshot": True,
            },
            reason="KDocs links are dynamic document pages and should use browser/profile first.",
        )
    if "xiaohongshu.com" in host or "xhslink.com" in host:
        return _strategy(
            site_key="xhs",
            kind="xhs_note",
            allowed_transports=["xhs_browser_state", "xhs_redbook_cli", "browser"],
            preferred_backend_order=["xhs_browser_state", "xhs_redbook_cli", "browser"],
            login_required_hint="Only use authenticated, human-authorized local browser state for Xiaohongshu content.",
            shell_detection_hints=["anti-automation shell", "login prompt", "empty note state"],
            completeness_expectations={"images": "note image list should be preserved", "tables": "not usually expected"},
            recommended_manual_fallback="Use browser-state/manual export or vetted local XHS tool; do not bypass access controls.",
            route_options={
                "use_browser": True,
                "browser_auto_scroll": True,
                "browser_wait_until": "domcontentloaded",
                "browser_scroll_steps": 8,
                "browser_settle_ms": 1000,
                "download_images": True,
                "save_screenshot": True,
            },
            reason="Xiaohongshu should use authorized browser state or vetted local tools, never access-control bypass.",
        )
    if "notion.so" in host:
        return _strategy(
            site_key="notion",
            kind="notion_page",
            allowed_transports=["browser", "crawl4ai", "direct"],
            preferred_backend_order=["browser", "crawl4ai", "direct"],
            login_required_hint="Private Notion pages require logged-in browser state.",
            shell_detection_hints=["client-side shell", "loading skeleton", "permission prompt"],
            recommended_manual_fallback="Rerun with logged-in browser profile or export from Notion.",
            route_options={
                "use_browser": True,
                "browser_auto_scroll": True,
                "browser_wait_until": "domcontentloaded",
                "browser_scroll_steps": 16,
                "browser_settle_ms": 1000,
                "download_images": True,
                "save_screenshot": True,
            },
            reason="Notion public/share pages are client-side rendered and need browser fallback.",
        )
    if "docs.qq.com" in host:
        return _strategy(
            site_key="tencent_docs",
            kind="qq_doc",
            allowed_transports=["browser", "direct"],
            preferred_backend_order=["browser", "direct"],
            login_required_hint="Private Tencent Docs require local browser login state.",
            shell_detection_hints=["Tencent Docs shell", "login prompt", "permission prompt"],
            recommended_manual_fallback="Use logged-in browser profile or manual export.",
            route_options={
                "use_browser": True,
                "browser_auto_scroll": True,
                "browser_wait_until": "domcontentloaded",
                "browser_scroll_steps": 10,
                "browser_settle_ms": 1000,
                "save_screenshot": True,
            },
            reason="Tencent Docs links are dynamic document pages and need browser fallback.",
        )
    if re.search(r"\.(pdf|docx?|pptx?|xlsx?|csv|md|txt)$", path, re.I):
        return _strategy(
            site_key="file",
            kind="file",
            allowed_transports=["direct", "browser"],
            preferred_backend_order=["direct", "browser"],
            recommended_manual_fallback="Use MarkItDown or the local visual/document pipeline after download.",
            reason="File-like URL; current MVP stores raw response and metadata when direct fetch is available.",
        )
    return _strategy(
        site_key="generic",
        kind="web_page",
        allowed_transports=["direct", "browser", "crawl4ai"],
        preferred_backend_order=["direct", "browser", "crawl4ai"],
        recommended_manual_fallback="If extraction is weak, rerun with browser or Crawl4AI.",
        reason="Default web page route.",
    )
