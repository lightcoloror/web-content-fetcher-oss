from __future__ import annotations

import os
import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


@dataclass
class ScraplingRequest:
    url: str
    timeout_seconds: int = 30
    mode: str = "static"
    headless: bool = True
    network_idle: bool = False
    wait_ms: int = 0
    wait_selector: str = ""
    real_chrome: bool = False
    user_data_dir: str = ""
    python_path: str = ""
    python_executable: str = ""


def _response_body_text(response: Any) -> str:
    body = getattr(response, "body", b"")
    encoding = str(getattr(response, "encoding", "") or "utf-8")
    if isinstance(body, bytes):
        return body.decode(encoding, errors="replace")
    if isinstance(body, str):
        return body
    html_content = getattr(response, "html_content", "")
    return str(html_content or "")


def _selector_text(response: Any) -> str:
    try:
        return str(response.get_all_text(strip=True, separator="\n") or "")
    except Exception:
        return ""


def _selector_links(response: Any) -> list[str]:
    try:
        values = response.css("a::attr(href)").get_all()
    except Exception:
        return []
    return [str(item) for item in values or [] if str(item or "").strip()]


def _selector_images(response: Any) -> list[dict[str, Any]]:
    try:
        values = response.css("img")
    except Exception:
        return []
    images: list[dict[str, Any]] = []
    for item in values or []:
        try:
            src = str(item.attrib.get("src") or "")
            alt = str(item.attrib.get("alt") or "")
        except Exception:
            continue
        if src:
            images.append({"url": src, "src": src, "alt": alt})
    return images


def _default_python_executable() -> str:
    return os.getenv("WEB_CONTENT_FETCHER_SCRAPLING_PYTHON", "").strip()


def _run_scrapling_in_process(request: ScraplingRequest) -> dict[str, Any]:
    python_path = request.python_path or os.getenv("WEB_CONTENT_FETCHER_SCRAPLING_PATH", "")
    if python_path:
        os.environ.setdefault("PYTHONPATH", python_path)
        if python_path not in sys.path:
            sys.path.insert(0, python_path)
    mode = (request.mode or "static").strip().lower()
    timeout_ms = max(1, int(request.timeout_seconds or 30)) * 1000
    if mode == "static":
        from scrapling.fetchers import Fetcher

        response = Fetcher.get(request.url, timeout=timeout_ms)
    elif mode == "dynamic":
        from scrapling.fetchers import DynamicFetcher

        kwargs: dict[str, Any] = {
            "headless": bool(request.headless),
            "network_idle": bool(request.network_idle),
            "timeout": timeout_ms,
            "real_chrome": bool(request.real_chrome),
        }
        if request.wait_ms:
            kwargs["wait"] = int(request.wait_ms)
        if request.wait_selector:
            kwargs["wait_selector"] = request.wait_selector
        response = DynamicFetcher.fetch(request.url, **kwargs)
    elif mode == "stealthy":
        from scrapling.fetchers import StealthyFetcher

        kwargs = {
            "headless": bool(request.headless),
            "network_idle": bool(request.network_idle),
            "timeout": timeout_ms,
            "real_chrome": bool(request.real_chrome),
        }
        if request.wait_ms:
            kwargs["wait"] = int(request.wait_ms)
        if request.wait_selector:
            kwargs["wait_selector"] = request.wait_selector
        if request.user_data_dir:
            kwargs["user_data_dir"] = request.user_data_dir
        response = StealthyFetcher.fetch(request.url, **kwargs)
    else:
        raise ValueError(f"unsupported_scrapling_mode:{mode}")
    html = _response_body_text(response)
    return {
        "success": True,
        "url": str(getattr(response, "url", "") or request.url),
        "status": int(getattr(response, "status", 0) or 0),
        "reason": str(getattr(response, "reason", "") or ""),
        "headers": dict(getattr(response, "headers", {}) or {}),
        "html": html,
        "text": _selector_text(response),
        "links": _selector_links(response),
        "images": _selector_images(response),
        "config": {
            "mode": mode,
            "headless": bool(request.headless),
            "network_idle": bool(request.network_idle),
            "wait_ms": int(request.wait_ms or 0),
            "wait_selector": request.wait_selector,
            "real_chrome": bool(request.real_chrome),
            "user_data_dir_used": bool(request.user_data_dir),
            "python_path": python_path,
        },
    }


def _run_scrapling_subprocess(request: ScraplingRequest, python_executable: str) -> dict[str, Any]:
    payload = {
        "url": request.url,
        "timeout_seconds": request.timeout_seconds,
        "mode": request.mode,
        "headless": request.headless,
        "network_idle": request.network_idle,
        "wait_ms": request.wait_ms,
        "wait_selector": request.wait_selector,
        "real_chrome": request.real_chrome,
        "user_data_dir": request.user_data_dir,
        "python_path": request.python_path or os.getenv("WEB_CONTENT_FETCHER_SCRAPLING_PATH", ""),
    }
    script = r'''
import json
import os
import sys

payload = json.loads(sys.stdin.read())
python_path = payload.get("python_path") or ""
if python_path and python_path not in sys.path:
    sys.path.insert(0, python_path)

def body_text(response):
    body = getattr(response, "body", b"")
    encoding = str(getattr(response, "encoding", "") or "utf-8")
    if isinstance(body, bytes):
        return body.decode(encoding, errors="replace")
    if isinstance(body, str):
        return body
    return str(getattr(response, "html_content", "") or "")

def selector_text(response):
    try:
        return str(response.get_all_text(strip=True, separator="\n") or "")
    except Exception:
        return ""

def selector_links(response):
    try:
        values = response.css("a::attr(href)").get_all()
    except Exception:
        return []
    return [str(item) for item in values or [] if str(item or "").strip()]

def selector_images(response):
    try:
        values = response.css("img")
    except Exception:
        return []
    images = []
    for item in values or []:
        try:
            src = str(item.attrib.get("src") or "")
            alt = str(item.attrib.get("alt") or "")
        except Exception:
            continue
        if src:
            images.append({"url": src, "src": src, "alt": alt})
    return images

mode = str(payload.get("mode") or "static").strip().lower()
timeout_ms = max(1, int(payload.get("timeout_seconds") or 30)) * 1000
if mode == "static":
    from scrapling.fetchers import Fetcher
    response = Fetcher.get(payload["url"], timeout=timeout_ms)
elif mode == "dynamic":
    from scrapling.fetchers import DynamicFetcher
    kwargs = {
        "headless": bool(payload.get("headless", True)),
        "network_idle": bool(payload.get("network_idle", False)),
        "timeout": timeout_ms,
        "real_chrome": bool(payload.get("real_chrome", False)),
    }
    if payload.get("wait_ms"):
        kwargs["wait"] = int(payload["wait_ms"])
    if payload.get("wait_selector"):
        kwargs["wait_selector"] = payload["wait_selector"]
    response = DynamicFetcher.fetch(payload["url"], **kwargs)
elif mode == "stealthy":
    from scrapling.fetchers import StealthyFetcher
    kwargs = {
        "headless": bool(payload.get("headless", True)),
        "network_idle": bool(payload.get("network_idle", False)),
        "timeout": timeout_ms,
        "real_chrome": bool(payload.get("real_chrome", False)),
    }
    if payload.get("wait_ms"):
        kwargs["wait"] = int(payload["wait_ms"])
    if payload.get("wait_selector"):
        kwargs["wait_selector"] = payload["wait_selector"]
    if payload.get("user_data_dir"):
        kwargs["user_data_dir"] = payload["user_data_dir"]
    response = StealthyFetcher.fetch(payload["url"], **kwargs)
else:
    raise ValueError(f"unsupported_scrapling_mode:{mode}")
html = body_text(response)
print(json.dumps({
    "success": True,
    "url": str(getattr(response, "url", "") or payload["url"]),
    "status": int(getattr(response, "status", 0) or 0),
    "reason": str(getattr(response, "reason", "") or ""),
    "headers": dict(getattr(response, "headers", {}) or {}),
    "html": html,
    "text": selector_text(response),
    "links": selector_links(response),
    "images": selector_images(response),
    "config": {
        "mode": mode,
        "headless": bool(payload.get("headless", True)),
        "network_idle": bool(payload.get("network_idle", False)),
        "wait_ms": int(payload.get("wait_ms") or 0),
        "wait_selector": payload.get("wait_selector") or "",
        "real_chrome": bool(payload.get("real_chrome", False)),
        "user_data_dir_used": bool(payload.get("user_data_dir")),
        "python_path": python_path,
        "python_executable": sys.executable,
    },
}, ensure_ascii=False))
'''
    completed = subprocess.run(
        [python_executable, "-c", script],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=max(30, int(request.timeout_seconds or 30) + 30),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"scrapling_subprocess_failed:{completed.stderr.strip()[:500]}")
    stdout = completed.stdout.strip()
    if not stdout:
        raise RuntimeError("scrapling_subprocess_failed:empty_stdout")
    return json.loads(stdout.splitlines()[-1])


def run_scrapling(request: ScraplingRequest) -> dict[str, Any]:
    try:
        return _run_scrapling_in_process(request)
    except ModuleNotFoundError:
        python_executable = request.python_executable or _default_python_executable()
        if not python_executable:
            raise
        return _run_scrapling_subprocess(request, python_executable)
