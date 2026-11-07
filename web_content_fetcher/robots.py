from __future__ import annotations

import urllib.parse
import urllib.robotparser


def robots_url_for(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))


def robots_hint(url: str, user_agent: str = "*", *, timeout_seconds: int = 8) -> dict:
    """Return a compliance hint without enforcing a hard block.

    The fetcher is a low-level utility. Downstream projects decide whether a
    robots warning should stop collection. We still expose the hint so audit
    logs can distinguish "technically fetched" from "safe to automate".
    """
    robots_url = robots_url_for(url)
    if not robots_url:
        return {"checked": False, "allowed": None, "robots_url": "", "warning": "invalid_url"}
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        # RobotFileParser does not expose timeout; this remains best-effort and
        # intentionally non-blocking at the policy layer.
        parser.read()
        allowed = parser.can_fetch(user_agent or "*", url)
        return {
            "checked": True,
            "allowed": bool(allowed),
            "robots_url": robots_url,
            "warning": "" if allowed else "robots_disallow_hint",
        }
    except Exception as exc:
        return {
            "checked": False,
            "allowed": None,
            "robots_url": robots_url,
            "warning": f"robots_check_failed:{type(exc).__name__}:{str(exc)[:120]}",
        }

