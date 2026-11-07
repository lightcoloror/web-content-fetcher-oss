from __future__ import annotations

import json
import mimetypes
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


FEISHU_OPEN_API = "https://open.feishu.cn/open-apis"
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


class FeishuExportUnavailable(RuntimeError):
    pass


def is_feishu_doc_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url or "")
    host = parsed.netloc.lower()
    return ("feishu.cn" in host or "larksuite.com" in host) and bool(re.search(r"/docx?/|/wiki/", parsed.path))


def extract_feishu_token(url_or_token: str) -> str:
    match = re.search(r"/(?:docx|doc|wiki)/([A-Za-z0-9]+)", url_or_token or "")
    return match.group(1) if match else (url_or_token or "")


def feishu_credentials_available() -> bool:
    return bool(_config_value("FEISHU_APP_ID") and _config_value("FEISHU_APP_SECRET"))


def _config_value(name: str) -> str:
    value = os.environ.get(name) or os.environ.get(name.replace("FEISHU_", "LARK_")) or ""
    if value:
        return value
    config_path = os.environ.get("WEB_CONTENT_FETCHER_FEISHU_CONFIG", "")
    if config_path:
        try:
            data = json.loads(Path(config_path).read_text(encoding="utf-8"))
            key = name.lower().replace("feishu_", "")
            return str(data.get(key) or data.get(name) or "")
        except Exception:
            return ""
    return ""


def _json_request(url: str, *, token: str = "", method: str = "GET", payload: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body.decode("utf-8", errors="replace"))


def _binary_request(url: str, *, token: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def get_tenant_access_token(timeout: int = 30) -> str:
    app_id = _config_value("FEISHU_APP_ID")
    app_secret = _config_value("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise FeishuExportUnavailable("missing FEISHU_APP_ID/FEISHU_APP_SECRET")
    data = _json_request(
        f"{FEISHU_OPEN_API}/auth/v3/tenant_access_token/internal",
        method="POST",
        payload={"app_id": app_id, "app_secret": app_secret},
        timeout=timeout,
    )
    if data.get("code") != 0:
        raise RuntimeError(f"tenant_access_token failed: {data.get('msg') or data}")
    token = str((data.get("tenant_access_token") or ""))
    if not token:
        raise RuntimeError("tenant_access_token missing in response")
    return token


def export_feishu_docx(url_or_token: str, *, output_dir: str = "", timeout: int = 60, poll_interval: float = 1.5) -> dict[str, Any]:
    token = get_tenant_access_token(timeout=timeout)
    doc_token = extract_feishu_token(url_or_token)
    if not doc_token:
        raise RuntimeError("missing Feishu document token")
    root = Path(output_dir or tempfile.mkdtemp(prefix="wcf-feishu-export-"))
    root.mkdir(parents=True, exist_ok=True)

    create = _json_request(
        f"{FEISHU_OPEN_API}/drive/v1/export_tasks",
        token=token,
        method="POST",
        payload={"file_extension": "docx", "token": doc_token, "type": "docx"},
        timeout=timeout,
    )
    if create.get("code") != 0:
        raise RuntimeError(f"feishu export create failed: {create.get('msg') or create}")
    ticket = str(((create.get("data") or {}).get("ticket") or ""))
    if not ticket:
        raise RuntimeError(f"feishu export ticket missing: {create}")

    result: dict[str, Any] = {}
    deadline = time.time() + max(10, timeout)
    while time.time() < deadline:
        result = _json_request(
            f"{FEISHU_OPEN_API}/drive/v1/export_tasks/{urllib.parse.quote(ticket)}?token={urllib.parse.quote(doc_token)}",
            token=token,
            timeout=timeout,
        )
        if result.get("code") != 0:
            raise RuntimeError(f"feishu export query failed: {result.get('msg') or result}")
        task = result.get("data") or {}
        job_status = int(task.get("job_status") or task.get("status") or 0)
        file_token = str(task.get("file_token") or "")
        if file_token and job_status in {0, 3, 4}:
            body = _binary_request(
                f"{FEISHU_OPEN_API}/drive/v1/export_tasks/file/{urllib.parse.quote(file_token)}/download",
                token=token,
                timeout=timeout,
            )
            file_name = str(task.get("file_name") or f"{doc_token}.docx")
            if not file_name.lower().endswith(".docx"):
                file_name = f"{file_name}.docx"
            path = root / _safe_name(file_name)
            path.write_bytes(body)
            return {
                "doc_token": doc_token,
                "ticket": ticket,
                "file_token": file_token,
                "file_name": file_name,
                "docx_path": str(path),
                "bytes": len(body),
                "query_result": task,
            }
        if job_status in {2, 5}:
            raise RuntimeError(f"feishu export failed: {task}")
        time.sleep(poll_interval)
    raise TimeoutError(f"feishu export timed out: ticket={ticket}, last={result}")


def parse_docx_export(docx_path: str, *, assets_dir: str = "") -> dict[str, Any]:
    root = Path(assets_dir or tempfile.mkdtemp(prefix="wcf-feishu-docx-assets-"))
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(docx_path) as zf:
        rels = _read_relationships(zf)
        title = _read_core_title(zf)
        document = ET.fromstring(zf.read("word/document.xml"))
        lines: list[str] = []
        image_assets: list[dict[str, Any]] = []
        image_index = 0
        for child in document.findall("./w:body/*", NS):
            tag = _local_name(child.tag)
            if tag == "p":
                para_lines, para_assets, image_index = _paragraph_to_markdown(child, zf, rels, root, image_index)
                lines.extend(para_lines)
                image_assets.extend(para_assets)
            elif tag == "tbl":
                table = _table_to_markdown(child)
                if table:
                    lines.extend(["", table, ""])
        markdown = "\n\n".join(line for line in lines if line is not None).strip()
    markdown_stats = markdown_capture_stats(markdown)
    return {
        "title": title,
        "markdown": markdown,
        "image_assets": image_assets,
        "image_count": len(image_assets),
        "asset_dir": str(root),
        "markdown_stats": markdown_stats,
        "text_chars": markdown_stats["text_chars"],
        "heading_count": markdown_stats["heading_count"],
        "table_count": markdown_stats["table_count"],
    }


def markdown_capture_stats(markdown: str) -> dict[str, Any]:
    text = str(markdown or "")
    heading_count = 0
    image_count = 0
    table_count = 0
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if re.match(r"^#{1,6}\s+\S", stripped):
            heading_count += 1
        image_count += len(re.findall(r"!\[[^\]]*\]\([^)]+\)", line))
        if "|" in stripped and re.match(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", stripped):
            table_count += 1
    plain = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", plain)
    plain = re.sub(r"^#{1,6}\s+", "", plain, flags=re.M)
    plain = re.sub(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", "", plain, flags=re.M)
    text_chars = len(re.sub(r"\s+", "", plain))
    return {
        "text_chars": text_chars,
        "heading_count": heading_count,
        "image_count": image_count,
        "table_count": table_count,
    }


def compare_feishu_captures(
    *,
    browser_markdown: str = "",
    export_markdown: str = "",
    browser_stats: dict[str, Any] | None = None,
    export_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    browser = dict(browser_stats or markdown_capture_stats(browser_markdown))
    export = dict(export_stats or (markdown_capture_stats(export_markdown) if export_markdown else {}))
    reasons: list[str] = []
    if not export:
        return {
            "status": "missing_export",
            "browser": browser,
            "export": {},
            "deltas": {},
            "reasons": ["feishu_docx_export_not_available"],
        }
    if not browser_markdown and not browser.get("text_chars"):
        return {
            "status": "missing_browser_reference",
            "browser": browser,
            "export": export,
            "deltas": {},
            "reasons": ["browser_or_rendered_markdown_not_available"],
        }
    deltas = {
        "text_chars": int(browser.get("text_chars") or 0) - int(export.get("text_chars") or 0),
        "heading_count": int(browser.get("heading_count") or 0) - int(export.get("heading_count") or 0),
        "image_count": int(browser.get("image_count") or 0) - int(export.get("image_count") or 0),
        "table_count": int(browser.get("table_count") or 0) - int(export.get("table_count") or 0),
    }
    export_text = int(export.get("text_chars") or 0)
    browser_text = int(browser.get("text_chars") or 0)
    if export_text >= 800 and browser_text < int(export_text * 0.8):
        reasons.append("browser_text_chars_significantly_lower_than_export")
    if int(browser.get("image_count") or 0) < int(export.get("image_count") or 0):
        reasons.append("browser_image_count_lower_than_export")
    if int(browser.get("table_count") or 0) < int(export.get("table_count") or 0):
        reasons.append("browser_table_count_lower_than_export")
    if int(browser.get("heading_count") or 0) < max(0, int(export.get("heading_count") or 0) - 2):
        reasons.append("browser_heading_count_lower_than_export")
    return {
        "status": "needs_review" if reasons else "ok",
        "browser": browser,
        "export": export,
        "deltas": deltas,
        "reasons": reasons,
    }


def _read_relationships(zf: zipfile.ZipFile) -> dict[str, str]:
    try:
        rels_root = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
    except KeyError:
        return {}
    rels: dict[str, str] = {}
    for rel in rels_root:
        rel_id = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        if rel_id and target:
            rels[rel_id] = target if target.startswith("word/") else f"word/{target.lstrip('/')}"
    return rels


def _read_core_title(zf: zipfile.ZipFile) -> str:
    try:
        root = ET.fromstring(zf.read("docProps/core.xml"))
        title = root.findtext("dc:title", default="", namespaces=NS)
        return (title or "").strip()
    except Exception:
        return ""


def _paragraph_to_markdown(
    para: ET.Element,
    zf: zipfile.ZipFile,
    rels: dict[str, str],
    assets_dir: Path,
    image_index: int,
) -> tuple[list[str], list[dict[str, Any]], int]:
    text_parts: list[str] = []
    assets: list[dict[str, Any]] = []
    heading_level = _heading_level(para)
    for node in para.iter():
        tag = _local_name(node.tag)
        if tag == "t" and node.text:
            text_parts.append(node.text)
        elif tag == "tab":
            text_parts.append("\t")
        elif tag == "br":
            text_parts.append("\n")
        elif tag == "blip":
            embed = node.attrib.get(f"{{{NS['r']}}}embed") or node.attrib.get(f"{{{NS['r']}}}link") or ""
            target = rels.get(embed, "")
            if target:
                image_index += 1
                asset = _extract_docx_image(zf, target, assets_dir, image_index)
                if asset:
                    assets.append(asset)
                    text_parts.append(f"![{asset['alt']}]({asset['asset_path']})")
    text = "".join(text_parts).strip()
    if not text:
        return [], assets, image_index
    if heading_level:
        return [f"{'#' * heading_level} {text}"], assets, image_index
    return [text], assets, image_index


def _table_to_markdown(table: ET.Element) -> str:
    rows: list[list[str]] = []
    for row in table.findall(".//w:tr", NS):
        cells = []
        for cell in row.findall("./w:tc", NS):
            cell_text = " ".join(text.text or "" for text in cell.findall(".//w:t", NS)).strip()
            cells.append(cell_text.replace("|", "\\|"))
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = "| " + " | ".join(normalized[0]) + " |"
    sep = "| " + " | ".join("---" for _ in range(width)) + " |"
    body = ["| " + " | ".join(row) + " |" for row in normalized[1:]]
    return "\n".join([header, sep, *body])


def _heading_level(para: ET.Element) -> int:
    style = para.find("./w:pPr/w:pStyle", NS)
    value = style.attrib.get(f"{{{NS['w']}}}val", "") if style is not None else ""
    match = re.search(r"Heading([1-6])|([1-6])$", value or "", re.I)
    return int(match.group(1) or match.group(2)) if match else 0


def _extract_docx_image(zf: zipfile.ZipFile, target: str, assets_dir: Path, index: int) -> dict[str, Any] | None:
    try:
        body = zf.read(target)
    except KeyError:
        return None
    source_name = Path(target).name or f"image{index}"
    suffix = Path(source_name).suffix or mimetypes.guess_extension("image/png") or ".png"
    path = assets_dir / f"docx_image_{index:03d}{suffix}"
    path.write_bytes(body)
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "url": f"docx://{target}",
        "alt": f"docx image {index}",
        "title": "",
        "asset_path": str(path),
        "asset_sha256": _sha256_bytes(body),
        "content_type": content_type,
        "bytes": len(body),
    }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in value)[:160].strip() or "feishu-export.docx"


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()
