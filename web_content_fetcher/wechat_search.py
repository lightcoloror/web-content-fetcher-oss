from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from .batch import BatchFetchRequest, fetch_batch
from .wechat_download_api import (
    normalize_wechat_article_url,
    search_wechat_accounts,
    search_wechat_articles,
)


DISCOVERY_PROVIDER_ENV = "WEB_CONTENT_FETCHER_WECHAT_DISCOVERY_PATH"
DISCOVERY_ENGINES = {"sogou", "bing", "duckduckgo", "all"}
DISCOVERY_PROVIDERS = {"auto", "wechat_download_api", "get_wechat_oa"}


@dataclass
class WechatSearchArchiveRequest:
    account_name: str
    archive_dir: str
    provider_path: str = ""
    discovery_provider: str = "auto"
    article_query: str = ""
    wechat_api_base_url: str = ""
    account_limit: int = 5
    begin: int = 0
    engine: str = "sogou"
    pages: int = 2
    limit: int = 20
    delay_seconds: float = 1.0
    discovery_timeout_seconds: int = 300
    fetch_articles: bool = True
    timeout_seconds: int = 30
    backend: str = "auto"
    extractor: str = "auto"
    browser_profile: str = ""
    browser_user_data_dir: str = ""
    browser_executable_path: str = ""
    browser_storage_state: str = ""
    headless: bool = False
    check_robots: bool = True


DiscoveryRunner = Callable[[WechatSearchArchiveRequest, Path], dict[str, Any]]
FetchRunner = Callable[[BatchFetchRequest], dict[str, Any]]



def sanitize_discovery_candidates(payload: Any, *, limit: int = 0) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("wechat_discovery_manifest_must_be_array")
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        url = normalize_wechat_article_url(str(item.get("url") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        candidates.append(
            {
                "url": url,
                "title": str(item.get("title") or "").strip(),
                "author": str(item.get("author") or item.get("nickname") or "").strip(),
                "publish_time": str(item.get("publish_time") or "").strip(),
                "provider_source": str(item.get("provider_source") or item.get("_source") or "public_search").strip(),
            }
        )
        if limit > 0 and len(candidates) >= limit:
            break
    return candidates


def load_discovery_manifest(path: str | Path, *, limit: int = 0) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return sanitize_discovery_candidates(payload, limit=limit)


def _provider_script(provider_path: str) -> Path:
    configured = provider_path.strip() or os.environ.get(DISCOVERY_PROVIDER_ENV, "").strip()
    if not configured:
        raise ValueError(f"wechat_discovery_provider_not_configured:set {DISCOVERY_PROVIDER_ENV}")
    path = Path(configured).expanduser()
    script = path / "collect_by_name.py" if path.is_dir() else path
    if not script.is_file():
        raise FileNotFoundError(f"wechat_discovery_provider_not_found:{script}")
    return script.resolve()


def run_get_wechat_oa_discovery(request: WechatSearchArchiveRequest, run_root: Path) -> dict[str, Any]:
    script = _provider_script(request.provider_path)
    provider_output = run_root / "provider"
    provider_output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(script),
        request.account_name,
        "--pages",
        str(max(1, min(int(request.pages), 20))),
        "--limit",
        str(max(1, min(int(request.limit), 200))),
        "--engine",
        request.engine,
        "--delay",
        str(max(0.2, float(request.delay_seconds))),
        "--output",
        str(provider_output),
        "--discover-only",
    ]
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    completed = subprocess.run(
        command,
        cwd=str(script.parent),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(30, int(request.discovery_timeout_seconds)),
        check=False,
    )
    stdout_path = run_root / "provider_stdout.txt"
    stderr_path = run_root / "provider_stderr.txt"
    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    if completed.returncode != 0:
        combined_output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
        no_results = "没有找到匹配该公众号名称的可公开文章" in combined_output or "搜索阶段共发现 0 个候选链接" in combined_output
        if no_results:
            return {
                "provider": "get-WeChat-OA",
                "provider_script": str(script),
                "engine": request.engine,
                "provider_status": "not_found",
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "candidates": [],
            }
        detail = (completed.stderr or completed.stdout or "provider_failed").strip()[-500:]
        raise RuntimeError(f"wechat_discovery_provider_failed:{completed.returncode}:{detail}")
    manifests = sorted(provider_output.rglob("discovered_articles.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not manifests:
        raise RuntimeError("wechat_discovery_manifest_not_found")
    candidates = load_discovery_manifest(manifests[0], limit=request.limit)
    return {
        "provider": "get-WeChat-OA",
        "provider_script": str(script),
        "engine": request.engine,
        "manifest_path": str(manifests[0]),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "candidates": candidates,
    }


def run_wechat_download_api_discovery(request: WechatSearchArchiveRequest, run_root: Path) -> dict[str, Any]:
    accounts = search_wechat_accounts(
        request.account_name,
        base_url=request.wechat_api_base_url,
        timeout_seconds=min(max(5, request.timeout_seconds), 60),
    )
    selected_accounts = accounts[: max(1, min(int(request.account_limit), 20))]
    candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    for account in selected_accounts:
        try:
            candidates.extend(
                search_wechat_articles(
                    str(account.get("fakeid") or ""),
                    query=request.article_query,
                    begin=max(0, int(request.begin)),
                    count=max(1, min(int(request.limit), 100)),
                    account=account,
                    base_url=request.wechat_api_base_url,
                    timeout_seconds=min(max(5, request.timeout_seconds), 60),
                )
            )
        except Exception as exc:
            errors.append(
                f"account:{account.get('nickname') or account.get('fakeid')}:"
                f"{type(exc).__name__}:{str(exc)[:200]}"
            )
    return {
        "provider": "wechat-download-api",
        "provider_status": "partial" if errors and candidates else "ok" if candidates else "not_found",
        "api_base_url": request.wechat_api_base_url,
        "article_query": request.article_query,
        "accounts": selected_accounts,
        "errors": errors,
        "candidates": candidates,
    }


def run_wechat_discovery(request: WechatSearchArchiveRequest, run_root: Path) -> dict[str, Any]:
    if request.discovery_provider == "wechat_download_api":
        return run_wechat_download_api_discovery(request, run_root)
    if request.discovery_provider == "get_wechat_oa":
        return run_get_wechat_oa_discovery(request, run_root)

    api_error = ""
    try:
        api_result = run_wechat_download_api_discovery(request, run_root)
        if api_result.get("candidates"):
            return api_result
        api_error = "wechat-download-api:no_candidates"
    except Exception as exc:
        api_error = f"wechat-download-api:{type(exc).__name__}:{str(exc)[:300]}"

    fallback = run_get_wechat_oa_discovery(request, run_root)
    if api_error:
        fallback["errors"] = [api_error, *(fallback.get("errors") or [])]
        fallback["fallback_from"] = "wechat-download-api"
    return fallback


def _run_id(account_name: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha256(account_name.encode("utf-8")).hexdigest()[:10]
    return f"{stamp}-{digest}"


def _write_search_index(run_root: Path, data: dict[str, Any]) -> dict[str, str]:
    candidates_path = run_root / "candidates.json"
    urls_path = run_root / "urls.txt"
    summary_path = run_root / "summary.json"
    index_path = run_root / "index.md"
    candidates = data.get("candidates") or []
    items = ((data.get("fetch") or {}).get("items") or [])
    item_by_url = {str(item.get("requested_url") or ""): item for item in items}
    candidates_path.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    urls_path.write_text("\n".join(str(item.get("url") or "") for item in candidates), encoding="utf-8")
    lines = [
        f"# WeChat search archive: {data.get('account_name') or ''}",
        "",
        f"- Status: `{data.get('status') or ''}`",
        f"- Discovery provider: `{(data.get('discovery') or {}).get('provider') or ''}`",
        f"- Engine: `{(data.get('discovery') or {}).get('engine') or ''}`",
        f"- Candidates: `{len(candidates)}`",
        f"- Run directory: `{run_root}`",
        "- Scope: publicly indexed articles only; this is not a complete account history export.",
        "",
        "## Articles",
        "",
    ]
    for candidate in candidates:
        url = str(candidate.get("url") or "")
        fetched = item_by_url.get(url) or {}
        title = str(candidate.get("title") or fetched.get("title") or url)
        lines.extend(
            [
                f"- [{title}]({url})",
                f"  - status: `{fetched.get('status') or 'not_fetched'}`",
                f"  - quality: `{fetched.get('quality_status') or ''}`",
                f"  - archive: `{fetched.get('archive_path') or ''}`",
            ]
        )
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    paths = {
        "run_dir": str(run_root),
        "candidates_path": str(candidates_path),
        "urls_path": str(urls_path),
        "summary_path": str(summary_path),
        "index_path": str(index_path),
    }
    data["paths"] = paths
    summary_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths


def search_and_archive_wechat(
    request: WechatSearchArchiveRequest,
    *,
    discovery_runner: DiscoveryRunner | None = None,
    fetch_runner: FetchRunner | None = None,
) -> dict[str, Any]:
    account_name = request.account_name.strip()
    if not account_name:
        raise ValueError("wechat_account_name_required")
    if not request.archive_dir.strip():
        raise ValueError("archive_dir_required")
    if request.engine not in DISCOVERY_ENGINES:
        raise ValueError(f"unsupported_wechat_discovery_engine:{request.engine}")
    if request.discovery_provider not in DISCOVERY_PROVIDERS:
        raise ValueError(f"unsupported_wechat_discovery_provider:{request.discovery_provider}")
    root = Path(request.archive_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    run_root = root / "wechat-searches" / _run_id(account_name)
    run_root.mkdir(parents=True, exist_ok=False)
    discover = discovery_runner or run_wechat_discovery
    try:
        discovery = discover(request, run_root)
    except Exception as exc:
        data = {
            "status": "failed",
            "account_name": account_name,
            "scope": "publicly_indexed_articles",
            "complete_account_history": False,
            "discovery": {"provider": request.discovery_provider, "engine": request.engine},
            "candidates": [],
            "fetch": {},
            "errors": [f"{type(exc).__name__}:{str(exc)[:500]}"],
        }
        _write_search_index(run_root, data)
        return data
    candidates = sanitize_discovery_candidates(discovery.get("candidates") or [], limit=request.limit)
    data: dict[str, Any] = {
        "status": "not_found" if not candidates else "discovered",
        "account_name": account_name,
        "scope": "publicly_indexed_articles",
        "complete_account_history": False,
        "discovery": {key: value for key, value in discovery.items() if key != "candidates"},
        "candidates": candidates,
        "fetch": {},
        "errors": list(discovery.get("errors") or []),
    }
    if candidates and request.fetch_articles:
        runner = fetch_runner or fetch_batch
        fetch_backend = request.backend
        if fetch_backend == "auto" and discovery.get("provider") == "wechat-download-api":
            fetch_backend = "wechat_download_api"
        batch = runner(
            BatchFetchRequest(
                urls=[str(item.get("url") or "") for item in candidates],
                archive_dir=str(root),
                timeout_seconds=request.timeout_seconds,
                backend=fetch_backend,
                extractor=request.extractor,
                as_documents=fetch_backend != "wechat_download_api",
                browser_profile=request.browser_profile,
                browser_user_data_dir=request.browser_user_data_dir,
                browser_executable_path=request.browser_executable_path,
                browser_storage_state=request.browser_storage_state,
                headless=request.headless,
                check_robots=request.check_robots,
                include_results=False,
                save_report=True,
                wechat_api_base_url=request.wechat_api_base_url,
            )
        )
        data["fetch"] = batch
        data["status"] = str(batch.get("status") or "failed")
    _write_search_index(run_root, data)
    return data
