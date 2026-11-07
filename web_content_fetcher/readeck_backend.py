from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urljoin

from .archive import archive_child_path, enrich_archive, sha256_file, write_archive


@dataclass(frozen=True)
class ReadeckRequest:
    url: str
    html: str = ""
    title: str = ""
    labels: list[str] = field(default_factory=list)
    base_url: str = ""
    token_env: str = "READECK_TOKEN"
    archive_dir: str = ""
    timeout_seconds: int = 30
    wait_seconds: int = 0


@dataclass
class ReadeckResult:
    status: str
    url: str
    base_url: str
    bookmark_id: str = ""
    article_html: str = ""
    article_markdown: str = ""
    response: dict[str, Any] = field(default_factory=dict)
    archive: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _base_url(value: str) -> str:
    return (value or os.getenv("READECK_BASE_URL", "")).strip().rstrip("/")


def _api_request(base_url: str, token: str, method: str, path: str, *, payload: dict[str, Any] | None = None, timeout_seconds: int = 30) -> tuple[int, dict[str, str], bytes]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(urljoin(base_url + "/", "api/" + path.lstrip("/")), data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=max(1, timeout_seconds)) as response:
        return int(response.status), {key.lower(): value for key, value in response.headers.items()}, response.read()


def run_readeck(request: ReadeckRequest) -> ReadeckResult:
    base_url = _base_url(request.base_url)
    result = ReadeckResult(status="failed", url=request.url, base_url=base_url)
    if not base_url:
        result.errors.append("readeck_base_url_missing: set READECK_BASE_URL or pass base_url")
        return result
    token = os.getenv(request.token_env, "")
    if not token:
        result.errors.append(f"readeck_token_missing_env:{request.token_env}")
        return result

    payload: dict[str, Any] = {"url": request.url}
    if request.html:
        payload["html"] = request.html
    if request.title:
        payload["title"] = request.title
    labels = [item.strip() for item in request.labels if item and item.strip()]
    if labels:
        payload["labels"] = labels
    try:
        status, headers, body = _api_request(base_url, token, "POST", "bookmarks", payload=payload, timeout_seconds=request.timeout_seconds)
        result.bookmark_id = headers.get("bookmark-id", "")
        if body:
            try:
                parsed = json.loads(body.decode("utf-8", errors="replace"))
                if isinstance(parsed, dict):
                    result.response = parsed
            except json.JSONDecodeError:
                result.response = {"body_preview": body.decode("utf-8", errors="replace")[:500]}
        result.status = "accepted" if status in {200, 201, 202} else "failed"
        if result.status == "failed":
            result.errors.append(f"readeck_create_status:{status}")
    except urllib.error.HTTPError as exc:
        result.errors.append(f"readeck_http:{exc.code}:{exc.reason}")
    except Exception as exc:
        result.errors.append(f"readeck:{type(exc).__name__}:{str(exc)[:300]}")

    if result.status == "accepted" and result.bookmark_id and request.wait_seconds > 0:
        time.sleep(min(request.wait_seconds, 30))
        for suffix, attribute in (("article.md", "article_markdown"), ("article", "article_html")):
            try:
                _status, _headers, body = _api_request(base_url, token, "GET", f"bookmarks/{result.bookmark_id}/{suffix}", timeout_seconds=request.timeout_seconds)
                setattr(result, attribute, body.decode("utf-8", errors="replace"))
            except Exception as exc:
                result.errors.append(f"readeck_export_{suffix}:{type(exc).__name__}:{str(exc)[:160]}")

    if request.archive_dir:
        metadata = {
            "status": result.status,
            "requested_url": request.url,
            "final_url": request.url,
            "title": request.title or f"Readeck: {request.url}",
            "transport": "readeck",
            "errors": result.errors,
            "readeck": {
                "base_url": base_url,
                "bookmark_id": result.bookmark_id,
                "token_env": request.token_env,
                "submitted_html": bool(request.html),
                "labels": labels,
                "article_markdown_available": bool(result.article_markdown),
                "article_html_available": bool(result.article_html),
            },
        }
        result.archive = write_archive(
            request.archive_dir,
            f"readeck-{result.bookmark_id or 'submission'}",
            url=request.url,
            title=metadata["title"],
            raw_snapshot=result.article_html,
            extracted_text=result.article_markdown or "# Readeck submission\n\n" + json.dumps(metadata["readeck"], ensure_ascii=False, indent=2),
            metadata=metadata,
        )
        artifacts: list[dict[str, Any]] = []
        if result.article_markdown:
            markdown_path = archive_child_path(result.archive, "readeck", "article.md")
            assert markdown_path is not None
            markdown_path.write_text(result.article_markdown, encoding="utf-8", newline="\n")
            artifacts.append(
                {
                    "role": "readeck_article_markdown",
                    "path": str(markdown_path),
                    "media_type": "text/markdown",
                    "source": "readeck",
                    "label": "Readeck article Markdown",
                    "description": "Readeck normalized article export for agent reading",
                    "sha256": sha256_file(markdown_path),
                    "bytes": markdown_path.stat().st_size,
                    "view": "agent_readable",
                }
            )
        if result.article_html:
            html_path = archive_child_path(result.archive, "readeck", "article.html")
            assert html_path is not None
            html_path.write_text(result.article_html, encoding="utf-8", newline="\n")
            artifacts.append(
                {
                    "role": "readeck_article_html",
                    "path": str(html_path),
                    "media_type": "text/html",
                    "source": "readeck",
                    "label": "Readeck article HTML",
                    "description": "Readeck normalized article export for human reading",
                    "sha256": sha256_file(html_path),
                    "bytes": html_path.stat().st_size,
                    "view": "human_readable",
                }
            )
        if not artifacts:
            artifacts.append(
                {
                    "role": "readeck_submission_receipt",
                    "path": str(result.archive.get("extracted_text_path") or ""),
                    "media_type": "text/markdown",
                    "source": "readeck",
                    "label": "Readeck submission receipt",
                    "description": "Readeck accepted the URL but article exports are not available yet; rerun with --wait after it finishes processing.",
                    "view": "agent_readable",
                }
            )
        result.archive["archive_index"] = enrich_archive(
            result.archive,
            artifacts=artifacts,
            title=metadata["title"],
            url=request.url,
            transport="readeck",
        )
    return result
