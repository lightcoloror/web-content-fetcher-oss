from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .archive import archive_child_path, enrich_archive, sha256_file, write_archive


@dataclass(frozen=True)
class BrowsertrixRequest:
    urls: list[str]
    output_dir: str
    collection: str = "wcf-capture"
    archive_dir: str = ""
    docker_executable: str = "docker"
    image: str = "webrecorder/browsertrix-crawler"
    timeout_seconds: int = 900
    profile_path: str = ""
    extra_args: list[str] = field(default_factory=list)
    copy_artifacts_to_archive: bool = True


@dataclass
class BrowsertrixResult:
    status: str
    urls: list[str]
    command: list[str]
    collection: str
    output_dir: str
    wacz_path: str = ""
    pages_path: str = ""
    archived_wacz_path: str = ""
    archived_pages_path: str = ""
    wacz_sha256: str = ""
    pages_sha256: str = ""
    stdout: str = ""
    stderr: str = ""
    archive: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_browsertrix(request: BrowsertrixRequest) -> BrowsertrixResult:
    urls = [item.strip() for item in request.urls if item and item.strip()]
    if not urls:
        raise ValueError("BrowsertrixRequest.urls must contain at least one URL")
    output_dir = Path(request.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [request.docker_executable, "run", "--rm", "-v", f"{output_dir}:/crawls", request.image, "crawl"]
    for url in urls:
        command.extend(["--url", url])
    command.extend(["--generateWACZ", "--text", "to-pages", "--screenshot", "view", "--collection", request.collection])
    if request.profile_path:
        profile = Path(request.profile_path).resolve()
        command[4:4] = ["-v", f"{profile}:/profile.tar.gz:ro"]
        command.extend(["--profile", "/profile.tar.gz"])
    command.extend(request.extra_args)
    result = BrowsertrixResult(status="failed", urls=urls, command=command, collection=request.collection, output_dir=str(output_dir))
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=max(1, request.timeout_seconds), check=False)
        result.stdout = completed.stdout or ""
        result.stderr = completed.stderr or ""
        if completed.returncode != 0:
            result.errors.append(f"browsertrix:returncode:{completed.returncode}")
        else:
            collection_dir = output_dir / "collections" / request.collection
            candidates = sorted(collection_dir.rglob("*.wacz"))
            result.wacz_path = str(candidates[0]) if candidates else ""
            pages = collection_dir / "pages" / "pages.jsonl"
            result.pages_path = str(pages) if pages.exists() else ""
            result.status = "ok" if result.wacz_path else "partial"
            if not result.wacz_path:
                result.errors.append("browsertrix:wacz_missing")
    except FileNotFoundError as exc:
        result.errors.append(f"browsertrix:docker_not_found:{exc}")
    except subprocess.TimeoutExpired as exc:
        result.errors.append(f"browsertrix:timeout:{exc.timeout}")

    if request.archive_dir:
        title = f"Browsertrix WACZ: {urls[0]}"
        result.archive = write_archive(
            request.archive_dir,
            f"browsertrix-{request.collection}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            url=urls[0],
            title=title,
            raw_snapshot=result.stdout,
            extracted_text="\n".join(["# Browsertrix WACZ", "", f"- Status: {result.status}", f"- WACZ source: {result.wacz_path}", f"- Pages source: {result.pages_path}", "", result.stderr]),
            metadata={
                "status": result.status,
                "requested_url": urls[0],
                "final_url": urls[0],
                "title": title,
                "transport": "browsertrix",
                "errors": result.errors,
                "browsertrix": {
                    "command": result.command,
                    "collection": result.collection,
                    "output_dir": result.output_dir,
                    "wacz_path": result.wacz_path,
                    "pages_path": result.pages_path,
                    "copy_artifacts_to_archive": request.copy_artifacts_to_archive,
                },
            },
        )
        artifacts: list[dict[str, Any]] = []
        for source_value, role, media_type, label in (
            (result.wacz_path, "browsertrix_wacz", "application/wacz", "Browsertrix WACZ"),
            (result.pages_path, "browsertrix_pages", "application/x-ndjson", "Browsertrix pages index"),
        ):
            source = Path(source_value) if source_value else None
            if source is None or not source.exists() or not source.is_file():
                continue
            archived = source
            if request.copy_artifacts_to_archive:
                destination = archive_child_path(result.archive, "browsertrix", source.name)
                assert destination is not None
                if destination.resolve() != source.resolve():
                    shutil.copy2(source, destination)
                archived = destination
            digest = sha256_file(archived)
            artifact = {
                "role": role,
                "path": str(archived),
                "media_type": media_type,
                "source": "browsertrix",
                "label": label,
                "description": "Copied from Browsertrix output for local long-term evidence" if request.copy_artifacts_to_archive else "Referenced Browsertrix output; retain the external output directory",
                "sha256": digest,
                "bytes": archived.stat().st_size,
                "view": "evidence",
            }
            if archived.resolve() != source.resolve():
                artifact["external_path"] = str(source)
            artifacts.append(artifact)
            if role == "browsertrix_wacz":
                result.archived_wacz_path = str(archived)
                result.wacz_sha256 = digest
            else:
                result.archived_pages_path = str(archived)
                result.pages_sha256 = digest
        result.archive["archive_index"] = enrich_archive(
            result.archive,
            artifacts=artifacts,
            title=title,
            url=urls[0],
            transport="browsertrix",
        )
    return result
