from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from typing import Any

from .html_extract import Node, clean_text, collect_links, node_text, parse_html, select_nodes, title_from_html


def stable_id(*parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def first_node_text(root: Node, selector: str, limit: int | None = None) -> str:
    nodes = select_nodes(root, selector)
    if not nodes:
        return ""
    text = node_text(nodes[0])
    return text[:limit].strip() if limit else text.strip()


def ancestor_with_cell_id(node: Node) -> Node | None:
    cur: Node | None = node
    while cur is not None:
        if cur.tag == "div" and cur.attr("id", "").startswith("r_"):
            return cur
        cur = cur.parent
    return None


def member_from_links(node: Node, base_url: str) -> tuple[str, str]:
    for link in collect_links(node, base_url):
        parsed = urllib.parse.urlparse(link)
        if parsed.path.startswith("/member/"):
            return parsed.path.rsplit("/", 1)[-1], link
    return "", ""


def classify_v2ex_viewpoints(body: str, replies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    texts = [body] + [str(reply.get("content") or "") for reply in replies]
    buckets = [
        ("成本上升但收入未同步", ["开销", "订阅费", "自费", "收入", "工资", "付费"]),
        ("生产力提升不等于分配权提升", ["生产力", "收入提升", "分配权", "利润", "工作量", "技术贬值"]),
        ("AI 费用成为保住工作的必要成本", ["不用", "出局", "保住工作", "完不成"]),
        ("AI 主要是在购买时间", ["买时间", "工作时间", "摸鱼", "提速", "单位时间"]),
        ("企业/行业差异决定收益归属", ["行业", "芯片", "ai 行业", "公司", "报销", "资本家"]),
    ]
    viewpoints: list[dict[str, Any]] = []
    for label, keywords in buckets:
        matched = []
        for idx, text in enumerate(texts):
            lowered = text.lower()
            if any(keyword.lower() in lowered for keyword in keywords):
                matched.append({"sample_index": idx, "text": clean_text(text)[:180]})
            if len(matched) >= 3:
                break
        if matched:
            viewpoints.append({"viewpoint": label, "supporting_samples": matched})
    return viewpoints


def extract_v2ex(url: str, html: str, *, root: Node | None = None, max_replies: int = 30) -> dict[str, Any]:
    root = root or parse_html(html)
    title_raw = first_node_text(root, "title") or title_from_html(html, root)
    title = re.sub(r"\s+-\s+V2EX\s*$", "", title_raw).strip() or title_raw
    topic_id_match = re.search(r"/t/(\d+)", url)
    topic_id = topic_id_match.group(1) if topic_id_match else ""

    header_nodes = select_nodes(root, "div.header")
    header_text = first_node_text(root, "div.header", 800)
    body = first_node_text(root, "div.topic_content", 4000)
    topic_author, topic_author_url = ("", "")
    if header_nodes:
        topic_author, topic_author_url = member_from_links(header_nodes[0], url)

    node_name = ""
    breadcrumbs = []
    for link in collect_links(header_nodes[0], url) if header_nodes else []:
        parsed = urllib.parse.urlparse(link)
        if parsed.path.startswith("/go/"):
            node_name = parsed.path.rsplit("/", 1)[-1]
            breadcrumbs.append(link)

    click_match = re.search(r"(\d+)\s*次点击", header_text)
    time_match = re.search(r"(\d+\s*(?:秒|分钟|小时|天|周|个月|年)前)", header_text)
    created_hint = time_match.group(1) if time_match else ""

    replies = []
    for idx, reply_node in enumerate(select_nodes(root, "div.reply_content")[:max_replies], start=1):
        cell = ancestor_with_cell_id(reply_node) or reply_node.parent or reply_node
        author, author_url = member_from_links(cell, url)
        text = clean_text(node_text(reply_node))[:2000]
        if not text:
            continue
        floor = ""
        floor_match = re.search(r"#(\d+)", clean_text(node_text(cell))[:500])
        if floor_match:
            floor = floor_match.group(1)
        replies.append(
            {
                "floor": floor or str(idx),
                "author": author,
                "author_url": author_url,
                "content": text,
                "links": collect_links(reply_node, url),
            }
        )

    return {
        "site": "V2EX",
        "site_profile": "v2ex_thread",
        "topic_id": topic_id,
        "title": title,
        "summary": body[:600],
        "node": node_name,
        "breadcrumbs": breadcrumbs,
        "author": topic_author,
        "author_url": topic_author_url,
        "created_hint": created_hint,
        "click_count": int(click_match.group(1)) if click_match else None,
        "body": body,
        "reply_count_extracted": len(replies),
        "replies": replies,
        "viewpoints": classify_v2ex_viewpoints(body, replies),
        "structure_quality": {
            "title": bool(title),
            "body": bool(body),
            "replies": len(replies),
            "author": bool(topic_author),
        },
    }


def extract_feishu_doc(url: str, html: str, *, root: Node | None = None) -> dict[str, Any]:
    root = root or parse_html(html)
    parsed = urllib.parse.urlparse(url)
    token_match = re.search(r"/(?:docx?|wiki)/([A-Za-z0-9]+)", parsed.path)
    anchors = []
    external_links = []
    for link in collect_links(root, url):
        link_parsed = urllib.parse.urlparse(link)
        if link_parsed.netloc.lower().endswith("feishu.cn") and link_parsed.fragment:
            anchors.append(link)
        elif (
            link_parsed.scheme in {"http", "https"}
            and "feishu.cn" not in link_parsed.netloc.lower()
            and "feishucdn.com" not in link_parsed.netloc.lower()
            and not re.search(r"\.(css|js|png|jpg|jpeg|gif|webp|svg|ico)$", link_parsed.path, re.I)
        ):
            external_links.append(link)
    body = clean_text(node_text(root))[:12000]
    images = select_nodes(root, "img")
    attachments = extract_attachment_mentions(body)
    virtual_placeholder_heights = [
        float(match.group(1))
        for match in re.finditer(r"bear-virtual-renderUnit-placeholder[^>]+height:\s*([0-9.]+)px", html or "", re.I)
    ]
    catalog_headings = extract_feishu_catalog_headings(body)
    ssr_catalog = extract_feishu_ssr_catalog(html)
    ssr_heading_texts = [item["text"] for item in ssr_catalog if item.get("text")]
    missing_ssr_headings = [
        heading
        for heading in ssr_heading_texts
        if heading and heading not in body
    ]
    anonymous_shell = bool(
        re.search(r"登录/注册|Log in|Sign in", body, re.I)
        or re.search(r'class="[^"]*\bnote-login\b|class="[^"]*\bsuite-docx anonymous\b', html or "", re.I)
    )
    return {
        "site": "Feishu",
        "site_profile": "feishu_doc",
        "doc_token": token_match.group(1) if token_match else "",
        "title": title_from_html(html, root),
        "summary": body[:600],
        "anchor_count": len(anchors),
        "anchors": anchors[:100],
        "external_links": external_links[:100],
        "image_count": len(images),
        "attachments": attachments,
        "catalog_headings": catalog_headings,
        "ssr_catalog": ssr_catalog,
        "ssr_heading_count": len(ssr_heading_texts),
        "missing_ssr_headings": missing_ssr_headings[:50],
        "structure_quality": {
            "title": bool(title_from_html(html, root)),
            "body_chars": len(body),
            "images": len(images),
            "external_links": len(external_links),
            "attachments": len(attachments),
            "catalog_headings": len(catalog_headings),
            "ssr_heading_count": len(ssr_heading_texts),
            "missing_ssr_heading_count": len(missing_ssr_headings),
            "anonymous_or_login_prompt": anonymous_shell,
            "virtual_placeholder_height": max(virtual_placeholder_heights) if virtual_placeholder_heights else 0,
        },
    }


def extract_feishu_ssr_catalog(html: str) -> list[dict[str, Any]]:
    payload = _extract_window_json(html or "", "window.catalogRecordInfo=")
    if not isinstance(payload, dict):
        return []
    records = payload.get("headingRecords")
    if not isinstance(records, dict):
        return []
    items: list[dict[str, Any]] = []
    for record_id, record in records.items():
        if not isinstance(record, dict):
            continue
        data = record.get("data") or {}
        if not isinstance(data, dict):
            continue
        record_type = str(data.get("type") or "")
        text = _feishu_record_text(data)
        if not text:
            continue
        level = 1
        match = re.fullmatch(r"heading([1-6])", record_type)
        if match:
            level = int(match.group(1))
        elif record_type == "page":
            level = 1
        items.append({"id": str(record_id), "type": record_type, "level": level, "text": text})
    return items[:200]


def _feishu_record_text(data: dict[str, Any]) -> str:
    text_data = ((data.get("text") or {}).get("initialAttributedTexts") or {}).get("text") or {}
    if not isinstance(text_data, dict):
        return ""
    def sort_key(value: object) -> int:
        try:
            return int(str(value))
        except ValueError:
            return 0
    return clean_text("".join(str(text_data[key]) for key in sorted(text_data, key=sort_key)))


def _extract_window_json(html: str, marker: str) -> dict[str, Any] | None:
    start = html.find(marker)
    if start < 0:
        return None
    cursor = start + len(marker)
    while cursor < len(html) and html[cursor].isspace():
        cursor += 1
    if cursor >= len(html) or html[cursor] != "{":
        return None
    depth = 0
    in_string = False
    quote = ""
    escape = False
    for index in range(cursor, len(html)):
        ch = html[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_string = False
            continue
        if ch in {'"', "'"}:
            in_string = True
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[cursor:index + 1])
                except Exception:
                    return None
    return None


def extract_feishu_catalog_headings(text: str) -> list[str]:
    headings: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        value = clean_text(line)
        if not value or len(value) > 80:
            continue
        if value in seen:
            continue
        if _looks_like_catalog_heading(value):
            seen.add(value)
            headings.append(value)
    return headings[:100]


def _looks_like_catalog_heading(value: str) -> bool:
    if re.match(r"^(?:#{1,6}\s+|\d{1,2}[.、]\s*|[一二三四五六七八九十]{1,3}[、.]\s*)", value):
        return True
    if len(value) <= 36 and not re.search(r"[。！？!?；;，,]", value):
        return True
    return False


def extract_attachment_mentions(text: str) -> list[dict[str, Any]]:
    pattern = re.compile(r"([^\s<>:\"|?*]{2,120}\.(?:pdf|docx?|xlsx?|pptx?|csv|zip|rar|7z))", re.I)
    seen: set[str] = set()
    attachments: list[dict[str, Any]] = []
    for match in pattern.finditer(text or ""):
        filename = clean_attachment_filename(match.group(1))
        key = filename.lower()
        if key in seen:
            continue
        seen.add(key)
        suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        attachments.append(
            {
                "filename": filename,
                "extension": suffix,
                "source": "text_mention",
                "status": "candidate",
                "note": "Detected from rendered document text; no direct downloadable URL was exposed in the page snapshot.",
            }
        )
    return attachments


def clean_attachment_filename(value: str) -> str:
    filename = (value or "").strip("，。；;、)）")
    filename = re.sub(r"^(?:附件|文件|下载|材料|资料|文档)\s*[:：]\s*", "", filename)
    if re.search(r"[:：]", filename):
        filename = re.split(r"[:：]", filename)[-1].strip()
    return filename


def extract_site_data(url: str, html: str, *, root: Node | None = None) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if "v2ex.com" in host and re.search(r"/t/\d+", parsed.path):
        data = extract_v2ex(url, html, root=root)
        data["site_data_id"] = stable_id("site_data", url, data.get("topic_id"))
        return data
    if ("feishu.cn" in host or "larksuite.com" in host) and re.search(r"/(?:docx?|wiki)/", parsed.path):
        data = extract_feishu_doc(url, html, root=root)
        data["site_data_id"] = stable_id("site_data", url, data.get("doc_token"))
        return data
    return {}
