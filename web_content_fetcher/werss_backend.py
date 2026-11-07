from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


@dataclass
class WerssRequest:
    url: str
    article_id: str = ""
    timeout_seconds: int = 30
    base_url: str = ""
    api_key_env: str = "WERSS_API_KEY"


def run_werss(request: WerssRequest) -> dict[str, Any]:
    """Read one existing local WeRSS article; never start collection jobs."""
    article_id = request.article_id.strip() or os.environ.get("WEB_CONTENT_FETCHER_WERSS_ARTICLE_ID", "").strip()
    if not article_id:
        raise ValueError("werss_article_id_required")
    configured_base_url = request.base_url.strip() or os.environ.get("WEB_CONTENT_FETCHER_WERSS_URL", "").strip() or "http://127.0.0.1:8001"
    parsed = urlsplit(configured_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid_werss_base_url")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("werss_base_url_userinfo_not_allowed")
    if parsed.query or parsed.fragment:
        raise ValueError("werss_base_url_query_or_fragment_not_allowed")
    try:
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    except ValueError as exc:
        raise ValueError("invalid_werss_base_url") from exc
    base_url = f"{parsed.scheme}://{netloc}{parsed.path.rstrip('/')}"
    api_key_env = request.api_key_env.strip() or "WERSS_API_KEY"
    headers = {"Accept": "application/json"}
    api_key = os.environ.get(api_key_env, "").strip()
    if api_key:
        headers["X-API-Key"] = api_key
    endpoint = f"{base_url}/api/v1/wx/articles/{quote(article_id, safe='')}"
    with urlopen(Request(endpoint, headers=headers), timeout=max(1, int(request.timeout_seconds or 30))) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
        status_code = int(getattr(response, "status", 0) or 0)
    if isinstance(payload, dict) and payload.get("code") not in {None, 0}:
        raise RuntimeError(str(payload.get("message") or "werss_request_failed")[:300])
    article = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(article, dict):
        raise RuntimeError("werss_invalid_article_response")
    html = str(article.get("content") or "")
    if not html.strip():
        raise RuntimeError("werss_article_has_no_content")
    return {
        "html": html,
        "url": str(article.get("url") or request.url),
        "article": {key: value for key, value in article.items() if key != "content"},
        "status_code": status_code,
        "config": {"base_url": base_url, "article_id": article_id, "api_key_env": api_key_env, "api_key_present": bool(api_key)},
    }