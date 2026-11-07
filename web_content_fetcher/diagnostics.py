from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

from .html_extract import IGNORED_TEXT_TAGS, Node, iter_nodes, parse_html


SPA_SHELL_PATTERNS = [
    "doesn't work properly without javascript enabled",
    "does not work properly without javascript enabled",
    "please enable javascript",
    "enable javascript to continue",
]
LOGIN_PATTERNS = [
    "#/login",
    "/login",
    "login",
    "sign in",
    "扫码登录",
    "二维码登录",
    "登录后",
    "请登录",
]
ENDPOINT_KEYWORDS = (
    "/api",
    "/wctapi",
    "/goods",
    "/orders",
    "/activity_packages",
    "/permissions",
    "/permission",
)
SENSITIVE_KEY_RE = re.compile(
    r"(token|cookie|authorization|session|secret|password|phone|mobile|tel|email|order|pay|id_?card|identity)",
    re.I,
)
SCRIPT_SRC_RE = re.compile(r"<script\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.I)
QUOTED_ENDPOINT_RE = re.compile(r"[\"'`]((?:https?://[^\"'`\\\s<>]+)|(?:/[^\"'`\\\s<>]+))[\"'`]", re.I)
FETCH_ENDPOINT_RE = re.compile(r"\b(?:fetch|axios\.(?:get|post|put|delete)|request)\s*\(\s*[\"'`]([^\"'`]+)[\"'`]", re.I)


def preflight_archive_dir(archive_dir: str) -> dict[str, Any]:
    if not archive_dir:
        return {"ok": True, "checked": False}
    root = Path(archive_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".web_content_fetcher_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"ok": True, "checked": True, "path": str(root)}
    except Exception as exc:
        return {
            "ok": False,
            "checked": True,
            "path": str(root),
            "error_code": "archive_dir_not_writable",
            "error": f"{type(exc).__name__}:{str(exc)[:300]}",
        }


def detect_spa_shell(raw: str, *, extracted_text: str = "", links: Iterable[str] | None = None) -> dict[str, Any]:
    lower = (raw or "").lower()
    reasons: list[str] = []
    for pattern in SPA_SHELL_PATTERNS:
        if pattern in lower:
            reasons.append(f"spa shell phrase detected: {pattern}")
    if re.search(r"<noscript\b", raw or "", re.I):
        reasons.append("noscript tag detected")
    if re.search(r"<div\b[^>]+id=[\"'](?:app|root|__next)[\"'][^>]*>\s*</div>", raw or "", re.I | re.S):
        reasons.append("empty SPA root container detected")
    script_count = len(re.findall(r"<script\b", raw or "", re.I))
    text_chars = len(" ".join((extracted_text or "").split()))
    if script_count >= 3 and text_chars < 200:
        reasons.append(f"short extracted text with many scripts: text_chars={text_chars}, script_count={script_count}")
    js_links = [str(link) for link in (links or []) if str(link).lower().endswith(".js")]
    if len(js_links) >= 3 and text_chars < 200:
        reasons.append(f"short extracted text with many JS links: text_chars={text_chars}, js_links={len(js_links)}")
    return {
        "detected": bool(reasons),
        "reasons": list(dict.fromkeys(reasons)),
        "script_count": script_count,
        "text_chars": text_chars,
        "recommendation": "try browser/profile rendering or run endpoint discovery dry-run for official frontend API candidates",
    }


def detect_login_redirect(raw: str, *, requested_url: str = "", final_url: str = "", title: str = "") -> dict[str, Any]:
    reasons: list[str] = []
    parsed_final = urllib.parse.urlsplit(final_url or "")
    route = f"{parsed_final.path}/{parsed_final.fragment}".lower()
    if re.search(r"(?:^|/)(?:login|signin|sign-in)(?:[/?.#;]|$)", route):
        reasons.append("final URL has a login route")
    if re.search(r"^(?:login|sign in|log in|登录|扫码登录)(?:\s|[-_|：:]|$)", title.strip(), re.I):
        reasons.append("login page title detected")

    # Attribute names, scripts and a site's navigation login link are not access gates.
    root = parse_html(raw)
    def visible(node: Node) -> str:
        if node.tag in IGNORED_TEXT_TAGS | {"head", "nav", "header", "footer", "a"}:
            return ""
        if "hidden" in node.attrs or node.attr("aria-hidden").lower() == "true":
            return ""
        if re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", node.attr("style"), re.I):
            return ""
        return " ".join(visible(part) if isinstance(part, Node) else str(part) for part in node.contents)

    text = " ".join(visible(root).split())
    prompt = re.search(r"扫码登录|二维码登录|登录后|请登录|please (?:log|sign) in", text, re.I)
    if prompt and len(text) < 800:
        reasons.append("short page with a visible login requirement")
    for form in (n for n in iter_nodes(root) if n.tag == "form"):
        if visible(form).strip() and any(n.tag == "input" and (n.attr("type").lower() == "password" or n.attr("name").lower() == "password") for n in iter_nodes(form)):
            reasons.append("visible password form detected")
            break
    return {
        "detected": bool(reasons),
        "reasons": list(dict.fromkeys(reasons)),
        "recommendation": "use an authorized browser profile, official API/export, or stop if access is not available",
    }


def detect_document_mismatch(requested_url: str, final_url: str) -> dict[str, Any]:
    """Stable document IDs must not silently become a different Tencent document."""
    requested = urllib.parse.urlsplit(requested_url)
    final = urllib.parse.urlsplit(final_url)
    pattern = r"/document/product/(\d+)/(\d+)/?"
    expected = re.fullmatch(pattern, requested.path)
    checked = requested.hostname == "cloud.tencent.com" and expected is not None
    actual = re.fullmatch(pattern, final.path)
    mismatch = bool(checked and final_url and (final.hostname != requested.hostname or not actual or actual.groups() != expected.groups()))
    return {"checked": checked, "detected": mismatch,
            "reason": "requested_document_identity_changed" if mismatch else "",
            "recommendation": "Verify the requested document manually; preserve the redirected page separately."}


def normalize_browser_error(error: str) -> dict[str, Any]:
    value = str(error or "")
    lower = value.lower()
    code = "browser_error"
    if "user data directory is already in use" in lower or "profile" in lower and "use" in lower:
        code = "profile_locked_or_in_use"
    elif "no such file" in lower or "cannot find" in lower or "not found" in lower:
        code = "profile_missing"
    elif "target page" in lower and "closed" in lower or "context" in lower and "closed" in lower:
        code = "persistent_context_closed"
    elif "timeout" in lower:
        code = "wait_strategy_timeout"
    elif "login" in lower or "#/login" in lower:
        code = "login_redirect"
    return {"code": code, "message": value[:300]}


def script_urls_from_html(html: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for match in SCRIPT_SRC_RE.finditer(html or ""):
        src = match.group(1).strip()
        if src:
            urls.append(urllib.parse.urljoin(base_url, src))
    return list(dict.fromkeys(urls))


def discover_endpoint_candidates(sources: list[dict[str, Any]], *, page_url: str = "") -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        text = str(source.get("content") or "")
        source_url = str(source.get("source_url") or page_url or "")
        source_type = str(source.get("source_type") or "unknown")
        for regex, method_hint in ((FETCH_ENDPOINT_RE, "request_call"), (QUOTED_ENDPOINT_RE, "quoted_string")):
            for match in regex.finditer(text):
                endpoint = match.group(1).strip()
                if not endpoint or not _endpoint_has_keyword(endpoint):
                    continue
                absolute = urllib.parse.urljoin(page_url or source_url, endpoint)
                key = (absolute, source_url)
                if key in seen:
                    continue
                seen.add(key)
                keyword = _matched_keyword(endpoint)
                candidates.append(
                    {
                        "endpoint": absolute,
                        "raw_endpoint": endpoint,
                        "source_url": source_url,
                        "source_type": source_type,
                        "matched_keyword": keyword,
                        "method_guess": _method_guess(text, match.start(), method_hint),
                        "confidence": _endpoint_confidence(endpoint, source_type, method_hint),
                        "requires_auth_guess": _requires_auth_guess(endpoint),
                        "access_policy": "candidate_only_not_fetched",
                    }
                )
    candidates.sort(key=lambda item: (-(int(item.get("confidence") or 0)), str(item.get("endpoint") or "")))
    return candidates


def endpoint_candidates_markdown(candidates: list[dict[str, Any]], *, page_url: str = "") -> str:
    lines = ["# Endpoint Discovery", "", f"Source page: {page_url}", "", "Default policy: candidates only; business endpoints were not fetched.", ""]
    if not candidates:
        lines.extend(["No endpoint candidates found.", ""])
        return "\n".join(lines)
    lines.extend(["| # | Endpoint | Keyword | Confidence | Source | Auth guess |", "|---:|---|---|---:|---|---|"])
    for index, item in enumerate(candidates, 1):
        lines.append(
            "| {idx} | `{endpoint}` | `{keyword}` | {confidence} | {source_type} | {auth} |".format(
                idx=index,
                endpoint=str(item.get("endpoint") or "").replace("|", "%7C"),
                keyword=str(item.get("matched_keyword") or ""),
                confidence=int(item.get("confidence") or 0),
                source_type=str(item.get("source_type") or ""),
                auth="yes" if item.get("requires_auth_guess") else "unknown/no",
            )
        )
    lines.append("")
    return "\n".join(lines)


def parse_json_response(raw: str) -> tuple[Any | None, str]:
    try:
        return json.loads(raw), ""
    except Exception as exc:
        return None, f"{type(exc).__name__}:{str(exc)[:200]}"


def summarize_json_response(raw: str, *, url: str = "") -> dict[str, Any]:
    parsed, error = parse_json_response(raw)
    if error:
        return {"is_json": False, "error": error}
    key_paths: list[dict[str, Any]] = []
    redaction_warnings: list[str] = []
    _walk_json(parsed, key_paths, redaction_warnings, path="$", depth=0)
    top_level = list(parsed.keys()) if isinstance(parsed, dict) else []
    pretty = json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=False)
    summary = {
        "is_json": True,
        "url": url,
        "top_level_keys": top_level[:100],
        "key_path_count": len(key_paths),
        "key_paths": key_paths[:250],
        "redaction_warnings": list(dict.fromkeys(redaction_warnings))[:100],
        "pretty_chars": len(pretty),
    }
    return {**summary, "pretty_json": pretty, "summary_markdown": json_summary_markdown(summary)}


def redact_sensitive_json(value: Any) -> Any:
    """Return a copy safe for archive metadata; do not apply this to page content."""
    if isinstance(value, dict):
        safe: dict[Any, Any] = {}
        for key, child in value.items():
            safe[key] = "<redacted>" if SENSITIVE_KEY_RE.search(str(key)) else redact_sensitive_json(child)
        return safe
    if isinstance(value, list):
        return [redact_sensitive_json(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive_json(item) for item in value]
    if isinstance(value, str):
        return _redact_sensitive_query_values(value)
    return value


def _redact_sensitive_query_values(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.netloc or not parsed.query:
        return value
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if not any(SENSITIVE_KEY_RE.search(key) for key, _ in query):
        return value
    safe_query = [(key, "<redacted>" if SENSITIVE_KEY_RE.search(key) else item) for key, item in query]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(safe_query), parsed.fragment))


def json_summary_markdown(summary: dict[str, Any]) -> str:
    lines = ["# JSON API Response Summary", "", f"Source URL: {summary.get('url', '')}", ""]
    keys = summary.get("top_level_keys") or []
    lines.append("## Top-level keys")
    lines.append("")
    lines.extend(f"- `{key}`" for key in keys) if keys else lines.append("- none")
    warnings = summary.get("redaction_warnings") or []
    lines.extend(["", "## Redaction warnings", ""])
    lines.extend(f"- {item}" for item in warnings) if warnings else lines.append("- none")
    lines.extend(["", "## Key paths", "", "| Path | Type | Size/Sample |", "|---|---|---|"])
    for item in (summary.get("key_paths") or [])[:80]:
        sample = str(item.get("sample") or item.get("size") or "").replace("|", "\\|")
        lines.append(f"| `{item.get('path', '')}` | {item.get('type', '')} | {sample[:120]} |")
    lines.append("")
    return "\n".join(lines)


def _walk_json(value: Any, key_paths: list[dict[str, Any]], warnings: list[str], *, path: str, depth: int) -> None:
    if depth > 5:
        return
    if isinstance(value, dict):
        key_paths.append({"path": path, "type": "object", "size": len(value)})
        for key, child in list(value.items())[:80]:
            child_path = f"{path}.{key}"
            if SENSITIVE_KEY_RE.search(str(key)):
                warnings.append(f"sensitive-looking key path: {child_path}")
            _walk_json(child, key_paths, warnings, path=child_path, depth=depth + 1)
    elif isinstance(value, list):
        key_paths.append({"path": path, "type": "array", "size": len(value)})
        if value:
            _walk_json(value[0], key_paths, warnings, path=f"{path}[0]", depth=depth + 1)
    else:
        sample = str(value)
        if SENSITIVE_KEY_RE.search(path):
            sample = "<redacted-sample>"
        key_paths.append({"path": path, "type": type(value).__name__, "sample": sample[:160]})


def _endpoint_has_keyword(endpoint: str) -> bool:
    lower = endpoint.lower()
    return any(keyword in lower for keyword in ENDPOINT_KEYWORDS)


def _matched_keyword(endpoint: str) -> str:
    lower = endpoint.lower()
    for keyword in ENDPOINT_KEYWORDS:
        if keyword in lower:
            return keyword
    return ""


def _endpoint_confidence(endpoint: str, source_type: str, method_hint: str) -> int:
    score = 50
    if source_type == "script":
        score += 15
    if method_hint == "request_call":
        score += 20
    if endpoint.startswith("http"):
        score += 5
    if _matched_keyword(endpoint) in {"/wctapi", "/activity_packages", "/permissions"}:
        score += 10
    return min(score, 95)


def _method_guess(text: str, start: int, fallback: str) -> str:
    prefix = text[max(0, start - 40):start].lower()
    for method in ("get", "post", "put", "delete"):
        if f".{method}" in prefix or f"method:{method}" in prefix or f"method: '{method}'" in prefix or f'method: "{method}"' in prefix:
            return method.upper()
    return "GET" if fallback == "quoted_string" else "UNKNOWN"


def _requires_auth_guess(endpoint: str) -> bool:
    lower = endpoint.lower()
    return any(part in lower for part in ("order", "permission", "profile", "user", "account", "buy", "pay"))
