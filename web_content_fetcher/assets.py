from __future__ import annotations

import hashlib
import mimetypes
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _asset_name(url: str, content_type: str = "") -> str:
    parsed = urllib.parse.urlparse(url)
    stem = Path(parsed.path).name or "image"
    stem = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in stem)[:80]
    suffix = Path(stem).suffix
    if not suffix:
        suffix = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip()) or ".bin"
        stem = f"{stem}{suffix}"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"{Path(stem).stem}_{digest}{Path(stem).suffix}"


def download_images(
    images: list[dict[str, str]],
    output_dir: str | Path,
    *,
    timeout_seconds: int = 15,
    max_bytes: int = 15_000_000,
    user_agent: str = "",
) -> list[dict[str, Any]]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for image in images:
        url = image.get("url", "")
        if not url or url.startswith("data:"):
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": user_agent} if user_agent else {})
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                content_type = resp.headers.get("content-type", "")
                data = resp.read(max_bytes)
            path = root / _asset_name(url, content_type)
            path.write_bytes(data)
            item = dict(image)
            item.update(
                {
                    "asset_path": str(path),
                    "asset_sha256": hashlib.sha256(data).hexdigest(),
                    "content_type": content_type,
                    "bytes": len(data),
                }
            )
            results.append(item)
        except Exception as exc:
            item = dict(image)
            item["asset_error"] = f"{type(exc).__name__}:{str(exc)[:200]}"
            results.append(item)
    return results
