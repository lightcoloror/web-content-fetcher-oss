from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path
from typing import Any

from .pipeline import DEFAULT_VISUAL_COMMAND
from .scrapling_backend import _default_python_executable


def _module_status(name: str, install_hint: str) -> dict[str, Any]:
    spec = importlib.util.find_spec(name)
    return {
        "name": name,
        "kind": "python_module",
        "status": "ready" if spec is not None else "missing",
        "available": spec is not None,
        "origin": str(getattr(spec, "origin", "") or "") if spec is not None else "",
        "install_hint": "" if spec is not None else install_hint,
    }


def _cli_status(name: str, install_hint: str, executable: str = "") -> dict[str, Any]:
    target = executable or name
    resolved = shutil.which(target)
    return {
        "name": name,
        "kind": "cli",
        "status": "ready" if resolved else "missing",
        "available": bool(resolved),
        "executable": target,
        "resolved_path": resolved or "",
        "install_hint": "" if resolved else install_hint,
    }


def _file_status(name: str, path: str, install_hint: str) -> dict[str, Any]:
    if not path:
        return {
            "name": name,
            "kind": "file",
            "status": "missing",
            "available": False,
            "path": "",
            "install_hint": install_hint,
        }
    target = Path(path)
    exists = target.exists()
    return {
        "name": name,
        "kind": "file",
        "status": "ready" if exists else "missing",
        "available": exists,
        "path": str(target),
        "install_hint": "" if exists else install_hint,
    }


def _env_status(name: str, env_var: str, install_hint: str) -> dict[str, Any]:
    available = bool(os.getenv(env_var))
    return {
        "name": name,
        "kind": "env",
        "status": "ready" if available else "missing",
        "available": available,
        "env_var": env_var,
        "install_hint": "" if available else install_hint,
    }


def bridge_doctor(*, archivebox_executable: str = "archivebox", visual_command: str = "") -> dict[str, Any]:
    from .defuddle_backend import default_defuddle_executable
    from .monolith_backend import default_monolith_executable

    readeck_base = bool(os.getenv("READECK_BASE_URL", "").strip())
    readeck_token = bool(os.getenv("READECK_TOKEN", ""))
    checks = [
        _module_status("playwright", "Install optional extra: pip install .[browser], then run playwright install chromium."),
        _module_status("crawl4ai", "Install optional extra: pip install .[crawl4ai]"),
        _module_status("scrapling", "Install local Scrapling, or set PYTHONPATH/WEB_CONTENT_FETCHER_SCRAPLING_PATH to the existing checkout."),
        _file_status(
            "scrapling_python",
            os.getenv("WEB_CONTENT_FETCHER_SCRAPLING_PYTHON", "") or _default_python_executable(),
            "Set WEB_CONTENT_FETCHER_SCRAPLING_PYTHON to the local Scrapling venv Python executable.",
        ),
        _module_status("markitdown", "Install optional extra: pip install .[attachments]"),
        _env_status("firecrawl_api_key", "FIRECRAWL_API_KEY", "Set FIRECRAWL_API_KEY, or pass --firecrawl-api-key-env with a local env var name."),
        _cli_status("defuddle", "Install Defuddle CLI, or set WEB_CONTENT_FETCHER_DEFUDDLE_COMMAND.", default_defuddle_executable()),
        _cli_status("monolith", "Install Monolith CLI, or set WEB_CONTENT_FETCHER_MONOLITH_COMMAND.", default_monolith_executable()),
        {
            "name": "readeck",
            "kind": "service_config",
            "status": "ready" if readeck_base and readeck_token else "partial" if readeck_base else "missing",
            "available": readeck_base and readeck_token,
            "base_url_configured": readeck_base,
            "token_configured": readeck_token,
            "install_hint": "Set READECK_BASE_URL and READECK_TOKEN for a self-hosted Readeck instance." if not (readeck_base and readeck_token) else "",
        },
        _cli_status("docker", "Install Docker Desktop and make docker available on PATH for Browsertrix WACZ captures."),
        _module_status("crawlee", "Optional only for site-scale jobs: pip install .[crawlee]. It is not used by ordinary single-URL fetches."),
        _cli_status("archivebox", "Install ArchiveBox separately and ensure archivebox is on PATH.", archivebox_executable),
        _file_status(
            "ebook_markdown_pipeline",
            visual_command or DEFAULT_VISUAL_COMMAND,
            "Install or restore the local ebook_markdown_pipeline process-web-archive command.",
        ),
    ]
    ready = [item for item in checks if item["available"]]
    missing = [item for item in checks if not item["available"]]
    return {
        "status": "ready" if not missing else "partial" if ready else "missing",
        "ready": len(ready),
        "missing": len(missing),
        "checks": checks,
        "notes": [
            "These checks are read-only and do not fetch web content.",
            "Missing optional bridges do not break direct/browser fetching.",
            "Private/member pages still require authorized local login state or official export/API credentials.",
        ],
    }
