from __future__ import annotations

import base64
import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any


DEFAULT_FIRECRAWL_API_URL = "https://api.firecrawl.dev/v1/scrape"


@dataclass
class FirecrawlRequest:
    url: str
    timeout_seconds: int = 30
    api_url: str = ""
    api_key_env: str = "FIRECRAWL_API_KEY"
    formats: list[str] = field(default_factory=lambda: ["markdown", "html", "links"])
    only_main_content: bool = True
    wait_for: int = 0


def _decode_maybe_base64(value: Any) -> bytes:
    if not value:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        payload = value.split(",", 1)[-1] if value.startswith("data:") else value
        try:
            return base64.b64decode(payload, validate=False)
        except Exception:
            return b""
    return b""


def _normalize_formats(formats: list[str] | None) -> list[str]:
    allowed = {"markdown", "html", "rawHtml", "links", "screenshot"}
    values = []
    for item in formats or []:
        value = str(item or "").strip()
        if value in allowed and value not in values:
            values.append(value)
    return values or ["markdown", "html", "links"]


def run_firecrawl(request: FirecrawlRequest) -> dict[str, Any]:
    api_url = request.api_url or os.getenv("FIRECRAWL_API_URL") or DEFAULT_FIRECRAWL_API_URL
    key_env = request.api_key_env or "FIRECRAWL_API_KEY"
    api_key = os.getenv(key_env) or ""
    if not api_key:
        raise RuntimeError(f"missing_firecrawl_api_key_env:{key_env}")
    formats = _normalize_formats(request.formats)
    payload: dict[str, Any] = {
        "url": request.url,
        "formats": formats,
        "onlyMainContent": bool(request.only_main_content),
    }
    if request.wait_for:
        payload["waitFor"] = int(request.wait_for)
    raw_payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=raw_payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=request.timeout_seconds) as resp:
        raw = resp.read()
        status_code = getattr(resp, "status", 0) or 0
    body = json.loads(raw.decode("utf-8", errors="replace"))
    data = body.get("data") if isinstance(body, dict) else {}
    if not isinstance(data, dict):
        data = body if isinstance(body, dict) else {}
    success = bool(body.get("success", True)) if isinstance(body, dict) else True
    if not success:
        raise RuntimeError(str(body.get("error") or body.get("message") or "firecrawl_failed")[:300])
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    final_url = str(metadata.get("sourceURL") or metadata.get("url") or data.get("url") or request.url)
    markdown = str(data.get("markdown") or "")
    html = str(data.get("html") or data.get("rawHtml") or "")
    links = data.get("links") if isinstance(data.get("links"), list) else []
    screenshot = data.get("screenshot") or ""
    return {
        "success": True,
        "status_code": status_code,
        "url": final_url,
        "markdown": markdown,
        "html": html,
        "links": links,
        "metadata": metadata,
        "screenshot_bytes": _decode_maybe_base64(screenshot),
        "raw_response": {k: v for k, v in body.items() if k != "data"},
        "config": {
            "api_url": api_url,
            "api_key_env": key_env,
            "formats": formats,
            "only_main_content": bool(request.only_main_content),
            "wait_for": int(request.wait_for or 0),
        },
    }
