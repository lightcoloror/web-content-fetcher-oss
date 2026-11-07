from __future__ import annotations

import base64
import inspect
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Crawl4AIRequest:
    url: str
    timeout_seconds: int = 30
    headless: bool = True
    wait_for: str = ""
    css_selector: str = ""
    screenshot: bool = False
    pdf: bool = False
    cache_mode: str = "bypass"
    base_directory: str = ""


def _construct(cls: Any, kwargs: dict[str, Any]) -> Any:
    try:
        signature = inspect.signature(cls)
        supported = {key: value for key, value in kwargs.items() if key in signature.parameters}
        return cls(**supported)
    except Exception:
        return cls()


def _cache_mode_value(name: str) -> Any:
    try:
        from crawl4ai import CacheMode
    except Exception:
        return None
    normalized = (name or "bypass").upper()
    return getattr(CacheMode, normalized, None)


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    try:
        return dict(value)
    except Exception:
        return {}


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"bytes": len(value)}
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    try:
        return _to_jsonable(vars(value))
    except Exception:
        return str(value)


def _decode_binary(value: Any) -> bytes:
    if not value:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        payload = value.split(",", 1)[-1] if value.startswith("data:") else value
        try:
            return base64.b64decode(payload, validate=False)
        except Exception:
            return value.encode("utf-8", errors="replace")
    return b""


def _ensure_base_directory(base_directory: str = "") -> str:
    configured = os.getenv("CRAWL4_AI_BASE_DIRECTORY") or base_directory or os.getenv("WEB_CONTENT_FETCHER_CRAWL4AI_BASE_DIR")
    base = Path(configured) if configured else Path(tempfile.gettempdir()) / "web-content-fetcher-crawl4ai"
    def prepare(path: Path) -> None:
        crawl_dir = path / ".crawl4ai"
        crawl_dir.mkdir(parents=True, exist_ok=True)
        for name in ("html_content", "cleaned_html", "markdown_content", "extracted_content", "screenshots", "cache"):
            (crawl_dir / name).mkdir(parents=True, exist_ok=True)

    try:
        base.mkdir(parents=True, exist_ok=True)
        prepare(base)
    except Exception:
        base = Path(tempfile.gettempdir()) / "web-content-fetcher-crawl4ai"
        base.mkdir(parents=True, exist_ok=True)
        prepare(base)
    os.environ["CRAWL4_AI_BASE_DIRECTORY"] = str(base)
    return str(base)


async def run_crawl4ai(request: Crawl4AIRequest) -> dict[str, Any]:
    base_directory = _ensure_base_directory(request.base_directory)
    from crawl4ai import AsyncWebCrawler

    try:
        from crawl4ai import BrowserConfig, CrawlerRunConfig
    except Exception:
        BrowserConfig = None
        CrawlerRunConfig = None

    browser_config = _construct(BrowserConfig, {"headless": request.headless}) if BrowserConfig else None
    run_config_kwargs: dict[str, Any] = {
        "wait_for": request.wait_for or None,
        "css_selector": request.css_selector or None,
        "screenshot": request.screenshot,
        "pdf": request.pdf,
        "page_timeout": max(1, int(request.timeout_seconds or 30)) * 1000,
    }
    cache_mode = _cache_mode_value(request.cache_mode)
    if cache_mode is not None:
        run_config_kwargs["cache_mode"] = cache_mode
    run_config = _construct(CrawlerRunConfig, {key: value for key, value in run_config_kwargs.items() if value is not None}) if CrawlerRunConfig else None

    crawler_kwargs = {"config": browser_config} if browser_config is not None else {}
    try:
        crawler = AsyncWebCrawler(**crawler_kwargs)
    except TypeError:
        crawler = AsyncWebCrawler()
    async with crawler:
        try:
            result = await crawler.arun(url=request.url, config=run_config)
        except TypeError:
            result = await crawler.arun(url=request.url)

    markdown = str(getattr(result, "markdown", "") or "")
    html = str(getattr(result, "html", "") or "")
    cleaned_html = str(getattr(result, "cleaned_html", "") or "")
    final_url = str(getattr(result, "url", "") or request.url)
    success = bool(getattr(result, "success", True))
    error_message = str(getattr(result, "error_message", "") or "")
    return {
        "success": success,
        "error_message": error_message,
        "url": final_url,
        "markdown": markdown,
        "html": html,
        "cleaned_html": cleaned_html,
        "screenshot_bytes": _decode_binary(getattr(result, "screenshot", None)),
        "pdf_bytes": _decode_binary(getattr(result, "pdf", None)),
        "media": _to_jsonable(_to_dict(getattr(result, "media", None))),
        "links": _to_jsonable(_to_dict(getattr(result, "links", None))),
        "metadata": _to_jsonable(_to_dict(getattr(result, "metadata", None))),
        "config": {
            "wait_for": request.wait_for,
            "css_selector": request.css_selector,
            "screenshot": request.screenshot,
            "pdf": request.pdf,
            "cache_mode": request.cache_mode,
            "headless": request.headless,
            "base_directory": base_directory,
        },
    }
