"""Offline XHS page normalization shared by WCF and browsing collectors.

Mobile layout and fileId/stream handling informed by hope0719/bagaoji.
No requests, browser actions, cookie access, downloads or ASR are performed.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DOMAINS = ("xiaohongshu.com", "rednote.com", "xhslink.com", "xhslink.cn", "xhsurl.com", "xhsurl.cn")

# Check the original string before strip/urlparse can discard control characters.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# Diagnostic reason code. Stable, machine-readable, and carries no URL content.
MEDIA_URL_SKIPPED_CONTROL_CHAR = "media_url_skipped:control_char"
MEDIA_URL_SKIPPED_INVALID_URL = "media_url_skipped:invalid_url"


def _url_and_reason(value: Any) -> tuple[str, str]:
    """Validate one candidate media URL and report why it was rejected.

    Single source of truth for both collection and diagnostics, so the recorded
    reason can never disagree with the accepted/rejected outcome:

    * non-string or empty values -> ``("", "")``: silently skipped
      (a dict is not a control character);
    * a control character (C0/C1/DEL) anywhere in the original string -> rejected
      with ``MEDIA_URL_SKIPPED_CONTROL_CHAR``;
    * outer plain spaces are trimmed; internal whitespace or malformed URLs are
      rejected without letting one bad media candidate abort the whole note.
    """
    if not isinstance(value, str):
        return "", ""
    if _CONTROL_CHARS.search(value):
        return "", MEDIA_URL_SKIPPED_CONTROL_CHAR
    value = value.strip(' ')
    if not value:
        return "", ""
    if any(char.isspace() for char in value) or "\\" in value:
        return "", MEDIA_URL_SKIPPED_INVALID_URL
    try:
        parsed = urlparse(value)
        if (parsed.scheme in ("http", "https") and parsed.hostname
                and parsed.username is None and parsed.password is None):
            parsed.port  # Validate a supplied port without normalizing the URL.
            return value, ""
    except ValueError:
        pass
    return "", MEDIA_URL_SKIPPED_INVALID_URL


def _url(value: Any) -> str:
    url, _reason = _url_and_reason(value)
    return url


def is_xhs_url(url: str) -> bool:
    validated = _url(url)
    if not validated:
        return False
    p = urlparse(validated)
    host = (p.hostname or "").lower()
    return p.scheme in ("http", "https") and any(host == d or host.endswith("." + d) for d in DOMAINS)


def extract_initial_state(html: str) -> dict[str, Any]:
    # Tokenize quoted strings separately: never replace words inside user text.
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*", html or "")
    if not match:
        return {}
    raw = (html[match.end():]).lstrip()
    tokens = r'"(?:\\.|[^"\\])*"|\b(?:undefined|NaN)\b'
    raw = re.sub(tokens, lambda m: m[0] if m[0].startswith('"') else "null", raw)
    try:
        value, _ = json.JSONDecoder().raw_decode(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, RecursionError):
        return {}


def extract_initial_state_note(html: str, url: str = "") -> dict[str, Any]:
    state = extract_initial_state(html)
    match = re.search(r"/(?:explore|discovery/item|item)/([A-Za-z0-9]+)", url)
    wanted = match[1] if match else ""
    candidates = []
    mobile = state.get("noteData") or {}
    if isinstance(mobile, dict):
        data = mobile.get("data") or {}
        if isinstance(data, dict) and isinstance(data.get("noteData"), dict):
            candidates.append(data["noteData"])
    root = state.get("note") or {}
    detail = root.get("noteDetailMap") or {} if isinstance(root, dict) else {}
    if isinstance(detail, dict):
        detail = detail.get("value", detail.get("_value", detail))
    if isinstance(detail, dict):
        for key, value in detail.items():
            if isinstance(value, dict):
                note = value.get("note", value)
                if isinstance(note, dict):
                    candidates.append({**note, "noteId": note.get("noteId") or note.get("note_id") or key})
    if wanted:
        return next((n for n in candidates if str(n.get("noteId") or n.get("note_id") or "") == wanted), {})
    return candidates[0] if len(candidates) == 1 else {}


def _list(value: Any) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


def collect_note_images(note: dict, *, diagnostics: list | None = None) -> list[dict]:
    """Collect one entry per picture from an XHS note payload.

    ``diagnostics`` is an optional out-parameter: when a list is supplied, a
    ``"media_url_skipped:control_char"`` reason code is appended once per skipped
    media reference. It is deliberately additive and keyword-only, so every existing
    caller keeps the previous signature and return type. Only the reason code is
    recorded -- never the rejected URL, which may carry a short-lived token.
    """
    images, seen = [], set()
    for item in _list(note.get("imageList") or note.get("image_list") or note.get("images")):
        item = {"url": item} if isinstance(item, str) else item
        if not isinstance(item, dict):
            continue
        fid = item.get("fileId") or ""
        if not isinstance(fid, str):
            fid = ""
        if _CONTROL_CHARS.search(fid):
            if diagnostics is not None:
                diagnostics.append(MEDIA_URL_SKIPPED_CONTROL_CHAR)
            fid = ""
        fid = fid.strip(' ').lstrip("/")
        candidates = [item.get(k) for k in ("urlDefault", "url_default", "url")]
        candidates += [i.get("url") for i in _list(item.get("infoList") or item.get("info_list")) if isinstance(i, dict)]
        if fid and not re.search(r"[\s?#:]", fid) and ".." not in fid.split("/"):
            candidates.append("https://sns-img-hw.xhscdn.com/" + fid)
        # A rejected candidate yields "" from _url and is simply not collected, so a
        # good alternative of the same picture still wins and a bad alternative never
        # displaces a good primary. The diagnostic reason comes from the same
        # validation pass that decides acceptance, so it cannot disagree with the
        # published image list.
        urls, reasons = [], []
        for candidate in candidates:
            url, reason = _url_and_reason(candidate)
            if url and url not in urls:
                urls.append(url)
            if reason:
                reasons.append(reason)
        if diagnostics is not None:
            diagnostics.extend(reasons)
        if not urls or urls[0] in seen:
            continue
        seen.add(urls[0])
        images.append({"url": urls[0], "alt": "小红书图片", "title": "", "file_id": fid,
                       "fallback_urls": urls[1:], "width": item.get("width"), "height": item.get("height")})
    return images


def collect_video_streams(note: dict, *, diagnostics: list | None = None) -> list[dict]:
    video = note.get("video") or {}
    media = video.get("media") or {} if isinstance(video, dict) else {}
    streams = media.get("stream") or {} if isinstance(media, dict) else {}
    out, seen = [], set()
    if not isinstance(streams, dict):
        return out
    for codec in ("h264", "h265", "h266", "av1"):
        for item in _list(streams.get(codec)):
            if not isinstance(item, dict):
                continue
            url, master_reason = _url_and_reason(item.get("masterUrl"))
            backups = []
            for candidate in _list(item.get("backupUrls")):
                backup_url, reason = _url_and_reason(candidate)
                if backup_url:
                    backups.append(backup_url)
                if reason and diagnostics is not None:
                    # Count every rejected string candidate of this stream. Non-string
                    # entries are not URLs and yield no control_char reason.
                    diagnostics.append(reason)
            if diagnostics is not None and master_reason:
                diagnostics.append(master_reason)
            # An unusable master URL must not discard an otherwise valid stream:
            # fall back to the first valid backup as the primary reference.
            if not url and backups:
                url = backups[0]
                backups = backups[1:]
            if not url or url in seen:
                continue
            seen.add(url)
            duration = item.get("videoDuration")
            out.append({"url": url, "codec": codec, "quality": item.get("qualityType"),
                        "width": item.get("width"), "height": item.get("height"), "size": item.get("size"),
                        "duration_seconds": duration / 1000 if isinstance(duration, (int, float)) else None,
                        "backup_urls": backups})
    return out


def normalize_note(raw: dict, *, source_url: str = "", source: str = "") -> dict:
    note = raw.get("note_card") if isinstance(raw.get("note_card"), dict) else raw
    author = note.get("user") or note.get("userInfo") or {}
    author = author if isinstance(author, dict) else {}
    diagnostics: list = []
    images = collect_note_images(note, diagnostics=diagnostics)
    videos = collect_video_streams(note, diagnostics=diagnostics)
    interaction = note.get("interactInfo") or note.get("interact_info") or {}
    interaction = interaction if isinstance(interaction, dict) else {}
    metrics = {}
    for name, aliases in {"likes": ("likedCount", "liked_count"), "collects": ("collectedCount", "collected_count"),
                          "comments": ("commentCount", "comment_count"), "shares": ("shareCount", "share_count")}.items():
        metrics[name] = next((interaction[k] for k in aliases if k in interaction), None)
    return {"site": "Xiaohongshu", "source": source, "source_url": source_url,
            "note_id": note.get("noteId") or note.get("note_id") or "",
            "title": str(note.get("title") or note.get("displayTitle") or "").strip(),
            "description": str(note.get("desc") or note.get("description") or note.get("content") or "").strip(),
            "author": str(author.get("nickname") or author.get("nickName") or author.get("name") or ""),
            "image_count": len(images), "images": images, "video_streams": videos, "metrics": metrics,
            "comments": [], "comments_status": "not_collected", "media_status": "references_only",
            # Additive diagnostics: reason codes only, no URLs. Sorted and de-duplicated
            # so the value is stable regardless of candidate order.
            "media_diagnostics": sorted(set(diagnostics)),
            "asr_status": "not_run", "raw": raw}


def parse_xhs_page(html: str, *, source_url: str = "") -> dict:
    if source_url and not is_xhs_url(source_url):
        raise ValueError("not_a_xiaohongshu_url")
    note = extract_initial_state_note(html, source_url)
    if not note:
        raise ValueError("xhs_initial_state_note_missing")
    return normalize_note(note, source_url=source_url, source="saved_page_initial_state")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    result = parse_xhs_page(args.html.read_text(encoding="utf-8"), source_url=args.url)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
