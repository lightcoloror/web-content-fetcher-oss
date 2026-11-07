from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .quality import inspect_quality_run, make_rerun_plan


SAFE_BOUNDARY_NOTE = (
    "This recovery pack only uses authorized local browser state, official API/export paths, "
    "manual export, or local visual/OCR recovery. It does not bypass login, paywalls, CAPTCHA, "
    "risk controls, or access permissions."
)


def _site_hint(url: str) -> str:
    lowered = url.lower()
    if "feishu.cn" in lowered or "larksuite.com" in lowered:
        return "Feishu/Lark"
    if "yuque.com" in lowered:
        return "Yuque"
    if "mp.weixin.qq.com" in lowered:
        return "WeChat MP"
    if "kdocs.cn" in lowered:
        return "KDocs"
    if "zsxq.com" in lowered:
        return "ZSXQ"
    if "xiaohongshu.com" in lowered or "xhslink.com" in lowered:
        return "Xiaohongshu"
    return "generic"


def safe_steps_for_strategy(strategy: str, url: str) -> list[str]:
    site = _site_hint(url)
    steps = [
        "Open the existing archive first: preferred_readable.html, preferred_markdown.md, image_gallery.html, screenshot.png, and structured.json.",
    ]
    if strategy == "browser":
        steps.extend(
            [
                "Run `python -m web_content_fetcher.cli browser-profile login-all` and manually confirm the page is accessible in the tool-owned browser profile.",
                "Rerun with `--backend browser`; use visible/headful mode for pages that lazy-load content during scrolling.",
                "If the page shows login, permission, CAPTCHA, or risk-control prompts, resolve them manually in the browser or stop.",
            ]
        )
    elif strategy == "feishu_export":
        steps.extend(
            [
                "Prefer official Feishu/Lark export or API credentials when available, then rerun with `--backend feishu_export --compare-feishu-export`.",
                "If official export is unavailable, manually export DOCX/PDF from the logged-in Feishu UI and run archive rebuild or visual recovery on the exported file.",
                "Compare browser and export results; choose the version with more text, images, tables, and headings for preferred views.",
            ]
        )
    elif strategy == "crawl4ai":
        steps.extend(
            [
                "Rerun with `--backend crawl4ai` and, when possible, add a precise wait target or CSS selector for the main content.",
                "Use this only for content that is public or available through your local authorized session; do not upload private content to third-party services.",
                "If the page is still incomplete, switch to manual export or browser visual recovery.",
            ]
        )
    elif strategy == "zsxq-cli":
        steps.extend(
            [
                "Run official `zsxq-cli auth status`; if needed, run `zsxq-cli auth login` and complete login manually.",
                "Use `python -m web_content_fetcher.cli fetch-zsxq --group-id <group> --query <query>` or fetch the known topic through official zsxq-cli data.",
                "If official tools do not expose the content, keep the result as manual review instead of attempting risk-control bypass.",
            ]
        )
    elif strategy == "xhs_browser_state":
        steps.extend(
            [
                "Use a logged-in local browser profile or manual export/screenshot for content you can normally view.",
                "Do not automate CAPTCHA, device-risk challenges, comment/private-message scraping, or platform risk-control bypass.",
                "If HTML extraction is incomplete, rebuild from screenshot/PDF/manual export through the visual/OCR bridge.",
            ]
        )
    else:
        steps.extend(
            [
                "Open the source page manually and verify whether the missing content is visible to you.",
                "If available, export HTML/PDF/DOCX or take screenshots, then run `rebuild-existing-archive` with visual recovery.",
                "If the missing content requires extra permission, request access or leave the item marked incomplete.",
            ]
        )
    if site != "generic":
        steps.append(f"Site hint: {site}.")
    return steps


def render_manual_recovery_markdown(pack: dict[str, Any]) -> str:
    lines = [
        "# Web Content Fetcher Manual Recovery Pack",
        "",
        f"- Status: {pack.get('status', '')}",
        f"- Source run: {pack.get('source_run_path', '')}",
        f"- Total recovery items: {pack.get('total', 0)}",
        f"- Created at: {pack.get('created_at', '')}",
        "",
        SAFE_BOUNDARY_NOTE,
        "",
    ]
    for index, item in enumerate(pack.get("items", []), start=1):
        lines.extend(
            [
                f"## {index}. {item.get('id') or item.get('url') or 'untitled'}",
                "",
                f"- URL: {item.get('url', '')}",
                f"- Strategy: {item.get('strategy', '')}",
                f"- Archive: {item.get('archive_path', '')}",
                f"- Command: `{item.get('command', '')}`" if item.get("command") else "- Command: ",
                "",
                "Failures:",
            ]
        )
        failures = item.get("failures") or []
        if failures:
            for failure in failures:
                lines.append(
                    f"- {failure.get('code', '')}: expected={failure.get('expected', '')} actual={failure.get('actual', '')}"
                )
        else:
            lines.append("- No structured failure details recorded.")
        lines.extend(["", "Safe recovery steps:"])
        for step in item.get("safe_steps") or []:
            lines.append(f"- {step}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def make_manual_recovery_pack(run_path: str | Path, output_dir: str | Path = "") -> dict[str, Any]:
    data = inspect_quality_run(run_path)
    source = Path(run_path)
    source_run_path = source if source.is_dir() else source.parent
    summary_path = source / "smoke_summary.json" if source.is_dir() else source
    target_dir = Path(output_dir) if output_dir else source_run_path / "manual_recovery"
    target_dir.mkdir(parents=True, exist_ok=True)

    plan = make_rerun_plan(data.get("items", []), output_root=str(data.get("output_root", "")))
    items = []
    for item in plan.get("items", []):
        url = str(item.get("url") or "")
        strategy = str(item.get("strategy") or "manual_review")
        items.append(
            {
                "id": item.get("id", ""),
                "url": url,
                "archive_path": item.get("archive_path", ""),
                "strategy": strategy,
                "failures": item.get("failures", []),
                "command": item.get("command", ""),
                "safe_steps": safe_steps_for_strategy(strategy, url),
            }
        )

    pack: dict[str, Any] = {
        "version": 1,
        "status": "ok",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_run_path": str(source_run_path),
        "output_dir": str(target_dir),
        "total": len(items),
        "items": items,
        "urls": [item["url"] for item in items if item.get("url")],
        "safe_boundary_note": SAFE_BOUNDARY_NOTE,
        "paths": {
            "manual_recovery_json_path": str(target_dir / "manual_recovery.json"),
            "manual_recovery_markdown_path": str(target_dir / "manual_recovery.md"),
            "rerun_urls_path": str(target_dir / "rerun-urls.txt"),
        },
    }
    Path(pack["paths"]["manual_recovery_json_path"]).write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    Path(pack["paths"]["manual_recovery_markdown_path"]).write_text(render_manual_recovery_markdown(pack), encoding="utf-8", newline="\n")
    Path(pack["paths"]["rerun_urls_path"]).write_text("\n".join(pack["urls"]).rstrip() + ("\n" if pack["urls"] else ""), encoding="utf-8", newline="\n")
    if summary_path.exists():
        data["manual_recovery"] = {"status": pack["status"], "total": pack["total"], **pack["paths"]}
        report = data.get("quality_report")
        if not isinstance(report, dict):
            report = {}
            data["quality_report"] = report
        report.update(pack["paths"])
        summary_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return pack
