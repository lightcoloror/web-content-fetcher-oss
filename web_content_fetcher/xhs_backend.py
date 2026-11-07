from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

from . import xhs_page as _page


def is_xhs_url(url: str):
    """Compatibility facade for the shared XHS page normalizer."""
    return _page.is_xhs_url(url)


def redbook_cli_fetch(url: str, *, chrome_profile: str = "", timeout: int = 60) -> dict[str, Any]:
    executable = os.environ.get("WEB_CONTENT_FETCHER_REDBOOK_CLI") or "redbook"
    command = [executable, "read", url, "--json", "--cookie-source", "chrome"]
    if chrome_profile:
        command.extend(["--chrome-profile", chrome_profile])
    if os.environ.get("WEB_CONTENT_FETCHER_XHS_COOKIE_STRING", "").strip():
        raise RuntimeError(
            "xhs_manual_cookie_string_not_supported: use an authorized Chrome profile or the local redbook configuration"
        )
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"redbook_cli_failed:{completed.returncode}:{message[:300]}")
    text = (completed.stdout or "").strip()
    if not text:
        raise RuntimeError("redbook_cli_empty_output")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"redbook_cli_invalid_json:{text[:300]}") from exc


def extract_initial_state_note(html: str, url: str = ""):
    """Compatibility facade for the shared XHS page normalizer."""
    return _page.extract_initial_state_note(html, url)


def extract_initial_state(html: str):
    """Compatibility facade for the shared XHS page normalizer."""
    return _page.extract_initial_state(html)


def note_to_markdown(note: dict[str, Any], *, source_url: str = "") -> str:
    title = str(_first(note, "title", "displayTitle") or "小红书笔记").strip()
    desc = str(_first(note, "desc", "description", "content") or "").strip()
    user = _first(note, "user", "userInfo") or {}
    nickname = str(_first(user, "nickname", "nickName", "name") or "").strip() if isinstance(user, dict) else ""
    images = collect_note_images(note)
    lines = [f"# {title}", ""]
    if nickname:
        lines.extend([f"作者：{nickname}", ""])
    if source_url:
        lines.extend([f"原链接：{source_url}", ""])
    if desc:
        lines.extend([desc, ""])
    for index, image in enumerate(images, start=1):
        image_url = image.get("url", "")
        if image_url:
            alt = image.get("alt") or f"图片 {index}"
            lines.append(f"![{alt}]({image_url})")
    return "\n".join(lines).strip()


def collect_note_images(note: dict[str, Any]):
    """Compatibility facade for the shared XHS page normalizer."""
    return _page.collect_note_images(note)


def normalize_note(raw: dict[str, Any], *, source_url: str = "", source: str = ""):
    """Compatibility facade for the shared XHS page normalizer."""
    return _page.normalize_note(raw, source_url=source_url, source=source)


def write_raw_tool_result(archive: dict[str, Any] | None, data: dict[str, Any], *, name: str) -> str:
    if not archive or not archive.get("archive_path"):
        return ""
    path = Path(str(archive["archive_path"])) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return str(path)


def _first(value: Any, *keys: str) -> Any:
    if not isinstance(value, dict):
        return None
    for key in keys:
        if key in value and value.get(key) not in (None, ""):
            return value.get(key)
    return None


def _unwrap_reactive(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    if isinstance(value, dict) and "_value" in value:
        return value.get("_value")
    return value


def _note_id_from_url(url: str) -> str:
    match = re.search(r"/(?:explore|discovery/item)/([A-Za-z0-9]+)", url or "")
    return match.group(1) if match else ""
