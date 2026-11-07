from __future__ import annotations

import hashlib
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any

from .archive import write_archive


@dataclass
class ArchiveBoxRequest:
    urls: list[str]
    archivebox_dir: str = ""
    archive_dir: str = ""
    executable: str = "archivebox"
    timeout_seconds: int = 120
    extra_args: list[str] = field(default_factory=list)


@dataclass
class ArchiveBoxResult:
    status: str
    urls: list[str]
    command: list[str]
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    archivebox_dir: str = ""
    archive: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _archivebox_item_id(urls: list[str]) -> str:
    digest = hashlib.sha1("\n".join(urls).encode("utf-8")).hexdigest()[:12]
    return f"archivebox-{digest}"


def _archivebox_metadata(result: ArchiveBoxResult) -> dict[str, Any]:
    first_url = result.urls[0] if result.urls else ""
    title = f"ArchiveBox add: {first_url}" if first_url else "ArchiveBox add"
    return {
        "status": result.status,
        "requested_url": first_url,
        "final_url": first_url,
        "title": title,
        "transport": "archivebox",
        "errors": result.errors,
        "archivebox": {
            "urls": result.urls,
            "command": result.command,
            "returncode": result.returncode,
            "archivebox_dir": result.archivebox_dir,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
        "archive_index": {
            "title": title,
            "final_url": first_url,
            "transport": "archivebox",
            "status": result.status,
            "errors": result.errors,
            "external_backend": {
                "name": "ArchiveBox",
                "command": result.command,
                "archivebox_dir": result.archivebox_dir,
                "returncode": result.returncode,
            },
            "files": [],
        },
    }


def run_archivebox_add(request: ArchiveBoxRequest) -> ArchiveBoxResult:
    urls = [url.strip() for url in request.urls if url and url.strip()]
    if not urls:
        raise ValueError("ArchiveBoxRequest.urls must contain at least one URL")
    command = [request.executable or "archivebox", "add", *request.extra_args, *urls]
    result = ArchiveBoxResult(status="failed", urls=urls, command=command, archivebox_dir=request.archivebox_dir)
    try:
        completed = subprocess.run(
            command,
            cwd=request.archivebox_dir or None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=request.timeout_seconds,
            check=False,
        )
        result.returncode = completed.returncode
        result.stdout = completed.stdout or ""
        result.stderr = completed.stderr or ""
        result.status = "ok" if completed.returncode == 0 else "failed"
        if completed.returncode != 0:
            result.errors.append(f"archivebox:returncode:{completed.returncode}")
    except FileNotFoundError as exc:
        result.errors.append(f"archivebox:FileNotFoundError:{exc}")
    except subprocess.TimeoutExpired as exc:
        result.errors.append(f"archivebox:TimeoutExpired:{exc}")
        result.stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        result.stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""

    if request.archive_dir:
        metadata = _archivebox_metadata(result)
        result.archive = write_archive(
            request.archive_dir,
            _archivebox_item_id(urls),
            url=urls[0],
            title=metadata["title"],
            raw_snapshot=result.stdout,
            extracted_text="\n".join(
                [
                    "# ArchiveBox Result",
                    "",
                    f"- Status: {result.status}",
                    f"- Return code: {result.returncode}",
                    f"- URLs: {', '.join(urls)}",
                    f"- ArchiveBox dir: {result.archivebox_dir}",
                    "",
                    "## Stdout",
                    "",
                    result.stdout,
                    "",
                    "## Stderr",
                    "",
                    result.stderr,
                ]
            ),
            metadata=metadata,
        )
    return result
