from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .batch import BatchFetchRequest, fetch_batch


DEFAULT_CORPUS_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "real_links.json"
DEFAULT_OUTPUT_ROOT = Path("output") / "real-link-smoke"


@dataclass
class QualitySmokeRequest:
    corpus_path: str = ""
    output_root: str = ""
    timeout_seconds: int = 90
    backend: str = "auto"
    extractor: str = "auto"
    limit: int = 0
    compare_feishu_export: bool = False
    headless: bool = False
    baseline_path: str = ""


def read_corpus(path: str | Path = "") -> dict[str, Any]:
    corpus_path = Path(path) if path else DEFAULT_CORPUS_PATH
    return json.loads(corpus_path.read_text(encoding="utf-8"))


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _preferred_view_count(preferred_views: dict[str, Any]) -> int:
    all_views = preferred_views.get("all") if isinstance(preferred_views, dict) else []
    if isinstance(all_views, list):
        return len(all_views)
    primary = preferred_views.get("primary") if isinstance(preferred_views, dict) else {}
    if isinstance(primary, dict):
        return sum(1 for value in primary.values() if isinstance(value, dict) and value)
    return 0


def actual_from_item(item: dict[str, Any]) -> dict[str, Any]:
    preferred_views = item.get("preferred_views") or {}
    return {
        "status": item.get("status", ""),
        "transport": item.get("transport", ""),
        "strategy": item.get("strategy") or item.get("site_strategy") or "",
        "route": item.get("route") or item.get("site_route") or "",
        "text_chars": _as_int(item.get("text_chars") or item.get("preferred_markdown_chars")),
        "images": _as_int(item.get("image_count") or item.get("preferred_markdown_image_count") or item.get("markdown_image_count")),
        "tables": _as_int(item.get("table_count")),
        "headings": _as_int(item.get("heading_count") or item.get("preferred_markdown_heading_count") or item.get("markdown_heading_count")),
        "preferred_view_count": _preferred_view_count(preferred_views),
        "quality_status": item.get("quality_status", ""),
        "content_completeness_status": item.get("content_completeness_status", ""),
        "archive_path": item.get("archive_path", ""),
    }


def actual_from_archive_index(archive_index: dict[str, Any]) -> dict[str, Any]:
    preferred_markdown = archive_index.get("preferred_markdown") or {}
    preferred_structure = preferred_markdown.get("structure") or {}
    markdown_structure = archive_index.get("markdown_structure") or {}
    site_strategy = ((archive_index.get("site_data") or {}).get("site_strategy") or {})
    preferred_views = archive_index.get("preferred_views") or {}
    quality = archive_index.get("extraction_quality") or {}
    content = archive_index.get("content_completeness") or {}
    return {
        "status": "ok" if archive_index else "missing",
        "transport": archive_index.get("transport", ""),
        "strategy": site_strategy.get("site_key", ""),
        "route": site_strategy.get("kind", ""),
        "text_chars": _as_int(preferred_markdown.get("chars")),
        "images": _as_int(preferred_structure.get("image_count") or markdown_structure.get("image_count")),
        "tables": _as_int(markdown_structure.get("table_count")),
        "headings": _as_int(preferred_structure.get("heading_count") or markdown_structure.get("heading_count")),
        "preferred_view_count": _preferred_view_count(preferred_views),
        "quality_status": quality.get("status", ""),
        "content_completeness_status": content.get("overall_status", ""),
        "archive_path": str(archive_index.get("archive_path") or ""),
    }


def infer_rerun_strategy(link: dict[str, Any], failures: list[dict[str, Any]], actual: dict[str, Any]) -> str:
    site = str(link.get("expected_strategy") or actual.get("strategy") or "")
    codes = {str(item.get("code") or "") for item in failures}
    if not failures:
        return "accept"
    if site == "feishu_lark":
        if "tables_below_minimum" in codes or "images_below_minimum" in codes:
            return "feishu_export"
        return "browser"
    if site in {"yuque", "wechat_mp", "kdocs", "notion", "tencent_docs"}:
        if "text_below_minimum" in codes or "preferred_views_missing" in codes:
            return "browser"
        return "crawl4ai"
    if site == "zsxq":
        return "zsxq-cli"
    if site == "xhs":
        return "xhs_browser_state"
    if str(actual.get("transport") or "") == "direct":
        return "browser"
    return "manual_review"


def rerun_command_for(link: dict[str, Any], strategy: str, archive_dir: str = "") -> str:
    url = str(link.get("url") or link.get("requested_url") or "")
    base = ['python -m web_content_fetcher.cli fetch-doc', json.dumps(url, ensure_ascii=False)]
    if archive_dir:
        base.extend(["--archive-dir", json.dumps(archive_dir, ensure_ascii=False)])
    if strategy in {"browser", "crawl4ai", "feishu_export", "xhs_auto", "xhs_browser_state"}:
        base.extend(["--backend", strategy])
    if strategy == "feishu_export":
        base.append("--compare-feishu-export")
    return " ".join(base)


def evaluate_quality_gate(link: dict[str, Any], actual: dict[str, Any], *, archive_dir: str = "") -> dict[str, Any]:
    minimum = link.get("minimum") or {}
    expected = {
        "id": link.get("id", ""),
        "url": link.get("url") or link.get("requested_url", ""),
        "expected_strategy": link.get("expected_strategy", ""),
        "expected_route": link.get("expected_route", ""),
        "login_required": bool(link.get("login_required", False)),
        "minimum": minimum,
    }
    failures: list[dict[str, Any]] = []
    if str(actual.get("status") or "") != "ok":
        failures.append({"code": "status_not_ok", "expected": "ok", "actual": actual.get("status", "")})
    if expected["expected_strategy"] and actual.get("strategy") and actual.get("strategy") != expected["expected_strategy"]:
        failures.append({"code": "strategy_mismatch", "expected": expected["expected_strategy"], "actual": actual.get("strategy", "")})
    if expected["expected_route"] and actual.get("route") and actual.get("route") != expected["expected_route"]:
        failures.append({"code": "route_mismatch", "expected": expected["expected_route"], "actual": actual.get("route", "")})
    if _as_int(minimum.get("text_chars")) and _as_int(actual.get("text_chars")) < _as_int(minimum.get("text_chars")):
        failures.append({"code": "text_below_minimum", "expected": _as_int(minimum.get("text_chars")), "actual": _as_int(actual.get("text_chars"))})
    if _as_int(minimum.get("images")) and _as_int(actual.get("images")) < _as_int(minimum.get("images")):
        failures.append({"code": "images_below_minimum", "expected": _as_int(minimum.get("images")), "actual": _as_int(actual.get("images"))})
    if _as_int(minimum.get("tables")) and _as_int(actual.get("tables")) < _as_int(minimum.get("tables")):
        failures.append({"code": "tables_below_minimum", "expected": _as_int(minimum.get("tables")), "actual": _as_int(actual.get("tables"))})
    if _as_int(actual.get("preferred_view_count")) == 0:
        failures.append({"code": "preferred_views_missing", "expected": ">0", "actual": 0})
    if actual.get("content_completeness_status") in {"suspect_incomplete", "needs_review"}:
        failures.append({"code": "content_completeness_not_ok", "expected": "ok", "actual": actual.get("content_completeness_status")})
    strategy = infer_rerun_strategy(link, failures, actual)
    return {
        "version": 1,
        "expected": expected,
        "actual": actual,
        "passed": not failures,
        "failures": failures,
        "rerun_strategy": strategy,
        "recommended_next_action": "accept" if not failures else f"rerun_with_{strategy}",
        "rerun_command": "" if not failures else rerun_command_for(link, strategy, archive_dir=archive_dir),
    }


def _link_by_url(corpus: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("url") or ""): item for item in corpus.get("links", [])}


def make_rerun_plan(items: list[dict[str, Any]], *, output_root: str = "") -> dict[str, Any]:
    plan_items = []
    for item in items:
        gate = item.get("quality_gate") or {}
        if gate.get("passed"):
            continue
        plan_items.append(
            {
                "id": item.get("id", ""),
                "url": item.get("url") or item.get("requested_url", ""),
                "archive_path": item.get("archive_path", ""),
                "strategy": gate.get("rerun_strategy", "manual_review"),
                "failures": gate.get("failures", []),
                "command": gate.get("rerun_command", ""),
            }
        )
    return {
        "version": 1,
        "status": "ok",
        "output_root": output_root,
        "total": len(plan_items),
        "items": plan_items,
        "urls": [str(item.get("url") or "") for item in plan_items if item.get("url")],
    }


def compare_quality_runs(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    baseline_items = {str(item.get("id") or item.get("url") or ""): item for item in baseline.get("items", [])}
    regressions = []
    for item in current.get("items", []):
        key = str(item.get("id") or item.get("url") or "")
        previous = baseline_items.get(key)
        if not previous:
            continue
        for field, code in (("text_chars", "text_regressed"), ("image_count", "images_regressed"), ("table_count", "tables_regressed")):
            if _as_int(item.get(field)) < _as_int(previous.get(field)):
                regressions.append({"id": key, "code": code, "field": field, "previous": _as_int(previous.get(field)), "current": _as_int(item.get(field))})
        if previous.get("quality_gate", {}).get("passed") and not item.get("quality_gate", {}).get("passed"):
            regressions.append({"id": key, "code": "quality_gate_regressed", "previous": True, "current": False})
    return {
        "version": 1,
        "status": "ok" if not regressions else "regressed",
        "regression_count": len(regressions),
        "regressions": regressions,
    }


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def render_quality_summary_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# Web Content Fetcher Quality Smoke Summary",
        "",
        f"- Run ID: {data.get('run_id', '')}",
        f"- Total: {data.get('total', 0)}",
        f"- OK: {data.get('ok', 0)}",
        f"- Failed: {data.get('failed', 0)}",
        f"- Quality failed: {data.get('quality_failed', 0)}",
        f"- Output root: {data.get('output_root', '')}",
        "",
        "| ID | Status | Strategy | Route | Text | Images | Tables | Gate | Rerun | Archive |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for item in data.get("items", []):
        gate = item.get("quality_gate") or {}
        lines.append(
            f"| {item.get('id', '')} | {item.get('status', '')} | {item.get('strategy', '')} | {item.get('route', '')} | "
            f"{item.get('text_chars', 0)} | {item.get('image_count', 0)} | {item.get('table_count', 0)} | "
            f"{'ok' if gate.get('passed') else 'failed'} | {gate.get('rerun_strategy', '')} | `{item.get('archive_path', '')}` |"
        )
    lines.extend(["", "This smoke runner is opt-in and may require logged-in local browser state for private/member content."])
    return "\n".join(lines).rstrip() + "\n"


def write_quality_run(data: dict[str, Any], run_root: str | Path) -> dict[str, str]:
    root = Path(run_root)
    root.mkdir(parents=True, exist_ok=True)
    failures = [item for item in data.get("items", []) if not (item.get("quality_gate") or {}).get("passed")]
    rerun_plan = make_rerun_plan(data.get("items", []), output_root=str(root))
    paths = {
        "run_path": str(root),
        "smoke_summary_json_path": str(root / "smoke_summary.json"),
        "smoke_summary_markdown_path": str(root / "smoke_summary.md"),
        "quality_failures_json_path": str(root / "quality_failures.json"),
        "rerun_plan_json_path": str(root / "rerun_plan.json"),
    }
    data["quality_report"] = paths
    Path(paths["smoke_summary_json_path"]).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    Path(paths["smoke_summary_markdown_path"]).write_text(render_quality_summary_markdown(data), encoding="utf-8", newline="\n")
    Path(paths["quality_failures_json_path"]).write_text(json.dumps({"version": 1, "total": len(failures), "items": failures}, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    Path(paths["rerun_plan_json_path"]).write_text(json.dumps(rerun_plan, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return paths


def run_quality_smoke(request: QualitySmokeRequest, *, fetch_runner: Callable[[BatchFetchRequest], dict[str, Any]] | None = None) -> dict[str, Any]:
    corpus = read_corpus(request.corpus_path)
    links = list(corpus.get("links", []))
    if request.limit > 0:
        links = links[: request.limit]
    output_root = Path(request.output_root or corpus.get("output_root") or DEFAULT_OUTPUT_ROOT)
    run_root = output_root / _run_id()
    archive_dir = run_root / "archives"
    runner = fetch_runner or fetch_batch
    batch = runner(
        BatchFetchRequest(
            urls=[str(item.get("url") or "") for item in links],
            archive_dir=str(archive_dir),
            timeout_seconds=request.timeout_seconds,
            backend=request.backend,
            extractor=request.extractor,
            as_documents=True,
            headless=request.headless,
            compare_feishu_export=request.compare_feishu_export,
            include_results=False,
        )
    )
    link_by_url = _link_by_url({"links": links})
    quality_items = []
    for batch_item in batch.get("items", []):
        url = str(batch_item.get("requested_url") or "")
        link = link_by_url.get(url, {"url": url, "id": url})
        actual = actual_from_item(batch_item)
        gate = evaluate_quality_gate(link, actual, archive_dir=str(archive_dir))
        quality_items.append(
            {
                "id": link.get("id", ""),
                "category": link.get("category", ""),
                "url": url,
                "status": batch_item.get("status", ""),
                "transport": batch_item.get("transport", ""),
                "strategy": batch_item.get("strategy") or link.get("expected_strategy", ""),
                "route": batch_item.get("route") or link.get("expected_route", ""),
                "text_chars": actual["text_chars"],
                "image_count": actual["images"],
                "table_count": actual["tables"],
                "quality_status": batch_item.get("quality_status", ""),
                "content_completeness_status": batch_item.get("content_completeness_status", ""),
                "archive_path": batch_item.get("archive_path", ""),
                "errors": batch_item.get("errors", []),
                "quality_gate": gate,
            }
        )
    data: dict[str, Any] = {
        "version": 1,
        "status": batch.get("status", "ok"),
        "run_id": run_root.name,
        "corpus_path": str(request.corpus_path or DEFAULT_CORPUS_PATH),
        "output_root": str(run_root),
        "batch_report": batch.get("batch_report", {}),
        "total": len(quality_items),
        "ok": sum(1 for item in quality_items if item.get("status") == "ok"),
        "failed": sum(1 for item in quality_items if item.get("status") != "ok"),
        "quality_failed": sum(1 for item in quality_items if not item.get("quality_gate", {}).get("passed")),
        "items": quality_items,
    }
    if request.baseline_path:
        try:
            baseline = inspect_quality_run(request.baseline_path)
            data["comparison"] = compare_quality_runs(data, baseline)
        except Exception as exc:
            data["comparison_error"] = f"{type(exc).__name__}:{str(exc)[:200]}"
    write_quality_run(data, run_root)
    return data


def inspect_quality_run(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if target.is_dir():
        target = target / "smoke_summary.json"
    return json.loads(target.read_text(encoding="utf-8"))


def list_quality_runs(output_root: str | Path, *, limit: int = 20) -> list[dict[str, Any]]:
    root = Path(output_root)
    if not root.exists():
        return []
    items = []
    for child in root.iterdir():
        summary = child / "smoke_summary.json"
        if not summary.exists():
            continue
        try:
            data = inspect_quality_run(summary)
        except Exception:
            continue
        report = data.get("quality_report") or {}
        recovery_dir = child / "manual_recovery"
        recovery_markdown_path = report.get("manual_recovery_markdown_path") or str(recovery_dir / "manual_recovery.md")
        recovery_json_path = report.get("manual_recovery_json_path") or str(recovery_dir / "manual_recovery.json")
        recovery_urls_path = report.get("rerun_urls_path")
        if not recovery_urls_path and (recovery_dir / "rerun-urls.txt").exists():
            recovery_urls_path = str(recovery_dir / "rerun-urls.txt")
        items.append(
            {
                "run_id": data.get("run_id") or child.name,
                "run_path": str(child),
                "status": data.get("status", ""),
                "total": data.get("total", 0),
                "failed": data.get("failed", 0),
                "quality_failed": data.get("quality_failed", 0),
                "summary_path": str(summary),
                "summary_markdown_path": str(child / "smoke_summary.md"),
                "quality_failures_json_path": report.get("quality_failures_json_path", str(child / "quality_failures.json")),
                "rerun_plan_json_path": report.get("rerun_plan_json_path", str(child / "rerun_plan.json")),
                "manual_recovery_markdown_path": recovery_markdown_path if Path(recovery_markdown_path).exists() else "",
                "manual_recovery_json_path": recovery_json_path if Path(recovery_json_path).exists() else "",
                "manual_recovery_rerun_urls_path": recovery_urls_path or "",
            }
        )
    items.sort(key=lambda item: str(item.get("run_id") or ""), reverse=True)
    return items[: max(1, limit)]
