from __future__ import annotations

from copy import deepcopy
from typing import Any


PROFILES: dict[str, dict[str, Any]] = {
    "generic": {
        "selectors": [],
        "timeout_seconds": 15,
        "description": "普通网页，使用正文抽取和链接抽取。",
    },
    "forum": {
        "selectors": [
            {"name": "topic", "selector": "article", "max_matches": 3},
            {"name": "main", "selector": "main", "max_matches": 3},
            {"name": "content", "selector": "div.content", "max_matches": 5},
            {"name": "reply", "selector": "div.reply_content", "max_matches": 20},
        ],
        "timeout_seconds": 20,
        "description": "论坛/问答帖，尽量抽取主题、正文和回复区域。",
    },
    "official_notice": {
        "selectors": [
            {"name": "article", "selector": "article", "max_matches": 2},
            {"name": "content", "selector": "div.content", "max_matches": 3},
            {"name": "main", "selector": "main", "max_matches": 2},
        ],
        "timeout_seconds": 20,
        "description": "政府/监管/官方公告，优先正文区域，保留证据归档。",
    },
    "github_release": {
        "selectors": [
            {"name": "release", "selector": "div.Box-body", "max_matches": 5},
            {"name": "markdown", "selector": "div.markdown-body", "max_matches": 5},
            {"name": "main", "selector": "main", "max_matches": 2},
        ],
        "timeout_seconds": 20,
        "description": "GitHub release/repo 页面，偏向 markdown/release 内容。",
    },
    "feishu_doc": {
        "selectors": [
            {"name": "document", "selector": "div.lark-docx-page", "max_matches": 1},
            {"name": "main", "selector": "main", "max_matches": 1},
            {"name": "body", "selector": "body", "max_matches": 1},
        ],
        "timeout_seconds": 45,
        "use_browser": True,
        "browser_auto_scroll": True,
        "browser_scroll_steps": 10,
        "description": "飞书云文档，优先浏览器渲染并滚动加载正文和图片。",
    },
}


def apply_profile(profile: str, values: dict[str, Any]) -> dict[str, Any]:
    data = deepcopy(values)
    spec = PROFILES.get((profile or "generic").strip(), {})
    if not spec:
        return data
    if not data.get("selectors"):
        data["selectors"] = deepcopy(spec.get("selectors") or [])
    if not data.get("timeout_seconds"):
        data["timeout_seconds"] = spec.get("timeout_seconds", 15)
    for key in ("use_browser", "browser_auto_scroll", "browser_wait_until", "browser_scroll_steps", "browser_settle_ms"):
        if key in spec and key not in data:
            data[key] = deepcopy(spec[key])
    data["profile_description"] = spec.get("description", "")
    return data
