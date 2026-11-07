from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def default_chrome_user_data_dir() -> Path:
    configured = os.getenv("WEB_CONTENT_FETCHER_CHROME_USER_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "Google" / "Chrome" / "User Data"
COMMON_LOGIN_URLS = [
    "https://my.feishu.cn",
    "https://wx.zsxq.com",
    "https://www.yuque.com",
    "https://www.notion.so",
    "https://www.kdocs.cn",
    "https://docs.qq.com",
    "https://www.xiaohongshu.com",
]


@dataclass
class BrowserFetchOptions:
    url: str
    timeout_seconds: int = 30
    headless: bool = False
    profile: str = ""
    user_data_dir: str = ""
    executable_path: str = ""
    storage_state: str = ""
    wait_until: str = "load"
    screenshot_path: str = ""
    html_path: str = ""
    image_assets_dir: str = ""
    download_images: bool = False
    auto_scroll: bool = False
    scroll_steps: int = 8
    settle_ms: int = 700
    viewport_width: int = 1440
    viewport_height: int = 1800
    dismiss_obstructive_overlays: bool = True


@dataclass
class BrowserFetchOutput:
    html: str
    final_url: str
    title: str
    screenshot_path: str = ""
    html_path: str = ""
    image_assets: list[dict[str, Any]] | None = None
    visible_blocks: list[dict[str, Any]] | None = None
    visible_markdown: str = ""
    visible_metrics: dict[str, Any] | None = None


@dataclass
class BrowserProfileLoginOptions:
    url: str = "about:blank"
    urls: list[str] | None = None
    timeout_seconds: int = 600
    user_data_dir: str = ""
    executable_path: str = ""
    profile: str = ""
    viewport_width: int = 1440
    viewport_height: int = 1800


def default_profile_dir() -> Path:
    return Path.home() / ".web-content-fetcher" / "browser-profile"


def browser_user_data_dir(options: BrowserFetchOptions) -> str:
    if options.user_data_dir:
        return options.user_data_dir
    if options.profile:
        return str(default_chrome_user_data_dir())
    return str(default_profile_dir())


def _iso_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except Exception:
        return ""


def browser_profile_status(user_data_dir: str = "", *, profile: str = "") -> dict[str, Any]:
    root = Path(user_data_dir or default_profile_dir()).expanduser()
    profile_path = root / profile if profile else root
    cookies: list[dict[str, Any]] = []
    local_storage_paths: list[str] = []
    preferences_paths: list[str] = []
    newest_mtime = 0.0
    newest_path = ""
    if root.exists():
        candidates = [
            profile_path / "Cookies",
            profile_path / "Network" / "Cookies",
            root / "Default" / "Cookies",
            root / "Default" / "Network" / "Cookies",
        ]
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen or not candidate.exists() or not candidate.is_file():
                continue
            seen.add(key)
            stat = candidate.stat()
            cookies.append({"path": key, "bytes": stat.st_size, "modified_at": _iso_mtime(candidate)})
        for candidate in (profile_path / "Local Storage", root / "Default" / "Local Storage"):
            if candidate.exists():
                local_storage_paths.append(str(candidate))
        for candidate in (profile_path / "Preferences", root / "Default" / "Preferences"):
            if candidate.exists() and candidate.is_file():
                preferences_paths.append(str(candidate))
        try:
            for item in root.rglob("*"):
                try:
                    stat = item.stat()
                except Exception:
                    continue
                if stat.st_mtime > newest_mtime:
                    newest_mtime = stat.st_mtime
                    newest_path = str(item)
        except Exception:
            pass
    has_cookies = any(int(item.get("bytes") or 0) > 0 for item in cookies)
    status = "ready" if root.exists() and has_cookies else "no_cookies" if root.exists() else "missing"
    return {
        "status": status,
        "user_data_dir": str(root),
        "profile": profile,
        "profile_path": str(profile_path),
        "exists": root.exists(),
        "profile_path_exists": profile_path.exists(),
        "modified_at": datetime.fromtimestamp(newest_mtime, timezone.utc).isoformat() if newest_mtime else "",
        "latest_modified_path": newest_path,
        "cookies_db_count": len(cookies),
        "has_cookies_db": has_cookies,
        "cookies_databases": cookies,
        "local_storage_paths": local_storage_paths,
        "preferences_paths": preferences_paths,
        "possibly_anonymous": not has_cookies,
        "login_command": f'python -m web_content_fetcher.cli browser-profile login --user-data-dir "{root}"',
        "login_all_command": f'python -m web_content_fetcher.cli browser-profile login-all --user-data-dir "{root}"',
        "note": "Only profile metadata is inspected; cookie values are not read.",
    }


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


PAGE_IMAGE_COLLECTOR = """() => {
    const root = document.querySelector('article#content .ne-viewer-body, .yuque-doc-content .ne-viewer-body, main, article, [role="main"]') || document.body;
    if (!root) return [];
    return Array.from(root.querySelectorAll('img')).map((img) => {
    const raw = img.currentSrc || img.src || img.getAttribute('data-src') || img.getAttribute('data-original') || '';
    let url = '';
    try { url = raw ? new URL(raw, location.href).href : ''; } catch (_) {}
    const rect = img.getBoundingClientRect();
    const scrollX = window.scrollX || window.pageXOffset || 0;
    const scrollY = window.scrollY || window.pageYOffset || 0;
    return {
        url,
        alt: img.alt || '',
        title: img.title || '',
        width: img.naturalWidth || img.width || '',
        height: img.naturalHeight || img.height || '',
        viewport_bbox: [rect.left, rect.top, rect.right, rect.bottom].map((value) => Math.round(value * 10) / 10),
        document_bbox: [rect.left + scrollX, rect.top + scrollY, rect.right + scrollX, rect.bottom + scrollY].map((value) => Math.round(value * 10) / 10),
        rendered_width: Math.round(rect.width * 10) / 10,
        rendered_height: Math.round(rect.height * 10) / 10
    };
    }).filter((img) => img.url && !img.url.startsWith('data:'));
}"""


async def _collect_document_anchor_targets(page) -> list[dict[str, str]]:
    targets = await page.evaluate(
        """() => {
            const current = new URL(location.href);
            const seen = new Set();
            const items = [];
            const addTarget = (href, text) => {
                if (!href) return;
                let url;
                try { url = new URL(href, location.href); } catch (_) { return; }
                if (url.origin !== current.origin || url.pathname !== current.pathname || !url.hash) return;
                const fragment = decodeURIComponent(url.hash.slice(1));
                if (!fragment || seen.has(fragment)) return;
                seen.add(fragment);
                items.push({ href: url.href, fragment, text: (text || '').replace(/\\s+/g, ' ').trim() });
            };
            for (const link of Array.from(document.querySelectorAll('a[href*="#"]'))) {
                addTarget(link.getAttribute('href') || link.href, link.innerText || link.textContent || '');
            }
            for (const item of Array.from(document.querySelectorAll('[data-anchor], [data-block-id], [id]'))) {
                const id = item.getAttribute('data-anchor') || item.getAttribute('data-block-id') || item.id || '';
                const text = item.innerText || item.textContent || '';
                if (id && text && text.length < 200) addTarget(`#${id}`, text);
            }
            return items.slice(0, 80);
        }"""
    )
    return [dict(item) for item in targets if item.get("fragment")]


async def _visit_document_anchor_targets(page, *, settle_ms: int) -> dict[str, Any]:
    targets = await _collect_document_anchor_targets(page)
    if not targets:
        return {"anchor_navigation": False, "target_count": 0}

    image_count_samples: list[int] = []
    image_candidates: list[dict[str, Any]] = []
    seen_images: set[str] = set()
    visible_blocks: list[dict[str, Any]] = []
    seen_blocks: set[str] = set()
    visited: list[dict[str, Any]] = []

    async def remember_images() -> None:
        page_images = await page.evaluate(PAGE_IMAGE_COLLECTOR)
        image_count_samples.append(len(page_images))
        for image in page_images:
            url = str(image.get("url") or "")
            if url and url not in seen_images:
                seen_images.add(url)
                image_candidates.append(dict(image))

    async def remember_visible_blocks() -> int:
        before = len(visible_blocks)
        blocks, _markdown, _metrics = await _collect_visible_blocks(page)
        for block in blocks:
            text = str(block.get("text") or "").strip()
            src = str(block.get("src") or "").strip()
            key = f"{block.get('type') or ''}|{text}|{src}"
            if not text or key in seen_blocks:
                continue
            seen_blocks.add(key)
            visible_blocks.append(dict(block))
        return len(visible_blocks) - before

    await remember_images()
    await remember_visible_blocks()
    for target in targets[:40]:
        fragment = str(target.get("fragment") or "")
        href = str(target.get("href") or "")
        if not fragment:
            continue
        clicked = await page.evaluate(
            """({ href, fragment }) => {
                const escaped = CSS.escape(fragment);
                const link = Array.from(document.querySelectorAll('a[href*="#"]')).find((item) => {
                    try {
                        const url = new URL(item.getAttribute('href') || item.href, location.href);
                        return decodeURIComponent(url.hash.slice(1)) === fragment;
                    } catch (_) {
                        return false;
                    }
                });
                if (link) {
                    link.scrollIntoView({ block: 'center', inline: 'nearest' });
                    link.click();
                    return true;
                }
                const target = document.getElementById(fragment)
                    || document.querySelector(`[data-anchor="${escaped}"], [data-block-id="${escaped}"]`);
                if (target) {
                    target.scrollIntoView({ block: 'start', inline: 'nearest' });
                    return true;
                }
                if (href) location.href = href;
                else location.hash = fragment;
                return false;
            }""",
            {"href": href, "fragment": fragment},
        )
        await page.wait_for_timeout(max(200, settle_ms))
        await page.mouse.wheel(0, 700)
        await page.wait_for_timeout(max(200, settle_ms // 2))
        await remember_images()
        new_blocks = await remember_visible_blocks()
        visited.append(
            {
                "fragment": fragment,
                "text": str(target.get("text") or "")[:120],
                "clicked": bool(clicked),
                "new_block_count": new_blocks,
                "image_count": image_count_samples[-1] if image_count_samples else 0,
            }
        )

    return {
        "anchor_navigation": True,
        "target_count": len(targets),
        "visited_count": len(visited),
        "visited": visited,
        "image_count_initial": image_count_samples[0] if image_count_samples else 0,
        "image_count_final": image_count_samples[-1] if image_count_samples else 0,
        "image_count_max": max(image_count_samples) if image_count_samples else 0,
        "image_count_samples": image_count_samples[:80],
        "image_candidates": image_candidates,
        "image_candidate_count": len(image_candidates),
        "visible_blocks": visible_blocks,
        "visible_block_count": len(visible_blocks),
    }


async def _sweep_scroll_positions(page, *, steps: int, settle_ms: int) -> dict[str, Any]:
    image_count_samples: list[int] = []
    image_candidates: list[dict[str, Any]] = []
    seen_images: set[str] = set()
    visible_blocks: list[dict[str, Any]] = []
    seen_blocks: set[str] = set()
    positions: list[dict[str, Any]] = []

    async def remember_images() -> None:
        page_images = await page.evaluate(PAGE_IMAGE_COLLECTOR)
        image_count_samples.append(len(page_images))
        for image in page_images:
            url = str(image.get("url") or "")
            if url and url not in seen_images:
                seen_images.add(url)
                image_candidates.append(dict(image))

    async def remember_visible_blocks() -> int:
        before = len(visible_blocks)
        blocks, _markdown, _metrics = await _collect_visible_blocks(page)
        for block in blocks:
            text = str(block.get("text") or "").strip()
            src = str(block.get("src") or "").strip()
            key = f"{block.get('type') or ''}|{text}|{src}"
            if not text or key in seen_blocks:
                continue
            seen_blocks.add(key)
            visible_blocks.append(dict(block))
        return len(visible_blocks) - before

    step_count = max(1, steps)
    await remember_images()
    await remember_visible_blocks()
    for index in range(step_count + 1):
        ratio = index / step_count
        position = await page.evaluate(
            """(ratio) => {
                const candidates = [
                    document.scrollingElement,
                    document.documentElement,
                    document.body,
                    ...Array.from(document.querySelectorAll('.lark-docx-page, [class*="scroll"], [class*="Scroll"], [data-qa*="scroll"], main, [role="main"]'))
                ].filter(Boolean);
                let best = null;
                for (const el of candidates) {
                    const scrollHeight = el.scrollHeight || 0;
                    const clientHeight = el.clientHeight || window.innerHeight || 0;
                    const maxScroll = Math.max(0, scrollHeight - clientHeight);
                    if (!best || maxScroll > best.maxScroll) best = { el, maxScroll, scrollHeight, clientHeight };
                }
                const top = best ? Math.floor(best.maxScroll * ratio) : 0;
                window.scrollTo(0, top);
                for (const el of candidates) {
                    const maxScroll = Math.max(0, (el.scrollHeight || 0) - (el.clientHeight || window.innerHeight || 0));
                    if (maxScroll > 0) el.scrollTop = Math.floor(maxScroll * ratio);
                }
                return {
                    ratio,
                    top,
                    maxScroll: best ? best.maxScroll : 0,
                    scrollHeight: best ? best.scrollHeight : 0,
                    clientHeight: best ? best.clientHeight : 0
                };
            }""",
            ratio,
        )
        await page.wait_for_timeout(max(150, settle_ms))
        await page.mouse.wheel(0, 450)
        await page.wait_for_timeout(max(150, settle_ms // 2))
        await remember_images()
        new_blocks = await remember_visible_blocks()
        positions.append({**dict(position or {}), "new_block_count": new_blocks, "image_count": image_count_samples[-1] if image_count_samples else 0})

    return {
        "position_sweep": True,
        "steps_requested": step_count,
        "positions_visited": len(positions),
        "positions": positions[:120],
        "image_count_initial": image_count_samples[0] if image_count_samples else 0,
        "image_count_final": image_count_samples[-1] if image_count_samples else 0,
        "image_count_max": max(image_count_samples) if image_count_samples else 0,
        "image_count_samples": image_count_samples[:120],
        "image_candidates": image_candidates,
        "image_candidate_count": len(image_candidates),
        "visible_blocks": visible_blocks,
        "visible_block_count": len(visible_blocks),
    }




async def _remove_hidden_extraction_noise(page) -> dict[str, Any]:
    """Remove hidden modal/template text that confuses article extraction."""
    return await page.evaluate(
        """() => {
            const noisyTextPattern = /(此产品为私募基金产品|合格投资者|请输入手机号|请输入验证码|微信扫码支付|手机端沪深Lv2购买|购买即视为同意|投诉原因|添加持仓盈亏|举报-非法荐股)/;
            const noisyClassPattern = /(modal|popup|dialog|login|pay|captcha|confirm|toast|tooltip)/i;
            const removed = [];
            for (const el of Array.from(document.querySelectorAll('body *'))) {
                const style = window.getComputedStyle(el);
                const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                const cls = `${el.id || ''} ${el.className || ''}`;
                const hidden = el.hidden || style.display === 'none' || style.visibility === 'hidden' || el.getAttribute('aria-hidden') === 'true';
                if (!hidden) continue;
                if (!(noisyTextPattern.test(text) || noisyClassPattern.test(cls))) continue;
                removed.push({tag: el.tagName.toLowerCase(), className: String(el.className || '').slice(0, 120), text: text.slice(0, 120)});
                el.remove();
                if (removed.length >= 80) break;
            }
            return {enabled: true, removed_count: removed.length, removed: removed.slice(0, 20)};
        }"""
    )

async def _dismiss_obstructive_overlays(page, *, settle_ms: int) -> dict[str, Any]:
    """Close obvious ad/promo overlays that cover page content.

    This intentionally clicks only close-like controls. It does not submit
    login, captcha, payment, consent, qualification, or group-join actions.
    """
    result = await page.evaluate(
        """() => {
            const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
            const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
            const viewportArea = Math.max(1, viewportW * viewportH);
            const closePattern = /^(×|x|X|close|关闭|关闭弹窗|取消|稍后再说|暂不|暂时不用|不再提醒|不用了|我知道了|知道了|跳过|skip)$/i;
            const closeHintPattern = /(close|关闭|取消|稍后|暂不|不用了|不再提醒|跳过|skip)/i;
            const forbiddenPattern = /(登录|注册|验证码|验证|确认|同意|购买|支付|开通|立即添加|进群|扫码|微信扫一扫|合格投资者|已阅读|继续|提交|授权|允许)/i;
            const roots = [];
            const addRoot = (el, reason) => {
                if (!el || roots.some((item) => item.el === el)) return;
                roots.push({el, reason});
            };
            for (const el of Array.from(document.querySelectorAll('body *'))) {
                const style = window.getComputedStyle(el);
                if (style.visibility === 'hidden' || style.display === 'none' || style.pointerEvents === 'none') continue;
                const rect = el.getBoundingClientRect();
                if (rect.width < 80 || rect.height < 40) continue;
                const areaRatio = (rect.width * rect.height) / viewportArea;
                const zIndex = Number.parseInt(style.zIndex || '0', 10) || 0;
                const cls = `${el.id || ''} ${el.className || ''}`.toLowerCase();
                const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                const isLayer = style.position === 'fixed' || style.position === 'sticky' || zIndex >= 100 || /modal|popup|dialog|mask|overlay|advert|ad-|qrcode|qr-code/.test(cls);
                const looksObstructive = areaRatio >= 0.12 || /老股民请进群|高手云集|微信扫一扫|立即添加|私募基金产品|合格投资者|广告|推广/.test(text);
                if (isLayer && looksObstructive) addRoot(el, 'obstructive_layer');
            }
            const globalCloseSelectors = [
                '[aria-label*="关闭"]', '[aria-label*="close" i]', '[title*="关闭"]', '[title*="close" i]',
                '.close', '.modal-close', '.popup-close', '.dialog-close', '.btn-close', '.icon-close',
                '[class*="close" i]', '[class*="modal-close" i]', '[class*="popup-close" i]'
            ];
            for (const selector of globalCloseSelectors) {
                for (const el of Array.from(document.querySelectorAll(selector))) addRoot(el.closest('[role="dialog"], .modal, .popup, .dialog, [class*="modal" i], [class*="popup" i]') || el, `selector:${selector}`);
            }
            const clicked = [];
            const tried = [];
            const candidates = [];
            for (const root of roots.slice(0, 20)) {
                const clickables = Array.from(root.el.querySelectorAll('button, a, [role="button"], [aria-label], [title], [class*="close" i], [class*="cancel" i], [class*="skip" i]'));
                if (root.el.matches && root.el.matches('button, a, [role="button"], [aria-label], [title], [class*="close" i], [class*="cancel" i], [class*="skip" i]')) clickables.unshift(root.el);
                for (const btn of clickables) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) continue;
                    const label = `${btn.getAttribute('aria-label') || ''} ${btn.getAttribute('title') || ''} ${btn.className || ''} ${(btn.innerText || btn.textContent || '').replace(/\\s+/g, ' ').trim()}`.trim();
                    if (!label) continue;
                    candidates.push(label.slice(0, 80));
                    if (forbiddenPattern.test(label)) continue;
                    const textOnly = (btn.innerText || btn.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (!(closePattern.test(textOnly) || closeHintPattern.test(label))) continue;
                    tried.push(label.slice(0, 80));
                    try {
                        btn.click();
                        clicked.push({label: label.slice(0, 80), reason: root.reason});
                        break;
                    } catch (_) {}
                }
                if (clicked.length >= 6) break;
            }
            return {enabled: true, roots: roots.length, clicked_count: clicked.length, clicked, tried, candidates: candidates.slice(0, 20)};
        }"""
    )
    if int(result.get("clicked_count") or 0) > 0:
        await page.wait_for_timeout(max(100, settle_ms))
    return dict(result or {})

async def _auto_scroll(page, *, steps: int, settle_ms: int) -> dict[str, Any]:
    last_height = 0
    heights: list[int] = []
    image_count_samples: list[int] = []
    image_candidates: list[dict[str, Any]] = []
    seen_images: set[str] = set()
    visible_blocks: list[dict[str, Any]] = []
    seen_blocks: set[str] = set()
    steps_run = 0

    async def remember_images() -> None:
        page_images = await page.evaluate(PAGE_IMAGE_COLLECTOR)
        image_count_samples.append(len(page_images))
        for image in page_images:
            url = str(image.get("url") or "")
            if url and url not in seen_images:
                seen_images.add(url)
                image_candidates.append(dict(image))

    async def remember_visible_blocks() -> None:
        blocks, _markdown, _metrics = await _collect_visible_blocks(page)
        for block in blocks:
            text = str(block.get("text") or "").strip()
            src = str(block.get("src") or "").strip()
            key = f"{block.get('type') or ''}|{text}|{src}"
            if not text or key in seen_blocks:
                continue
            seen_blocks.add(key)
            visible_blocks.append(dict(block))

    await remember_images()
    await remember_visible_blocks()
    min_steps_before_break = max(1, steps)
    for _ in range(max(1, steps)):
        height = await page.evaluate(
            """() => {
                const heights = [document.documentElement.scrollHeight || 0, document.body ? (document.body.scrollHeight || 0) : 0];
                for (const el of Array.from(document.querySelectorAll('.lark-docx-page, [class*="scroll"], [class*="Scroll"], [data-qa*="scroll"], main, [role="main"]'))) {
                    heights.push(el.scrollHeight || 0);
                }
                return Math.max(...heights);
            }"""
        )
        heights.append(int(height or 0))
        await page.evaluate(
            """() => {
                const viewport = window.innerHeight || document.documentElement.clientHeight || 900;
                const delta = Math.max(400, Math.floor(viewport * 0.85));
                window.scrollBy(0, delta);
                const candidates = Array.from(document.querySelectorAll('.lark-docx-page, [class*="scroll"], [class*="Scroll"], [data-qa*="scroll"], main, [role="main"]'));
                for (const el of candidates) {
                    if ((el.scrollHeight || 0) > (el.clientHeight || 0) + 20) {
                        el.scrollTop = Math.min((el.scrollTop || 0) + delta, el.scrollHeight || 0);
                    }
                }
            }"""
        )
        await page.mouse.wheel(0, 1200)
        await page.wait_for_timeout(max(100, settle_ms))
        steps_run += 1
        await remember_images()
        await remember_visible_blocks()
        scroll_position = await page.evaluate(
            """() => {
                const positions = [Math.ceil((window.scrollY || window.pageYOffset || 0) + (window.innerHeight || 0))];
                for (const el of Array.from(document.querySelectorAll('.lark-docx-page, [class*="scroll"], [class*="Scroll"], [data-qa*="scroll"], main, [role="main"]'))) {
                    if ((el.scrollHeight || 0) > (el.clientHeight || 0) + 20) {
                        positions.push(Math.ceil((el.scrollTop || 0) + (el.clientHeight || 0)));
                    }
                }
                return Math.max(...positions);
            }"""
        )
        if steps_run >= min_steps_before_break and height == last_height and int(scroll_position or 0) >= int(height or 0):
            break
        last_height = height
    await page.evaluate(
        """() => {
            window.scrollTo(0, 0);
            for (const el of Array.from(document.querySelectorAll('.lark-docx-page, [class*="scroll"], [class*="Scroll"], [data-qa*="scroll"], main, [role="main"]'))) {
                if ((el.scrollHeight || 0) > (el.clientHeight || 0) + 20) el.scrollTop = 0;
            }
        }"""
    )
    await page.wait_for_timeout(200)
    await remember_images()
    await remember_visible_blocks()
    final_height = await page.evaluate(
        """() => {
            const heights = [document.documentElement.scrollHeight || 0, document.body ? (document.body.scrollHeight || 0) : 0];
            for (const el of Array.from(document.querySelectorAll('.lark-docx-page, [class*="scroll"], [class*="Scroll"], [data-qa*="scroll"], main, [role="main"]'))) {
                heights.push(el.scrollHeight || 0);
            }
            return Math.max(...heights);
        }"""
    )
    return {
        "auto_scroll": True,
        "steps_requested": max(1, steps),
        "steps_run": steps_run,
        "initial_height": heights[0] if heights else 0,
        "final_height": int(final_height or 0),
        "height_samples": heights[:20],
        "image_count_initial": image_count_samples[0] if image_count_samples else 0,
        "image_count_final": image_count_samples[-1] if image_count_samples else 0,
        "image_count_max": max(image_count_samples) if image_count_samples else 0,
        "image_count_samples": image_count_samples[:50],
        "image_candidates": image_candidates,
        "image_candidate_count": len(image_candidates),
        "visible_blocks": visible_blocks,
        "visible_block_count": len(visible_blocks),
    }


async def _download_page_images(context, page, output_dir: str, *, timeout_ms: int, candidates: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    images = [dict(image) for image in (candidates or [])]
    images.extend(await page.evaluate(PAGE_IMAGE_COLLECTOR))
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for image in images:
        url = str(image.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        item = dict(image)
        try:
            if url.startswith("blob:"):
                blob = await page.evaluate(
                    """async (url) => {
                        const response = await fetch(url);
                        const blob = await response.blob();
                        const buffer = await blob.arrayBuffer();
                        return {
                            contentType: blob.type || response.headers.get('content-type') || '',
                            bytes: Array.from(new Uint8Array(buffer))
                        };
                    }""",
                    url,
                )
                body = bytes(blob.get("bytes") or [])
                content_type = str(blob.get("contentType") or "")
            else:
                response = await context.request.get(url, timeout=timeout_ms, headers={"referer": page.url})
                if not response.ok:
                    item["asset_error"] = f"HTTP {response.status}"
                    results.append(item)
                    continue
                body = await response.body()
                content_type = response.headers.get("content-type", "")
            if not body:
                item["asset_error"] = "empty image body"
                results.append(item)
                continue
            path = root / _asset_name(url, content_type)
            path.write_bytes(body)
            item.update(
                {
                    "asset_path": str(path),
                    "asset_sha256": hashlib.sha256(body).hexdigest(),
                    "content_type": content_type,
                    "bytes": len(body),
                }
            )
        except Exception as exc:
            item["asset_error"] = f"{type(exc).__name__}:{str(exc)[:200]}"
        results.append(item)
    return results


def _visible_blocks_to_markdown(blocks: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    lines: list[str] = []
    type_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for item in blocks:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        item_type = str(item.get("type") or "")
        source = str(item.get("source") or "semantic")
        type_counts[item_type] = type_counts.get(item_type, 0) + 1
        source_counts[source] = source_counts.get(source, 0) + 1
        if item_type == "heading":
            level = int(item.get("level") or 1)
            lines.append(f"{'#' * max(1, min(6, level))} {text}")
        elif item_type == "list_item":
            lines.append(f"- {text}")
        elif item_type == "blockquote":
            lines.append("\n".join(f"> {line}" for line in text.splitlines() if line.strip()))
        elif item_type == "code":
            lines.append(f"```\n{text}\n```")
        else:
            lines.append(text)
    markdown = "\n\n".join(lines).strip()
    metrics = {
        "block_count": len(blocks),
        "markdown_chars": len(" ".join(markdown.split())),
        "type_counts": type_counts,
        "source_counts": source_counts,
        "leaf_text_block_count": source_counts.get("leaf_text", 0),
    }
    return markdown, metrics


async def _collect_visible_blocks(page) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    blocks = await page.evaluate(
        """() => {
            const root = document.querySelector('article#content .ne-viewer-body, .yuque-doc-content .ne-viewer-body, main, article, [role="main"], .lark-docx-page, .yuque-doc-content') || document.body;
            if (!root) return [];
            const selector = 'h1,h2,h3,h4,h5,h6,[role="heading"],p,li,blockquote,pre,img,figure';
            const leafSelector = '[data-block-id],[data-node-id],[role="paragraph"],[data-qa*="paragraph"],[class*="paragraph"],[class*="Paragraph"],[contenteditable="true"] div,[role="textbox"] div';
            const isVisible = (el) => {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0 && el.tagName.toLowerCase() !== 'img') return false;
                if (rect.top > window.innerHeight * 20 || rect.bottom < -window.innerHeight) return false;
                return true;
            };
            const semanticNodes = Array.from(root.querySelectorAll(selector)).filter(isVisible);
            const semanticSet = new Set(semanticNodes);
            const leafNodes = Array.from(root.querySelectorAll(leafSelector)).filter((el) => {
                if (!isVisible(el) || semanticSet.has(el)) return false;
                if (el.closest(selector)) return false;
                const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                if (!text || text.length > 2000) return false;
                const nestedLeaf = Array.from(el.querySelectorAll(leafSelector)).find((child) => child !== el && isVisible(child));
                if (nestedLeaf) return false;
                const childSemantic = Array.from(el.querySelectorAll(selector)).find((child) => child !== el && isVisible(child));
                if (childSemantic && !el.querySelector('img')) return false;
                return true;
            });
            const nodes = [...semanticNodes, ...leafNodes].sort((a, b) => {
                const ar = a.getBoundingClientRect();
                const br = b.getBoundingClientRect();
                if (Math.abs(ar.top - br.top) > 2) return ar.top - br.top;
                return ar.left - br.left;
            });
            const imageData = (img) => {
                const raw = img.currentSrc || img.src || img.getAttribute('data-src') || '';
                let url = '';
                try { url = raw ? new URL(raw, location.href).href : ''; } catch (_) {}
                if (!url) return null;
                const alt = (img.alt || img.title || 'image').replace(/[\\[\\]]/g, ' ').trim() || 'image';
                return { type: 'image', markdown: `![${alt}](${url})`, src: url, alt };
            };
            const inlineMarkdown = (el) => {
                const parts = [];
                const inlineItems = [];
                let inlineIndex = 0;
                const appendText = (text) => {
                    const value = (text || '').replace(/\\s+/g, ' ').trim();
                    if (!value) return;
                    parts.push(value);
                    inlineItems.push({ type: 'text', inline_index: ++inlineIndex, text: value });
                };
                const walk = (node) => {
                    if (node.nodeType === Node.TEXT_NODE) {
                        appendText(node.textContent || '');
                        return;
                    }
                    if (node.nodeType !== Node.ELEMENT_NODE) return;
                    const tag = node.tagName.toLowerCase();
                    if (['script', 'style', 'noscript', 'template', 'svg', 'button'].includes(tag)) return;
                    if (tag === 'br') {
                        parts.push('\\n');
                        inlineItems.push({ type: 'break', inline_index: ++inlineIndex });
                        return;
                    }
                    if (tag === 'img') {
                        const image = imageData(node);
                        if (image) {
                            parts.push(image.markdown);
                            inlineItems.push({
                                type: 'image',
                                inline_index: ++inlineIndex,
                                text: image.markdown,
                                src: image.src,
                                alt: image.alt
                            });
                        }
                        return;
                    }
                    Array.from(node.childNodes).forEach(walk);
                };
                Array.from(el.childNodes).forEach(walk);
                return {
                    markdown: parts.join(' ').replace(/\\s+\\n\\s+/g, '\\n').replace(/\\s+/g, ' ').trim(),
                    inline_items: inlineItems,
                    image_positions: inlineItems.filter((item) => item.type === 'image')
                };
            };
            const blocks = [];
            const seen = new Set();
            let order = 0;
            for (const el of nodes) {
                const tag = el.tagName.toLowerCase();
                if (tag === 'figure' && el.querySelector('img')) continue;
                if (el.closest(selector) && el.closest(selector) !== el) {
                    const parentTag = el.closest(selector).tagName.toLowerCase();
                    if (parentTag !== 'figure') continue;
                }
                let text = '';
                let type = 'paragraph';
                let level = 0;
                let src = '';
                let alt = '';
                let inline = { markdown: '', inline_items: [], image_positions: [] };
                if (/^h[1-6]$/.test(tag)) {
                    type = 'heading';
                    level = Number(tag.slice(1));
                    inline = inlineMarkdown(el);
                    text = inline.markdown;
                } else if (el.getAttribute('role') === 'heading') {
                    type = 'heading';
                    level = Number(el.getAttribute('aria-level') || 2);
                    inline = inlineMarkdown(el);
                    text = inline.markdown;
                } else if (tag === 'img') {
                    type = 'image';
                    const image = imageData(el);
                    text = image ? image.markdown : '';
                    src = image ? image.src : '';
                    alt = el.alt || el.title || 'image';
                } else if (tag === 'li') {
                    type = 'list_item';
                    inline = inlineMarkdown(el);
                    text = inline.markdown;
                } else if (tag === 'blockquote') {
                    type = 'blockquote';
                    inline = inlineMarkdown(el);
                    text = inline.markdown;
                } else if (tag === 'pre') {
                    type = 'code';
                    text = el.innerText || el.textContent || '';
                } else {
                    inline = inlineMarkdown(el);
                    text = inline.markdown;
                }
                text = (text || '').trim();
                if (!text) continue;
                if (/^\\d+\\s*人点赞\\s*$/.test(text)) break;
                const key = `${type}|${level}|${text}`;
                if (seen.has(key)) continue;
                seen.add(key);
                const item = { order: ++order, type, text, tag };
                if (!semanticSet.has(el)) item.source = 'leaf_text';
                if (level) item.level = level;
                if (el.getAttribute('role')) item.role = el.getAttribute('role');
                if (el.getAttribute('aria-level')) item.aria_level = el.getAttribute('aria-level');
                if (src) item.src = src;
                if (alt) item.alt = alt;
                if (inline.inline_items && inline.inline_items.length) item.inline_items = inline.inline_items;
                if (inline.image_positions && inline.image_positions.length) item.image_positions = inline.image_positions;
                blocks.push(item);
                if (blocks.length >= 500) break;
            }
            return blocks;
        }"""
    )
    markdown, metrics = _visible_blocks_to_markdown(blocks)
    return blocks, markdown, metrics


async def _fetch_with_browser(options: BrowserFetchOptions) -> BrowserFetchOutput:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover - exercised when optional dep is absent
        raise RuntimeError("Playwright is not installed. Install playwright and browser binaries first.") from exc

    timeout_ms = options.timeout_seconds * 1000
    async with async_playwright() as pw:
        context = None
        browser = None
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ]
        if options.profile:
            launch_args.append(f"--profile-directory={options.profile}")
        executable = options.executable_path or (DEFAULT_CHROME_EXE if Path(DEFAULT_CHROME_EXE).exists() else None)
        if options.storage_state:
            browser = await pw.chromium.launch(
                executable_path=executable,
                headless=options.headless,
                args=launch_args,
                timeout=timeout_ms,
                channel=None if executable else "chrome",
            )
            context = await browser.new_context(
                storage_state=options.storage_state,
                viewport={"width": options.viewport_width, "height": options.viewport_height},
                accept_downloads=True,
            )
        else:
            user_data_dir = browser_user_data_dir(options)
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                executable_path=executable,
                headless=options.headless,
                args=launch_args,
                viewport={"width": options.viewport_width, "height": options.viewport_height},
                timeout=timeout_ms,
                accept_downloads=True,
                channel=None if executable else "chrome",
                chromium_sandbox=False,
                no_viewport=False,
                ignore_default_args=["--enable-automation"],
            )

        try:
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(timeout_ms)
            await page.goto(options.url, wait_until=options.wait_until, timeout=timeout_ms)
            await page.wait_for_timeout(500)
            overlay_metrics = {"enabled": bool(options.dismiss_obstructive_overlays), "clicked_count": 0}
            if options.dismiss_obstructive_overlays:
                overlay_metrics = await _dismiss_obstructive_overlays(page, settle_ms=options.settle_ms)
            scroll_metrics = {"auto_scroll": False}
            anchor_metrics = {"anchor_navigation": False}
            sweep_metrics = {"position_sweep": False}
            if options.auto_scroll:
                scroll_metrics = await _auto_scroll(page, steps=options.scroll_steps, settle_ms=options.settle_ms)
                anchor_metrics = await _visit_document_anchor_targets(page, settle_ms=options.settle_ms)
                sweep_metrics = await _sweep_scroll_positions(page, steps=options.scroll_steps, settle_ms=options.settle_ms)
                if options.dismiss_obstructive_overlays:
                    second_overlay_metrics = await _dismiss_obstructive_overlays(page, settle_ms=options.settle_ms)
                    if int(second_overlay_metrics.get("clicked_count") or 0) > 0:
                        overlay_metrics = {**overlay_metrics, "after_scroll": second_overlay_metrics, "clicked_count": int(overlay_metrics.get("clicked_count") or 0) + int(second_overlay_metrics.get("clicked_count") or 0)}
            hidden_cleanup_metrics = await _remove_hidden_extraction_noise(page) if options.dismiss_obstructive_overlays else {"enabled": False, "removed_count": 0}
            image_candidates = list(scroll_metrics.pop("image_candidates", [])) if isinstance(scroll_metrics, dict) else []
            if isinstance(anchor_metrics, dict):
                image_candidates.extend(list(anchor_metrics.pop("image_candidates", [])))
            if isinstance(sweep_metrics, dict):
                image_candidates.extend(list(sweep_metrics.pop("image_candidates", [])))
            scrolled_visible_blocks = list(scroll_metrics.pop("visible_blocks", [])) if isinstance(scroll_metrics, dict) else []
            if isinstance(anchor_metrics, dict):
                scrolled_visible_blocks.extend(list(anchor_metrics.pop("visible_blocks", [])))
            if isinstance(sweep_metrics, dict):
                scrolled_visible_blocks.extend(list(sweep_metrics.pop("visible_blocks", [])))
            html = await page.content()
            title = await page.title()
            final_url = page.url
            visible_blocks, visible_markdown, visible_metrics = await _collect_visible_blocks(page)
            if scrolled_visible_blocks:
                merged_blocks: list[dict[str, Any]] = []
                seen_block_keys: set[str] = set()
                for block in [*scrolled_visible_blocks, *visible_blocks]:
                    text = str(block.get("text") or "").strip()
                    src = str(block.get("src") or "").strip()
                    key = f"{block.get('type') or ''}|{text}|{src}"
                    if not text or key in seen_block_keys:
                        continue
                    seen_block_keys.add(key)
                    merged = dict(block)
                    merged["order"] = len(merged_blocks) + 1
                    merged_blocks.append(merged)
                visible_blocks = merged_blocks
                visible_markdown, visible_metrics = _visible_blocks_to_markdown(visible_blocks)
                visible_metrics["collected_during_scroll"] = True
                visible_metrics["scrolled_visible_block_count"] = len(scrolled_visible_blocks)
            visible_metrics["scroll"] = scroll_metrics
            visible_metrics["anchor_navigation"] = anchor_metrics
            visible_metrics["position_sweep"] = sweep_metrics
            visible_metrics["dismiss_obstructive_overlays"] = overlay_metrics
            visible_metrics["hidden_extraction_noise_cleanup"] = hidden_cleanup_metrics

            image_assets = []
            if options.download_images and options.image_assets_dir:
                image_assets = await _download_page_images(context, page, options.image_assets_dir, timeout_ms=timeout_ms, candidates=image_candidates)

            screenshot_path = ""
            if options.screenshot_path:
                path = Path(options.screenshot_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    await page.screenshot(path=str(path), full_page=True, timeout=min(timeout_ms, 10_000))
                    screenshot_path = str(path)
                except Exception as exc:
                    visible_metrics["screenshot_error"] = f"{type(exc).__name__}:{str(exc)[:200]}"

            html_path = ""
            if options.html_path:
                path = Path(options.html_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(html, encoding="utf-8", newline="\n")
                html_path = str(path)

            return BrowserFetchOutput(
                html=html,
                final_url=final_url,
                title=title,
                screenshot_path=screenshot_path,
                html_path=html_path,
                image_assets=image_assets,
                visible_blocks=visible_blocks,
                visible_markdown=visible_markdown,
                visible_metrics=visible_metrics,
            )
        finally:
            await context.close()
            if browser:
                await browser.close()


def fetch_with_browser(options: BrowserFetchOptions) -> BrowserFetchOutput:
    try:
        return asyncio.run(_fetch_with_browser(options))
    except RuntimeError as exc:
        if "asyncio.run() cannot be called from a running event loop" not in str(exc):
            raise
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_fetch_with_browser(options))
        finally:
            loop.close()


async def _open_browser_profile_for_login(options: BrowserProfileLoginOptions) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover - exercised when optional dep is absent
        raise RuntimeError("Playwright is not installed. Install playwright and browser binaries first.") from exc

    timeout_ms = max(30, options.timeout_seconds) * 1000
    user_data_dir = options.user_data_dir or str(default_profile_dir())
    async with async_playwright() as pw:
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ]
        if options.profile:
            launch_args.append(f"--profile-directory={options.profile}")
        executable = options.executable_path or (DEFAULT_CHROME_EXE if Path(DEFAULT_CHROME_EXE).exists() else None)
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            executable_path=executable,
            headless=False,
            args=launch_args,
            viewport={"width": options.viewport_width, "height": options.viewport_height},
            timeout=timeout_ms,
            accept_downloads=True,
            channel=None if executable else "chrome",
            chromium_sandbox=False,
            no_viewport=False,
            ignore_default_args=["--enable-automation"],
        )
        try:
            login_urls = [str(url).strip() for url in (options.urls or [options.url]) if str(url).strip()]
            if not login_urls:
                login_urls = ["about:blank"]
            pages = []
            navigation_errors: list[str] = []
            for index, url in enumerate(login_urls):
                page = context.pages[0] if index == 0 and context.pages else await context.new_page()
                pages.append(page)
                if url and url != "about:blank":
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    except Exception as exc:
                        navigation_errors.append(f"{url}: {type(exc).__name__}:{str(exc)[:200]}")
            try:
                await context.wait_for_event("close", timeout=timeout_ms)
            except Exception:
                pass
            return {
                "status": "ok",
                "user_data_dir": user_data_dir,
                "url": pages[0].url if pages and not pages[0].is_closed() else login_urls[0],
                "urls": login_urls,
                "final_urls": [page.url for page in pages if not page.is_closed()],
                "navigation_errors": navigation_errors,
                "message": "Browser profile session finished. Future browser fetches reuse this persistent profile by default.",
            }
        finally:
            await context.close()


def open_browser_profile_for_login(options: BrowserProfileLoginOptions) -> dict[str, Any]:
    try:
        return asyncio.run(_open_browser_profile_for_login(options))
    except RuntimeError as exc:
        if "asyncio.run() cannot be called from a running event loop" not in str(exc):
            raise
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_open_browser_profile_for_login(options))
        finally:
            loop.close()
