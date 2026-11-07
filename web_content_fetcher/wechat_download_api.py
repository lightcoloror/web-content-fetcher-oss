from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .config import wechat_download_api_base_url


DEFAULT_API_URL_ENV = "WEB_CONTENT_FETCHER_WECHAT_API_URL"
LOCAL_HTTP_HOSTS = {"127.0.0.1", "localhost", "::1", "host.docker.internal"}
MAX_RESPONSE_BYTES = 12_000_000


@dataclass(frozen=True)
class WechatDownloadApiConfig:
    base_url: str = ""
    timeout_seconds: int = 30


def normalize_wechat_article_url(url: str) -> str:
    """Return a canonical public WeChat article URL or an empty string."""
    try:
        parsed = urlsplit(str(url or "").strip())
        if parsed.scheme not in {"http", "https"}:
            return ""
        if parsed.username is not None or parsed.password is not None or parsed.port is not None:
            return ""
        if (parsed.hostname or "").lower() != "mp.weixin.qq.com":
            return ""
        if parsed.path != "/s" and not parsed.path.startswith("/s/"):
            return ""
    except ValueError:
        return ""
    return urlunsplit(("https", "mp.weixin.qq.com", parsed.path, parsed.query, ""))


def normalize_wechat_api_base_url(value: str = "") -> str:
    configured = (
        str(value or "").strip()
        or os.environ.get(DEFAULT_API_URL_ENV, "").strip()
        or wechat_download_api_base_url()
    )
    parsed = urlsplit(configured)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid_wechat_download_api_base_url")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("wechat_download_api_base_url_userinfo_not_allowed")
    if parsed.query or parsed.fragment:
        raise ValueError("wechat_download_api_base_url_query_or_fragment_not_allowed")
    hostname = parsed.hostname.lower()
    if parsed.scheme == "http" and hostname not in LOCAL_HTTP_HOSTS:
        raise ValueError("wechat_download_api_plain_http_requires_local_host")
    try:
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    except ValueError as exc:
        raise ValueError("invalid_wechat_download_api_base_url") from exc
    return f"{parsed.scheme}://{netloc}{parsed.path.rstrip('/')}"


def _safe_error(value: Any) -> str:
    text = str(value or "wechat_download_api_request_failed")
    lowered = text.lower()
    if any(key in lowered for key in ("cookie=", "token=", "authorization:", "bearer ")):
        return "wechat_download_api_sensitive_error_redacted"
    return text[:300]


def _request_json(
    config: WechatDownloadApiConfig,
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_url = normalize_wechat_api_base_url(config.base_url)
    endpoint = f"{base_url}{path}"
    if query:
        endpoint = f"{endpoint}?{urlencode(query)}"
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(endpoint, data=payload, headers=headers, method=method.upper())
    with urlopen(request, timeout=max(1, int(config.timeout_seconds))) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError("wechat_download_api_response_too_large")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("wechat_download_api_invalid_json") from exc
    if not isinstance(result, dict):
        raise RuntimeError("wechat_download_api_invalid_response")
    if result.get("success") is not True:
        raise RuntimeError(_safe_error(result.get("error")))
    data = result.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("wechat_download_api_missing_data")
    return data


def search_wechat_accounts(query: str, *, base_url: str = "", timeout_seconds: int = 30) -> list[dict[str, Any]]:
    query = str(query or "").strip()
    if not query:
        raise ValueError("wechat_account_query_required")
    data = _request_json(
        WechatDownloadApiConfig(base_url=base_url, timeout_seconds=timeout_seconds),
        "GET",
        "/api/public/searchbiz",
        query={"query": query},
    )
    accounts: list[dict[str, Any]] = []
    for item in data.get("list") or []:
        if not isinstance(item, dict) or not str(item.get("fakeid") or "").strip():
            continue
        accounts.append(
            {
                "fakeid": str(item.get("fakeid") or "").strip(),
                "nickname": str(item.get("nickname") or "").strip(),
                "alias": str(item.get("alias") or "").strip(),
                "service_type": int(item.get("service_type") or 0),
            }
        )
    return accounts


def search_wechat_articles(
    fakeid: str,
    *,
    query: str = "",
    begin: int = 0,
    count: int = 20,
    account: dict[str, Any] | None = None,
    base_url: str = "",
    timeout_seconds: int = 30,
) -> list[dict[str, Any]]:
    fakeid = str(fakeid or "").strip()
    if not fakeid:
        raise ValueError("wechat_fakeid_required")
    article_query = str(query or "").strip()
    path = "/api/public/articles/search" if article_query else "/api/public/articles"
    params: dict[str, Any] = {
        "fakeid": fakeid,
        "begin": max(0, int(begin)),
        "count": max(1, min(int(count), 100)),
    }
    if article_query:
        params["query"] = article_query
    data = _request_json(
        WechatDownloadApiConfig(base_url=base_url, timeout_seconds=timeout_seconds),
        "GET",
        path,
        query=params,
    )
    account = account or {}
    articles: list[dict[str, Any]] = []
    for item in data.get("articles") or []:
        if not isinstance(item, dict):
            continue
        url = normalize_wechat_article_url(str(item.get("link") or ""))
        if not url:
            continue
        articles.append(
            {
                "url": url,
                "title": str(item.get("title") or "").strip(),
                "author": str(item.get("author") or "").strip(),
                "publish_time": str(item.get("update_time") or item.get("create_time") or ""),
                "provider_source": "wechat-download-api",
                "aid": str(item.get("aid") or "").strip(),
                "digest": str(item.get("digest") or "").strip(),
                "account_fakeid": fakeid,
                "account_name": str(account.get("nickname") or "").strip(),
                "account_alias": str(account.get("alias") or "").strip(),
            }
        )
    return articles


def fetch_wechat_article(url: str, *, base_url: str = "", timeout_seconds: int = 60) -> dict[str, Any]:
    normalized_url = normalize_wechat_article_url(url)
    if not normalized_url:
        raise ValueError("wechat_article_url_required")
    data = _request_json(
        WechatDownloadApiConfig(base_url=base_url, timeout_seconds=timeout_seconds),
        "POST",
        "/api/article",
        body={"url": normalized_url},
    )
    content = str(data.get("content") or "")
    if not content.strip():
        raise RuntimeError("wechat_download_api_article_has_no_content")
    return {
        "url": normalized_url,
        "title": str(data.get("title") or "").strip(),
        "content": content,
        "plain_content": str(data.get("plain_content") or ""),
        "images": [str(item) for item in (data.get("images") or []) if str(item).strip()],
        "author": str(data.get("author") or "").strip(),
        "publish_time": int(data.get("publish_time") or 0),
        "publish_time_str": str(data.get("publish_time_str") or "").strip(),
        "provider": "wechat-download-api",
        "api_base_url": normalize_wechat_api_base_url(base_url),
    }
