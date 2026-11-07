from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .archive import write_archive
from .core import now_iso, stable_id


@dataclass
class ZsxqRequest:
    action: str
    group_id: str = ""
    topic_id: str = ""
    query: str = ""
    limit: int = 20
    end_time: str = ""
    timeout_seconds: int = 30
    archive_dir: str = ""
    cli_path: str = "zsxq-cli"
    max_pages: int = 0
    with_detail: bool = False
    with_comments: bool = False


@dataclass
class ZsxqResult:
    action: str
    status: str
    fetched_at: str
    transport: str = "zsxq-cli"
    data: dict[str, Any] = field(default_factory=dict)
    raw_output: str = ""
    archive: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        result = {
            "action": self.action,
            "status": self.status,
            "fetched_at": self.fetched_at,
            "transport": self.transport,
            "data": self.data,
            "archive": self.archive,
            "errors": self.errors,
        }
        if include_raw:
            result["raw_output"] = self.raw_output
        return result


def _command(request: ZsxqRequest) -> list[str]:
    base = [_resolve_cli_path(request.cli_path)]
    if request.action == "auth_status":
        return base + ["auth", "status", "--json"]
    if request.action == "list_groups":
        command = base + ["group", "+list", "--json"]
        if request.limit:
            command += ["--limit", str(request.limit)]
        return command
    if request.action == "list_topics":
        if not request.group_id:
            raise ValueError("group_id is required")
        command = base + ["group", "+topics", "--group-id", request.group_id, "--json"]
        if request.limit:
            command += ["--limit", str(request.limit)]
        if request.end_time:
            command += ["--end-time", request.end_time]
        return command
    if request.action == "search_topics":
        if not request.group_id:
            raise ValueError("group_id is required")
        if not request.query:
            raise ValueError("query is required")
        return base + ["topic", "+search", "--group-id", request.group_id, "--query", request.query, "--json"]
    if request.action == "get_topic":
        if not request.topic_id:
            raise ValueError("topic_id is required")
        return base + ["topic", "+detail", "--topic-id", request.topic_id, "--json"]
    raise ValueError(f"unknown zsxq action: {request.action}")


def _resolve_cli_path(cli_path: str) -> str:
    resolved = shutil.which(cli_path)
    if resolved:
        return resolved
    if not cli_path.lower().endswith((".cmd", ".exe", ".ps1")):
        for suffix in (".cmd", ".exe", ".ps1"):
            resolved = shutil.which(cli_path + suffix)
            if resolved:
                return resolved
    return cli_path


def _parse_json_output(output: str) -> dict[str, Any]:
    text = (output or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _get_path(data: Any, *keys: str) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def topic_payload(data: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        data.get("topic"),
        _get_path(data, "data", "topic"),
        _get_path(data, "data", "topic_detail"),
        _get_path(data, "resp_data", "topic"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return {}


def _plain_text_from_zsxq_content(value: str) -> str:
    def replace_web(match: re.Match[str]) -> str:
        attrs = match.group(1) or ""
        href_match = re.search(r'href="([^"]*)"', attrs)
        title_match = re.search(r'title="([^"]*)"', attrs)
        href = urllib.parse.unquote(html.unescape(href_match.group(1))) if href_match else ""
        title = urllib.parse.unquote(html.unescape(title_match.group(1))) if title_match else href
        return f"[{title}]({href})" if href else title

    text = re.sub(r"<e\b([^>]*)/>", replace_web, value or "")
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def topic_images(topic: dict[str, Any]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for index, item in enumerate(topic.get("images") or [], start=1):
        if not isinstance(item, dict):
            continue
        original = item.get("original") if isinstance(item.get("original"), dict) else {}
        large = item.get("large") if isinstance(item.get("large"), dict) else {}
        thumbnail = item.get("thumbnail") if isinstance(item.get("thumbnail"), dict) else {}
        url = str(original.get("url") or large.get("url") or thumbnail.get("url") or "")
        if not url:
            continue
        images.append(
            {
                "url": url,
                "alt": f"知识星球图片 #{index}",
                "title": "",
                "width": str(original.get("width") or large.get("width") or thumbnail.get("width") or ""),
                "height": str(original.get("height") or large.get("height") or thumbnail.get("height") or ""),
                "source": item.get("source") or "",
                "image_id": item.get("image_id") or "",
                "thumbnail_url": thumbnail.get("url") or "",
                "large_url": large.get("url") or "",
            }
        )
    return images


def topic_to_markdown(topic: dict[str, Any]) -> str:
    title = str(topic.get("title") or "").strip()
    topic_id = str(topic.get("topic_id") or topic.get("topicId") or "").strip()
    owner = topic.get("owner") if isinstance(topic.get("owner"), dict) else {}
    group = topic.get("group") if isinstance(topic.get("group"), dict) else {}
    lines: list[str] = []
    lines.append(f"# {title or topic_id or '知识星球主题'}")
    lines.append("")
    if topic_id:
        lines.append(f"- Topic ID: {topic_id}")
    if group.get("name") or group.get("group_id"):
        lines.append(f"- 星球: {group.get('name') or ''} {group.get('group_id') or ''}".strip())
    if owner.get("name") or owner.get("user_id"):
        lines.append(f"- 作者: {owner.get('name') or ''} {owner.get('user_id') or ''}".strip())
    if topic.get("create_time"):
        lines.append(f"- 创建时间: {topic.get('create_time')}")
    if lines[-1] != "":
        lines.append("")
    content = _plain_text_from_zsxq_content(str(topic.get("content") or ""))
    if content:
        lines.append(content)
        lines.append("")
    images = topic_images(topic)
    if images:
        lines.append("## 图片")
        lines.append("")
        for index, image in enumerate(images, start=1):
            lines.append(f"![知识星球图片 #{index}]({image['url']})")
            if image.get("thumbnail_url") or image.get("large_url"):
                lines.append("")
                lines.append(
                    f"- thumbnail: {image.get('thumbnail_url') or ''}\n"
                    f"- large: {image.get('large_url') or ''}"
                )
            lines.append("")
    comments = topic.get("comments") if isinstance(topic.get("comments"), list) else []
    if comments:
        lines.append("## 评论")
        lines.append("")
        for index, comment in enumerate(comments, start=1):
            if not isinstance(comment, dict):
                continue
            owner = comment.get("owner") if isinstance(comment.get("owner"), dict) else {}
            author = str(owner.get("name") or owner.get("user_id") or "匿名")
            text = _plain_text_from_zsxq_content(str(comment.get("text") or comment.get("content") or ""))
            created = str(comment.get("create_time") or "")
            lines.append(f"### 评论 {index} - {author}")
            lines.append("")
            if created:
                lines.append(f"- 时间: {created}")
                lines.append("")
            if text:
                lines.append(text)
                lines.append("")
    return "\n".join(lines).strip() + "\n"


def _candidate_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        data.get("topics_brief"),
        _get_path(data, "data", "items"),
        _get_path(data, "data", "topics"),
        _get_path(data, "resp_data", "topics"),
        _get_path(data, "resp_data", "topics_brief"),
        data.get("items"),
        data.get("topics"),
        data.get("value"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _topic_id(item: dict[str, Any]) -> str:
    for key in ("topic_id", "topicId", "id"):
        value = item.get(key)
        if value:
            return str(value)
    topic = item.get("topic")
    if isinstance(topic, dict):
        return _topic_id(topic)
    return ""


def _score_candidate(item: dict[str, Any], query: str) -> int:
    text = json.dumps(item, ensure_ascii=False).lower()
    normalized_query = query.lower().strip()
    score = 0
    if normalized_query and normalized_query in text:
        score += 20
    for token in normalized_query.split():
        if token and token in text:
            score += 5
    if _topic_id(item):
        score += 1
    return score


def _best_topic(data: dict[str, Any], query: str) -> tuple[str, dict[str, Any] | None]:
    items = _candidate_items(data)
    scored = [(_score_candidate(item, query), index, item) for index, item in enumerate(items)]
    scored.sort(key=lambda value: (value[0], -value[1]), reverse=True)
    for _score, _index, item in scored:
        topic_id = _topic_id(item)
        if topic_id:
            return topic_id, item
    return "", None


def _next_end_time(data: dict[str, Any]) -> str:
    for value in (
        data.get("next_end_time"),
        _get_path(data, "data", "next_end_time"),
        _get_path(data, "resp_data", "next_end_time"),
    ):
        if value:
            return str(value)
    return ""


def _has_more(data: dict[str, Any], topics: list[dict[str, Any]], next_end_time: str) -> bool:
    for value in (data.get("has_more"), _get_path(data, "data", "has_more"), _get_path(data, "resp_data", "has_more")):
        if value is not None:
            return bool(value)
    return bool(topics and next_end_time)


def _topic_title(topic: dict[str, Any]) -> str:
    title = str(topic.get("title") or "").strip()
    if title:
        return title
    content = _plain_text_from_zsxq_content(str(topic.get("content") or ""))
    return content.splitlines()[0][:80] if content else _topic_id(topic)


def _topic_summary(topic: dict[str, Any], archive: dict[str, Any] | None = None) -> dict[str, Any]:
    owner = topic.get("owner") if isinstance(topic.get("owner"), dict) else {}
    group = topic.get("group") if isinstance(topic.get("group"), dict) else {}
    counts = topic.get("counts") if isinstance(topic.get("counts"), dict) else {}
    return {
        "topic_id": _topic_id(topic),
        "title": _topic_title(topic),
        "type": topic.get("type") or "",
        "create_time": topic.get("create_time") or "",
        "modify_time": topic.get("modify_time") or "",
        "owner_name": owner.get("name") or "",
        "owner_id": owner.get("user_id") or "",
        "group_id": group.get("group_id") or "",
        "group_name": group.get("name") or "",
        "digested": bool(topic.get("digested")),
        "comment_count": counts.get("comments", 0),
        "exported_comment_count": len(topic.get("comments") or []) if isinstance(topic.get("comments"), list) else 0,
        "like_count": counts.get("likes", 0),
        "image_count": len(topic.get("images") or []),
        "file_count": len(topic.get("files") or []),
        "archive_path": (archive or {}).get("archive_path", ""),
        "extracted_text_path": (archive or {}).get("extracted_text_path", ""),
        "structured_path": (archive or {}).get("structured_path", ""),
    }


def _write_text(path: Path, value: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")
    return str(path)


def _collection_root(archive_dir: str | Path, group_id: str, fetched_at: str) -> Path:
    stamp = re.sub(r"[^0-9A-Za-z]+", "", fetched_at)[:14] or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return Path(archive_dir) / f"zsxq_group_{group_id}_{stamp}"


def _render_group_index(summary: dict[str, Any], topics: list[dict[str, Any]]) -> str:
    lines = [
        f"# 知识星球导出：{summary.get('group_name') or summary.get('group_id') or ''}",
        "",
        f"- Group ID: {summary.get('group_id') or ''}",
        f"- 导出时间: {summary.get('fetched_at') or ''}",
        f"- 主题数: {summary.get('topic_count') or 0}",
        f"- 页数: {summary.get('page_count') or 0}",
        f"- 完整分页结束: {summary.get('completed')}",
        f"- 下一页游标: {summary.get('next_end_time') or ''}",
        "",
        "## 主题索引",
        "",
    ]
    for item in topics:
        title = str(item.get("title") or item.get("topic_id") or "untitled").replace("\n", " ").strip()
        path = item.get("extracted_text_path") or item.get("archive_path") or ""
        lines.append(f"- {item.get('create_time') or ''} | {title} | 作者: {item.get('owner_name') or ''} | 图片: {item.get('image_count') or 0} | 附件: {item.get('file_count') or 0}")
        if path:
            lines.append(f"  - {path}")
    return "\n".join(lines).strip() + "\n"


def _run_zsxq_api_call(cli_path: str, method: str, params: dict[str, Any], timeout_seconds: int) -> tuple[dict[str, Any], list[str]]:
    try:
        completed = subprocess.run(
            [
                _resolve_cli_path(cli_path),
                "api",
                "call",
                method,
                "--params",
                json.dumps(params, ensure_ascii=False, separators=(",", ":")),
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except Exception as exc:
        return {}, [f"{method}:{type(exc).__name__}:{str(exc)[:300]}"]
    raw = (completed.stdout or completed.stderr or "").strip()
    errors: list[str] = []
    data: dict[str, Any] = {}
    if raw:
        try:
            data = _parse_json_output(raw)
        except Exception as exc:
            errors.append(f"{method}:json:{type(exc).__name__}:{str(exc)[:200]}")
    if completed.returncode != 0:
        errors.append(f"{method}:zsxq-cli exited with code {completed.returncode}")
    return data, errors


def _comments_from_data(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        _get_path(data, "result", "comments"),
        _get_path(data, "data", "comments"),
        _get_path(data, "resp_data", "comments"),
        data.get("comments"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _comment_next_index(data: dict[str, Any]) -> str:
    for value in (
        _get_path(data, "result", "index"),
        _get_path(data, "data", "index"),
        _get_path(data, "resp_data", "index"),
        data.get("index"),
    ):
        if value:
            return str(value)
    return ""


def _comment_has_more(data: dict[str, Any], comments: list[dict[str, Any]], next_index: str) -> bool:
    for value in (
        _get_path(data, "result", "has_more"),
        _get_path(data, "data", "has_more"),
        _get_path(data, "resp_data", "has_more"),
        data.get("has_more"),
    ):
        if value is not None:
            return bool(value)
    return bool(comments and next_index)


def _fetch_topic_comments(topic_id: str, request: ZsxqRequest) -> tuple[list[dict[str, Any]], list[str], bool]:
    comments: list[dict[str, Any]] = []
    errors: list[str] = []
    index = ""
    seen_indexes: set[str] = set()
    completed = True
    while True:
        params: dict[str, Any] = {"topic_id": topic_id, "limit": 30}
        if index:
            params["index"] = index
        data, page_errors = _run_zsxq_api_call(request.cli_path, "get_topic_comments", params, request.timeout_seconds)
        errors.extend(page_errors)
        if page_errors:
            completed = False
            break
        page_comments = _comments_from_data(data)
        comments.extend(page_comments)
        next_index = _comment_next_index(data)
        has_more = _comment_has_more(data, page_comments, next_index)
        if not has_more:
            break
        if not next_index or next_index in seen_indexes:
            completed = False
            break
        seen_indexes.add(next_index)
        index = next_index
    return comments, errors, completed


def _export_group(request: ZsxqRequest) -> ZsxqResult:
    fetched_at = now_iso()
    if not request.group_id:
        return ZsxqResult(action=request.action, status="failed", fetched_at=fetched_at, errors=["group_id is required"])

    all_topics: list[dict[str, Any]] = []
    topic_summaries: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_topic_ids: set[str] = set()
    seen_page_cursors: set[str] = set()
    end_time = request.end_time
    has_more = True
    page_count = 0
    group_name = ""
    collection_root = _collection_root(request.archive_dir, request.group_id, fetched_at) if request.archive_dir else None

    while has_more:
        if request.max_pages and page_count >= request.max_pages:
            break
        page = _run_zsxq_command(
            ZsxqRequest(
                action="list_topics",
                group_id=request.group_id,
                limit=request.limit,
                end_time=end_time,
                timeout_seconds=request.timeout_seconds,
                cli_path=request.cli_path,
            )
        )
        page_count += 1
        errors.extend(page.errors)
        if page.status != "ok":
            break
        topics = _candidate_items(page.data)
        next_cursor = _next_end_time(page.data)
        has_more = _has_more(page.data, topics, next_cursor)
        if next_cursor and next_cursor in seen_page_cursors:
            errors.append(f"pagination:repeated_end_time:{next_cursor}")
            has_more = True
            break
        if next_cursor:
            seen_page_cursors.add(next_cursor)
        end_time = next_cursor
        if not topics:
            break

        for topic in topics:
            topic_id = _topic_id(topic)
            if topic_id and topic_id in seen_topic_ids:
                continue
            if topic_id:
                seen_topic_ids.add(topic_id)
            group = topic.get("group") if isinstance(topic.get("group"), dict) else {}
            if group.get("name"):
                group_name = str(group.get("name") or "")

            final_topic = topic
            if request.with_detail and topic_id:
                detail = _run_zsxq_command(
                    ZsxqRequest(action="get_topic", topic_id=topic_id, timeout_seconds=request.timeout_seconds, cli_path=request.cli_path)
                )
                errors.extend(detail.errors)
                detail_topic = topic_payload(detail.data)
                if detail.status == "ok" and detail_topic:
                    final_topic = detail_topic

            if request.with_comments and topic_id:
                comments, comment_errors, comments_completed = _fetch_topic_comments(topic_id, request)
                errors.extend(comment_errors)
                final_topic = dict(final_topic)
                final_topic["comments"] = comments
                final_topic["comments_export"] = {
                    "transport": "zsxq-cli api call get_topic_comments",
                    "completed": comments_completed,
                    "count": len(comments),
                }

            archive = None
            if collection_root:
                raw_snapshot = json.dumps(final_topic, ensure_ascii=False, indent=2)
                archive = write_archive(
                    collection_root / "topics",
                    stable_id("zsxq", request.group_id, topic_id or len(all_topics), raw_snapshot),
                    url=f"zsxq://topic/{topic_id}" if topic_id else f"zsxq://group/{request.group_id}",
                    title=_topic_title(final_topic),
                    raw_snapshot=raw_snapshot,
                    extracted_text=topic_to_markdown(final_topic),
                    metadata={
                        "action": "export_group_topic",
                        "status": "ok",
                        "transport": "zsxq-cli",
                        "fetched_at": fetched_at,
                        "group_id": request.group_id,
                        "topic": final_topic,
                    },
                )
            all_topics.append(final_topic)
            topic_summaries.append(_topic_summary(final_topic, archive))

        if not has_more:
            break

    completed = not has_more
    export_status = "ok" if completed and not errors else "partial" if topic_summaries else "failed"
    summary = {
        "action": request.action,
        "status": export_status,
        "transport": "zsxq-cli",
        "fetched_at": fetched_at,
        "group_id": request.group_id,
        "group_name": group_name,
        "topic_count": len(topic_summaries),
        "page_count": page_count,
        "completed": completed,
        "next_end_time": end_time,
        "max_pages": request.max_pages,
        "with_detail": request.with_detail,
        "with_comments": request.with_comments,
        "collection_path": str(collection_root or ""),
    }
    archive = None
    if collection_root:
        summary_path = _write_text(collection_root / "summary.json", json.dumps({**summary, "topics": topic_summaries, "errors": errors}, ensure_ascii=False, indent=2))
        jsonl_path = collection_root / "topics.jsonl"
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        jsonl_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in all_topics) + ("\n" if all_topics else ""), encoding="utf-8", newline="\n")
        index_path = _write_text(collection_root / "index.md", _render_group_index(summary, topic_summaries))
        archive = {
            "archive_path": str(collection_root),
            "summary_path": summary_path,
            "topics_jsonl_path": str(jsonl_path),
            "index_markdown_path": index_path,
            "topic_archive_root": str(collection_root / "topics"),
        }
        summary.update(archive)

    return ZsxqResult(
        action=request.action,
        status=export_status,
        fetched_at=fetched_at,
        data={**summary, "topics": topic_summaries},
        raw_output=json.dumps({"summary": summary, "topics": all_topics}, ensure_ascii=False, indent=2),
        archive=archive,
        errors=errors,
    )


def _run_zsxq_command(request: ZsxqRequest) -> ZsxqResult:
    fetched_at = now_iso()
    try:
        command = _command(request)
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=request.timeout_seconds,
        )
    except Exception as exc:
        return ZsxqResult(
            action=request.action,
            status="failed",
            fetched_at=fetched_at,
            errors=[f"{type(exc).__name__}:{str(exc)[:300]}"],
        )

    raw_output = (completed.stdout or "").strip()
    raw_error = (completed.stderr or "").strip()
    parse_source = raw_output or raw_error
    errors: list[str] = []
    data: dict[str, Any] = {}
    status = "ok" if completed.returncode == 0 else "failed"
    if parse_source:
        try:
            data = _parse_json_output(parse_source)
            if data.get("ok") is False:
                status = "failed"
        except Exception as exc:
            status = "failed"
            errors.append(f"json:{type(exc).__name__}:{str(exc)[:200]}")
    if raw_error and not data:
        errors.append(raw_error[:500])
    if completed.returncode != 0 and not errors:
        errors.append(f"zsxq-cli exited with code {completed.returncode}")

    return ZsxqResult(
        action=request.action,
        status=status,
        fetched_at=fetched_at,
        data=data,
        raw_output=raw_output,
        errors=errors,
    )


def _fetch_topic_by_query(request: ZsxqRequest) -> ZsxqResult:
    fetched_at = now_iso()
    search = run_zsxq(
        ZsxqRequest(
            action="search_topics",
            group_id=request.group_id,
            query=request.query,
            limit=request.limit,
            timeout_seconds=request.timeout_seconds,
            cli_path=request.cli_path,
        )
    )
    if search.status != "ok":
        search.action = request.action
        return search

    topic_id, selected = _best_topic(search.data, request.query)
    if not topic_id:
        return ZsxqResult(
            action=request.action,
            status="failed",
            fetched_at=fetched_at,
            data={"search": search.data, "selected_topic": None},
            raw_output=search.raw_output,
            errors=["no topic_id found in search results"],
        )

    detail = run_zsxq(
        ZsxqRequest(
            action="get_topic",
            topic_id=topic_id,
            timeout_seconds=request.timeout_seconds,
            cli_path=request.cli_path,
        )
    )
    combined = {
        "ok": detail.status == "ok",
        "group_id": request.group_id,
        "query": request.query,
        "selected_topic_id": topic_id,
        "selected_topic": selected,
        "search": search.data,
        "topic": detail.data,
    }
    result = ZsxqResult(
        action=request.action,
        status=detail.status,
        fetched_at=fetched_at,
        data=combined,
        raw_output=json.dumps({"search": search.data, "topic": detail.data}, ensure_ascii=False, indent=2),
        errors=[*search.errors, *detail.errors],
    )
    if request.archive_dir and result.raw_output:
        try:
            detail_topic = topic_payload(detail.data)
            extracted_text = topic_to_markdown(detail_topic) if detail_topic else json.dumps(combined, ensure_ascii=False, indent=2)
            item_id = stable_id("zsxq", request.group_id, request.query, topic_id, result.raw_output)
            result.archive = write_archive(
                Path(request.archive_dir),
                item_id,
                url=f"zsxq://topic/{topic_id}",
                title=str(detail_topic.get("title") or f"zsxq:fetch_topic_by_query:{topic_id}") if detail_topic else f"zsxq:fetch_topic_by_query:{topic_id}",
                raw_snapshot=result.raw_output,
                extracted_text=extracted_text,
                metadata=result.to_dict(include_raw=False),
            )
        except Exception as exc:
            result.errors.append(f"archive:{type(exc).__name__}:{str(exc)[:200]}")
    return result


def run_zsxq(request: ZsxqRequest) -> ZsxqResult:
    if request.action == "export_group":
        return _export_group(request)
    if request.action == "fetch_topic_by_query":
        return _fetch_topic_by_query(request)

    result = _run_zsxq_command(request)
    if request.archive_dir and result.raw_output:
        try:
            topic = topic_payload(result.data)
            extracted_text = topic_to_markdown(topic) if topic else json.dumps(result.data, ensure_ascii=False, indent=2)
            title = str(topic.get("title") or f"zsxq:{request.action}") if topic else f"zsxq:{request.action}"
            topic_id = str(topic.get("topic_id") or request.topic_id or "")
            item_id = stable_id("zsxq", request.action, request.group_id, topic_id or request.topic_id, request.query, result.raw_output)
            result.archive = write_archive(
                Path(request.archive_dir),
                item_id,
                url=f"zsxq://topic/{topic_id}" if topic_id else f"zsxq://{request.action}",
                title=title,
                raw_snapshot=result.raw_output,
                extracted_text=extracted_text,
                metadata=result.to_dict(include_raw=False),
            )
        except Exception as exc:
            result.errors.append(f"archive:{type(exc).__name__}:{str(exc)[:200]}")
    return result
