#!/usr/bin/env python3
"""Run real-page smoke checks for web-content-fetcher.

This script intentionally lives outside unit tests. Public pages change,
rate-limit, and occasionally block automated access; the goal is to record
what worked, what failed, and whether required cases still satisfy the local
contract.

The operator supplies an explicit configuration file with ``--config``. The
JSON report and Markdown summary are diagnostic, operator-local artifacts: they
may contain the checked URLs, the child command line, stderr excerpts and
archive paths. They are not published automatically and they are not claimed to
be scrubbed. Running the helper only describes what happened for the supplied
configuration; it is not a network-permission or site-policy grant.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "output" / "real_page_regression.json"
DEFAULT_TIMEOUT = 20
DEFAULT_CACHE_MAX_AGE = 900
DEFAULT_FETCH_PROFILE = "generic"
CHILD_TIMEOUT_MARGIN = 45
REPORT_STDERR_TAIL = 500
REPORT_STDOUT_PREVIEW = 500

_BOOL_FIELDS = ("required", "use_web_access", "prefer_browser", "allow_status_fail")
_NUMERIC_FIELDS = (
    ("timeout_seconds", 1),
    ("cache_max_age_seconds", 0),
    ("min_extracted_chars", 0),
    ("min_selector_results", 0),
)
_FORBIDDEN_ID_CHARACTERS = frozenset('/\\:<>"|?*')
_WINDOWS_RESERVED_BASENAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {"com%d" % index for index in range(1, 10)}
    | {"lpt%d" % index for index in range(1, 10)}
)


class ConfigError(ValueError):
    """Raised when an operator config or CLI value violates the smoke contract."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme.lower() in {"http", "https"} and bool(parts.hostname)


def _validate_case_id(case_id: Any, where: str) -> None:
    if not isinstance(case_id, str) or not case_id:
        raise ConfigError(f"{where} must be a non-empty string")
    if case_id != case_id.strip():
        raise ConfigError(f"{where} must not start or end with whitespace")
    for char in case_id:
        if char in _FORBIDDEN_ID_CHARACTERS or unicodedata.category(char) == "Cc":
            raise ConfigError(f"{where} contains a forbidden character: {char!r}")
    if case_id in {".", ".."}:
        raise ConfigError(f"{where} must not be a dot segment")
    if case_id.endswith("."):
        raise ConfigError(f"{where} must not end with a dot")
    basename = case_id.split(".", 1)[0].rstrip(" ")
    if basename.casefold() in _WINDOWS_RESERVED_BASENAMES:
        raise ConfigError(f"{where} is a reserved Windows basename")


def _validate_selectors(selectors: Any, where: str) -> None:
    if not isinstance(selectors, list):
        raise ConfigError(f"{where} must be a list")
    for index, item in enumerate(selectors):
        item_where = f"{where}[{index}]"
        if isinstance(item, str):
            if not item.strip():
                raise ConfigError(f"{item_where} must be a non-empty selector string")
            continue
        if isinstance(item, dict):
            selector = item.get("selector")
            if not isinstance(selector, str) or not selector.strip():
                raise ConfigError(
                    f"{item_where} must be an object with a non-empty string 'selector'"
                )
            continue
        raise ConfigError(f"{item_where} must be a selector string or an object with 'selector'")


def validate_config(config: Any) -> list[dict[str, Any]]:
    """Validate the whole operator config before any case runs.

    Returns the list of case objects unchanged so callers can preserve the
    ordinary run time behaviour. Empty batches, malformed shapes, invalid
    numeric/boolean field types and archive-id collisions are rejected here
    rather than silently producing a green report.
    """

    if not isinstance(config, dict):
        raise ConfigError("config must be a JSON object")
    cases = config.get("cases")
    if not isinstance(cases, list):
        raise ConfigError("config.cases must be a list")
    if not cases:
        raise ConfigError("config.cases must contain at least one case")
    seen_ids: dict[str, str] = {}
    for index, case in enumerate(cases):
        where = f"config.cases[{index}]"
        if not isinstance(case, dict):
            raise ConfigError(f"{where} must be an object")
        case_id = case.get("id")
        _validate_case_id(case_id, f"{where}.id")
        collision_key = unicodedata.normalize("NFC", case_id).casefold()
        if collision_key in seen_ids:
            raise ConfigError(
                f"{where}.id {case_id!r} collides with {seen_ids[collision_key]!r}"
            )
        seen_ids[collision_key] = case_id
        if not _is_http_url(case.get("url")):
            raise ConfigError(f"{where}.url must be an absolute http(s) URL")
        for field in _BOOL_FIELDS:
            if field in case and not isinstance(case[field], bool):
                raise ConfigError(f"{where}.{field} must be a boolean")
        for field, minimum in _NUMERIC_FIELDS:
            if field in case:
                value = case[field]
                if not _is_int(value):
                    raise ConfigError(f"{where}.{field} must be an integer")
                if value < minimum:
                    raise ConfigError(f"{where}.{field} must be >= {minimum}")
        if "selectors" in case:
            _validate_selectors(case["selectors"], f"{where}.selectors")
    return cases


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _tail(value: Any, limit: int = REPORT_STDERR_TAIL) -> str:
    if not isinstance(value, str):
        return ""
    return value[-limit:]


def _preview(value: Any, limit: int = REPORT_STDOUT_PREVIEW) -> str:
    if not isinstance(value, str):
        return ""
    return value[:limit]


def _case_timeout(case: dict[str, Any], timeout: int) -> int:
    if "timeout_seconds" in case:
        return case["timeout_seconds"]
    return timeout


def _case_cache_max_age(case: dict[str, Any]) -> int:
    if "cache_max_age_seconds" in case:
        return case["cache_max_age_seconds"]
    return DEFAULT_CACHE_MAX_AGE


def run_case(case: dict[str, Any], *, timeout: int, archive_root: Path | None) -> dict[str, Any]:
    effective_timeout = _case_timeout(case, timeout)
    command = [
        sys.executable,
        "-m",
        "web_content_fetcher",
        "fetch",
        str(case["url"]),
        "--format",
        "json",
        "--include-raw",
        "--fetch-profile",
        str(case.get("fetch_profile") or DEFAULT_FETCH_PROFILE),
        "--timeout",
        str(effective_timeout),
        "--cache-dir",
        str(ROOT / "output" / "real_page_cache"),
        "--cache-max-age",
        str(_case_cache_max_age(case)),
    ]
    if archive_root:
        command.extend(["--archive-dir", str(archive_root / str(case["id"]))])
    if case.get("use_web_access"):
        command.append("--use-web-access")
    if case.get("prefer_browser"):
        command.append("--prefer-browser")
    for selector in case.get("selectors") or []:
        value = selector.get("selector") if isinstance(selector, dict) else selector
        if value:
            command.extend(["--selector", str(value)])

    started = time.monotonic()
    try:
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=effective_timeout + CHILD_TIMEOUT_MARGIN,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "id": case.get("id"),
            "url": case.get("url"),
            "ok": False,
            "required": bool(case.get("required")),
            "duration_seconds": round(time.monotonic() - started, 3),
            "failure": f"TimeoutExpired: {str(exc)[:240]}",
            "command": command,
        }
    except UnicodeDecodeError as exc:
        return {
            "id": case.get("id"),
            "url": case.get("url"),
            "ok": False,
            "required": bool(case.get("required")),
            "duration_seconds": round(time.monotonic() - started, 3),
            "failure": f"utf8:{type(exc).__name__}:{str(exc)[:200]}",
            "command": command,
        }

    duration = round(time.monotonic() - started, 3)
    body, response_failure = _load_response_body(getattr(completed, "stdout", None))
    if response_failure is not None:
        return {
            "id": case.get("id"),
            "url": case.get("url"),
            "ok": False,
            "required": bool(case.get("required")),
            "duration_seconds": duration,
            "returncode": getattr(completed, "returncode", None),
            "failure": response_failure,
            "stderr": _tail(getattr(completed, "stderr", None)),
            "stdout_preview": _preview(getattr(completed, "stdout", None)),
            "command": command,
        }

    checks = evaluate_case(case, body, completed.returncode)
    archive = body.get("archive")
    archive_path = archive.get("archive_path") if isinstance(archive, dict) else None
    return {
        "id": case.get("id"),
        "url": case.get("url"),
        "ok": checks["ok"],
        "required": bool(case.get("required")),
        "duration_seconds": duration,
        "returncode": completed.returncode,
        "status": body.get("status"),
        "transport": body.get("transport"),
        "final_url": body.get("final_url"),
        "title": body.get("title"),
        "extracted_chars": len(str(body.get("extracted_text") or "")),
        "selector_results": len(body.get("selector_results") or []),
        "site_profile": (body.get("site_data") or {}).get("site_profile"),
        "raw_sha256": body.get("raw_sha256"),
        "extracted_sha256": body.get("extracted_sha256"),
        "archive_path": archive_path,
        "errors": body.get("errors") or [],
        "stderr": _tail(getattr(completed, "stderr", None))
        if completed.returncode not in {0, 2}
        else "",
        "check_failures": checks["failures"],
        "notes": case.get("notes", ""),
    }


def _load_response_body(stdout: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Return a validated response object or a controlled failure marker."""

    if not isinstance(stdout, str):
        return None, f"stdout-type:{type(stdout).__name__}"
    try:
        body = json.loads(stdout)
    except ValueError as exc:
        return None, f"json:{type(exc).__name__}:{str(exc)[:200]}"
    if not isinstance(body, dict):
        return None, f"json-shape:{type(body).__name__}"
    extracted_text = body.get("extracted_text")
    if extracted_text is not None and not isinstance(extracted_text, str):
        return None, f"extracted_text-shape:{type(extracted_text).__name__}"
    site_data = body.get("site_data")
    if site_data is not None and not isinstance(site_data, dict):
        return None, f"site_data-shape:{type(site_data).__name__}"
    selector_results = body.get("selector_results")
    if selector_results is not None and not isinstance(selector_results, list):
        return None, f"selector_results-shape:{type(selector_results).__name__}"
    return body, None


def evaluate_case(case: dict[str, Any], body: dict[str, Any], returncode: int) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {"ok": False, "failures": ["response-not-object"]}
    failures: list[str] = []
    status = body.get("status")
    if returncode not in {0, 2}:
        failures.append(f"returncode={returncode}")
    if status != "ok" and not case.get("allow_status_fail"):
        failures.append(f"status={status}")
    min_chars = case.get("min_extracted_chars") or 0
    if min_chars and len(str(body.get("extracted_text") or "")) < min_chars:
        failures.append(f"extracted_chars<{min_chars}")
    min_selectors = case.get("min_selector_results") or 0
    selector_results = body.get("selector_results")
    selector_count = len(selector_results) if isinstance(selector_results, list) else 0
    if min_selectors and selector_count < min_selectors:
        failures.append(f"selector_results<{min_selectors}")
    expected_profile = str(case.get("expect_site_profile") or "").strip()
    if expected_profile:
        site_data = body.get("site_data")
        actual = site_data.get("site_profile") if isinstance(site_data, dict) else None
        if actual != expected_profile:
            failures.append(f"site_profile={actual!r} expected={expected_profile!r}")
    return {"ok": not failures, "failures": failures}


def _md_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text.replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Real Page Regression - {_md_cell(report['generated_at'])}",
        "",
        f"- Summary: {_md_cell(report['summary'])}",
        f"- Strict mode: {report['strict']}",
        "",
        "| Case | OK | Transport | Chars | Selectors | Notes |",
        "|---|---:|---|---:|---:|---|",
    ]
    for row in report["cases"]:
        ok = "yes" if row.get("ok") else "no"
        notes = row.get("notes", "")
        if row.get("check_failures"):
            notes = f"{notes} Failures: {', '.join(str(item) for item in row['check_failures'])}"
        lines.append(
            f"| {_md_cell(row.get('id'))} | {ok} | {_md_cell(row.get('transport'))} | "
            f"{row.get('extracted_chars') or 0} | {row.get('selector_results') or 0} | "
            f"{_md_cell(notes)} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run real-page smoke checks.")
    parser.add_argument(
        "--config",
        required=True,
        help="Explicit operator JSON config describing the cases to check.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N cases.")
    parser.add_argument("--strict", action="store_true", help="Fail on optional case failures too.")
    parser.add_argument("--no-archive", action="store_true")
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        parser.error("--timeout must be a positive integer")
    if args.limit < 0:
        parser.error("--limit must be >= 0")

    config_path = Path(args.config)
    try:
        config = load_json(config_path)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"error: invalid config JSON: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    try:
        cases = validate_config(config)
    except ConfigError as exc:
        print(f"error: invalid config: {exc}", file=sys.stderr)
        return 2

    if args.limit:
        cases = cases[: args.limit]
    archive_root = None if args.no_archive else ROOT / "output" / "real_page_archive"
    rows = [run_case(case, timeout=args.timeout, archive_root=archive_root) for case in cases]
    failed_required = [row for row in rows if row.get("required") and not row.get("ok")]
    failed_optional = [row for row in rows if not row.get("required") and not row.get("ok")]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path.resolve()),
        "strict": bool(args.strict),
        "summary": {
            "total": len(rows),
            "ok": sum(1 for row in rows if row.get("ok")),
            "failed_required": len(failed_required),
            "failed_optional": len(failed_optional),
        },
        "cases": rows,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    output.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8", newline="\n")
    print(json.dumps(report["summary"], ensure_ascii=False))
    if failed_required or (args.strict and failed_optional):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
