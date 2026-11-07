from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class BackendSpec:
    """Stable metadata for a fetch or archive bridge."""

    name: str
    kind: str
    description: str
    optional_extra: str = ""
    auto_eligible: bool = False
    private_content_safe: bool = True
    selectable_fetch_backend: bool = True


_SPECS = (
    BackendSpec("auto", "orchestrator", "Quality-gated local fetch route.", auto_eligible=True),
    BackendSpec("direct", "fetch", "Direct HTTP fetch."),
    BackendSpec("browser", "fetch", "Local Playwright browser capture.", optional_extra="browser"),
    BackendSpec("crawl4ai", "fetch", "Local Crawl4AI browser and Markdown backend.", optional_extra="crawl4ai"),
    BackendSpec("firecrawl", "fetch", "Hosted Firecrawl API backend.", private_content_safe=False),
    BackendSpec("scrapling", "fetch", "Local Scrapling static, dynamic, or stealthy backend.", optional_extra="scrapling"),
    BackendSpec("feishu_export", "fetch", "Official Feishu/Lark document export."),
    BackendSpec("werss", "fetch", "Local WeRSS article archive backend."),
    BackendSpec("wechat_download_api", "fetch", "Local official-login WeChat article API bridge."),
    BackendSpec("xhs_auto", "fetch", "Authorized local XHS route selection."),
    BackendSpec("xhs_redbook_cli", "fetch", "Local authorized redbook CLI bridge."),
    BackendSpec("xhs_browser_state", "fetch", "Local browser state extraction for XHS.", optional_extra="browser"),
    BackendSpec("monolith", "snapshot", "Single-file HTML or MHTML snapshot bridge.", selectable_fetch_backend=False),
    BackendSpec("readeck", "archive", "External readable archive and EPUB/Markdown export bridge.", selectable_fetch_backend=False),
    BackendSpec("browsertrix", "archive", "External WACZ/WARC evidence archive bridge.", selectable_fetch_backend=False),
    BackendSpec("crawlee", "crawl", "Scoped site crawl job with queue and resume support.", optional_extra="crawlee", selectable_fetch_backend=False),
)

BACKEND_REGISTRY = {spec.name: spec for spec in _SPECS}


def fetch_backend_choices() -> list[str]:
    return [spec.name for spec in _SPECS if spec.selectable_fetch_backend]


def get_backend_spec(name: str) -> BackendSpec | None:
    return BACKEND_REGISTRY.get((name or "").strip().lower())


def normalize_fetch_backend(name: str) -> str | None:
    candidate = (name or "auto").strip().lower()
    spec = get_backend_spec(candidate)
    if spec and spec.selectable_fetch_backend:
        return spec.name
    return None


def backend_catalog() -> list[dict[str, Any]]:
    return [asdict(spec) for spec in _SPECS]
