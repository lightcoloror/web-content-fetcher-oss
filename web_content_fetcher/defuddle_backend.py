from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DefuddleRequest:
    html: str
    timeout_seconds: int = 20
    executable: str = ""


def default_defuddle_executable() -> str:
    return os.getenv("WEB_CONTENT_FETCHER_DEFUDDLE_COMMAND", "").strip() or "defuddle"


def defuddle_available(executable: str = "") -> bool:
    target = executable or default_defuddle_executable()
    return bool(shutil.which(target) or os.path.exists(target))


def run_defuddle(request: DefuddleRequest) -> dict[str, Any]:
    executable = request.executable or default_defuddle_executable()
    command = [executable, "parse", "-", "--json", "--markdown"]
    try:
        completed = subprocess.run(
            command,
            input=request.html,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, request.timeout_seconds),
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"defuddle_command_not_found:{executable}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"defuddle_timeout:{exc.timeout}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "defuddle_failed").strip().replace("\n", " ")
        raise RuntimeError(f"defuddle_returncode:{completed.returncode}:{detail[:300]}")
    try:
        parsed = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"defuddle_invalid_json:{str(exc)[:160]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("defuddle_invalid_result")

    markdown = str(parsed.get("contentMarkdown") or "").strip()
    content_html = str(parsed.get("content") or "").strip()
    if not markdown and not content_html:
        raise RuntimeError("defuddle_empty_content")
    metadata = {
        key: parsed.get(key)
        for key in (
            "title",
            "description",
            "domain",
            "favicon",
            "image",
            "language",
            "published",
            "author",
            "site",
            "wordCount",
            "extractorType",
            "schemaOrgData",
        )
        if parsed.get(key) not in (None, "", [], {})
    }
    return {
        "success": True,
        "markdown": markdown,
        "html": content_html,
        "metadata": metadata,
        "config": {
            "executable": executable,
            "local_input": True,
            "network_fetch": False,
        },
        "stderr": (completed.stderr or "").strip(),
    }
