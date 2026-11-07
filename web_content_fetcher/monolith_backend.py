from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MonolithRequest:
    html: str
    base_url: str
    output_path: str
    timeout_seconds: int = 60
    executable: str = ""
    mhtml: bool = False


def default_monolith_executable() -> str:
    return os.getenv("WEB_CONTENT_FETCHER_MONOLITH_COMMAND", "").strip() or "monolith"


def monolith_available(executable: str = "") -> bool:
    target = executable or default_monolith_executable()
    return bool(shutil.which(target) or os.path.exists(target))


def run_monolith(request: MonolithRequest) -> dict[str, Any]:
    output_path = Path(request.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    executable = request.executable or default_monolith_executable()
    command = [executable, "-I", "-b", request.base_url, "-o", str(output_path)]
    if request.mhtml:
        command.append("-m")
    command.append("-")
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
        raise RuntimeError(f"monolith_command_not_found:{executable}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"monolith_timeout:{exc.timeout}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "monolith_failed").strip().replace("\n", " ")
        raise RuntimeError(f"monolith_returncode:{completed.returncode}:{detail[:300]}")
    if not output_path.exists():
        raise RuntimeError("monolith_output_missing")
    return {
        "success": True,
        "output_path": str(output_path),
        "bytes": output_path.stat().st_size,
        "config": {
            "executable": executable,
            "base_url": request.base_url,
            "mhtml": request.mhtml,
            "input_source": "captured_html_stdin",
            "cookie_file_used": False,
        },
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
    }
