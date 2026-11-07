from __future__ import annotations

import json
import time
from pathlib import Path

from .archive import safe_id, sha256_text


def cache_key(url: str) -> str:
    return safe_id(sha256_text(url)[:32])


def cache_path(cache_dir: str | Path, url: str) -> Path:
    return Path(cache_dir) / f"{cache_key(url)}.json"


def read_cache(cache_dir: str | Path, url: str, *, max_age_seconds: int = 0) -> dict | None:
    try:
        path = cache_path(cache_dir, url)
        if not path.exists():
            return None
        if max_age_seconds > 0 and time.time() - path.stat().st_mtime > max_age_seconds:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_cache(cache_dir: str | Path, url: str, data: dict) -> str:
    try:
        path = cache_path(cache_dir, url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        return str(path)
    except Exception:
        return ""
