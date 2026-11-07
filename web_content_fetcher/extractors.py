from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

from .defuddle_backend import DefuddleRequest, defuddle_available, run_defuddle
from .html_extract import Node, clean_text, extract_main_text, iter_nodes, parse_html, select_nodes


EXTRACTOR_CHOICES = {"auto", "yuque", "trafilatura", "readability", "defuddle", "builtin"}
MIN_USEFUL_CHARS = 200


@dataclass
class ExtractionResult:
    text: str
    engine: str
    metadata: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def extract_content(html: str, *, url: str = "", title: str = "", extractor: str = "auto") -> ExtractionResult:
    requested = (extractor or "auto").lower()
    if requested not in EXTRACTOR_CHOICES:
        requested = "auto"
    if requested == "auto":
        site_result = _extract_site_specific(html, url=url, title=title)
        if site_result and _is_useful(site_result, title=title, url=url):
            site_result.quality = quality_for(site_result.text, title=title, url=url, engine=site_result.engine, metadata=site_result.metadata)
            return site_result
    engines = ["trafilatura", "readability", "builtin"] if requested == "auto" else [requested]
    errors: list[str] = []
    fallback_candidate: ExtractionResult | None = None

    for engine in engines:
        try:
            result = _run_engine(engine, html, url=url, title=title)
        except Exception as exc:
            errors.append(f"{engine}:{type(exc).__name__}:{str(exc)[:200]}")
            continue
        result.errors.extend(errors)
        if _is_useful(result, title=title, url=url):
            result.quality = quality_for(result.text, title=title, url=url, engine=result.engine, metadata=result.metadata)
            if requested == "auto":
                recovered = _try_readability_coverage(html, url=url, title=title, primary=result)
                if recovered is not None:
                    return recovered
                sidecar = _try_defuddle_sidecar(html, url=url, title=title, primary=result)
                if sidecar is not None:
                    return sidecar
            return result
        if fallback_candidate is None or len(result.text) > len(fallback_candidate.text):
            fallback_candidate = result
        errors.extend(result.errors)

    if fallback_candidate is None:
        fallback_candidate = _extract_builtin(html, url=url, title=title)
    fallback_candidate.errors = errors
    fallback_candidate.quality = quality_for(
        fallback_candidate.text,
        title=title,
        url=url,
        engine=fallback_candidate.engine,
        metadata=fallback_candidate.metadata,
    )
    return fallback_candidate


def _try_readability_coverage(html: str, *, url: str, title: str, primary: ExtractionResult) -> ExtractionResult | None:
    if primary.engine != "trafilatura":
        return None
    from .completeness import _body_selection, compare_body_blocks

    body, scope = _body_selection(parse_html(html))
    if scope not in {"explicit_body_marker", "single_article", "existing_document_body_marker"}:
        return None
    before = compare_body_blocks(body, primary.text)
    covered = {row["dom_path"] for row in before["nodes"] if row["output_normalized_span"] is not None}
    if before["budget_exceeded"] or not before["missing"] or len(covered) < 2:
        return None
    try:
        # Coverage recovery already has a bounded source-body comparison.
        # Keep short article headings/labels for that comparison instead of
        # Readability's normal 25-character container pruning.
        candidate = _extract_readability(html, url=url, title=title, coverage_recovery=True)
        after = compare_body_blocks(body, candidate.text)
        recovered = {row["dom_path"] for row in after["nodes"] if row["output_normalized_span"] is not None}
        # A longer candidate alone is not sufficient: retain every previously covered source block.
        if after["budget_exceeded"] or not covered < recovered or not _is_useful(candidate, title=title, url=url):
            return None
        if not _retains_markdown_structure(primary.text, candidate.text):
            return None
        candidate.metadata = {**primary.metadata, **candidate.metadata, "coverage_recovery": {
            "strategy": "readability_after_saved_dom_omissions", "source_scope": scope,
            "previous_engine": primary.engine, "previous_covered_blocks": len(covered),
            "selected_covered_blocks": len(recovered), "remaining_missing_blocks": len(after["missing"]),
            "preserved_previous_blocks": True}}
        candidate.quality = quality_for(candidate.text, title=title, url=url, engine=candidate.engine, metadata=candidate.metadata)
        candidate.errors = [*primary.errors, *candidate.errors, "fallback:readability_after_saved_dom_omissions"]
        return candidate
    except Exception as exc:
        primary.errors.append(f"readability_coverage:{type(exc).__name__}:{str(exc)[:120]}")
        return None


def _retains_markdown_structure(previous: str, candidate: str) -> bool:
    from collections import Counter
    from markdown_it import MarkdownIt

    def structure(text):
        tokens = MarkdownIt().enable("table").parse(text)
        images = Counter(child.attrGet("src") for token in tokens for child in token.children or [] if child.type == "image")
        return images, sum(token.type == "table_open" for token in tokens)

    old_images, old_tables = structure(previous)
    new_images, new_tables = structure(candidate)
    return not (old_images - new_images) and new_tables >= old_tables


def _run_engine(engine: str, html: str, *, url: str, title: str) -> ExtractionResult:
    if engine == "yuque":
        return _extract_yuque(html, url=url, title=title)
    if engine == "trafilatura":
        return _extract_trafilatura(html, url=url, title=title)
    if engine == "readability":
        return _extract_readability(html, url=url, title=title)
    if engine == "defuddle":
        return _extract_defuddle(html, url=url, title=title)
    return _extract_builtin(html, url=url, title=title)


def _extract_defuddle(html: str, *, url: str, title: str) -> ExtractionResult:
    result = run_defuddle(DefuddleRequest(html=html))
    markdown = _normalize_markdown(result.get("markdown") or "")
    if not markdown:
        markdown = html_fragment_to_markdown(str(result.get("html") or ""), base_url=url)
    metadata = dict(result.get("metadata") or {})
    metadata["defuddle"] = {
        "config": result.get("config") or {},
        "stderr": str(result.get("stderr") or "")[:300],
        "source_html_chars": len(html),
    }
    if not metadata.get("title") and title:
        metadata["title"] = title
    return ExtractionResult(text=markdown, engine="defuddle", metadata=metadata)


def _structure_score(markdown: str) -> int:
    value = markdown or ""
    headings = len(re.findall(r"(?m)^#{1,6}\s+", value))
    images = value.count("![")
    tables = len(re.findall(r"(?m)^\|.*\|\s*$", value)) // 2
    return min(headings, 4) + min(images, 3) * 2 + min(tables, 2) * 2


def _should_try_defuddle_sidecar(html: str, primary: ExtractionResult) -> bool:
    if not defuddle_available():
        return False
    source = (html or "").lower()
    source_features = 0
    if re.search(r"<h[1-6][\s>]", source):
        source_features += 1
    if "<table" in source:
        source_features += 2
    if "<img" in source:
        source_features += 2
    return source_features > 0 and _structure_score(primary.text) < source_features


def _try_defuddle_sidecar(html: str, *, url: str, title: str, primary: ExtractionResult) -> ExtractionResult | None:
    if not _should_try_defuddle_sidecar(html, primary):
        return None
    try:
        candidate = _extract_defuddle(html, url=url, title=title)
    except Exception as exc:
        primary.errors.append(f"defuddle_sidecar:{type(exc).__name__}:{str(exc)[:200]}")
        return None
    candidate.quality = quality_for(candidate.text, title=title, url=url, engine=candidate.engine, metadata=candidate.metadata)
    if not _is_useful(candidate, title=title, url=url):
        primary.errors.append("defuddle_sidecar:low_quality")
        return None
    primary_score = _structure_score(primary.text)
    candidate_score = _structure_score(candidate.text)
    if candidate_score <= primary_score and len(candidate.text) < len(primary.text) * 1.1:
        return None
    candidate.errors = [*primary.errors, *candidate.errors, "sidecar:defuddle_selected_for_structure"]
    return candidate

def _extract_site_specific(html: str, *, url: str, title: str) -> ExtractionResult | None:
    host = urlparse(url or "").netloc.lower()
    if "yuque.com" in host:
        return _extract_yuque(html, url=url, title=title)
    return None


def _extract_trafilatura(html: str, *, url: str, title: str) -> ExtractionResult:
    import trafilatura

    metadata: dict[str, Any] = {}
    text = trafilatura.extract(
        html,
        url=url or None,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        include_links=True,
        include_images=True,
        with_metadata=False,
    ) or ""
    try:
        raw_json = trafilatura.extract(
            html,
            url=url or None,
            output_format="json",
            include_comments=False,
            include_tables=True,
            include_links=True,
            include_images=True,
            with_metadata=True,
        )
        if raw_json:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                metadata = {k: v for k, v in parsed.items() if k != "text"}
                if not text:
                    text = str(parsed.get("text") or "")
    except Exception as exc:
        metadata["metadata_error"] = f"{type(exc).__name__}:{str(exc)[:120]}"
    return ExtractionResult(text=_normalize_markdown(text), engine="trafilatura", metadata=metadata)


def _extract_readability(html: str, *, url: str, title: str, coverage_recovery: bool = False) -> ExtractionResult:
    from readability import Document

    options = {}
    if coverage_recovery:
        # Use the library's lenient retry for article-heading IDs such as
        # "community" or "toolchain"; retain normal scoring and sanitization.
        options = {"min_text_length": 1, "retry_length": len(html) + 1,
                   "positive_keywords": re.compile(r"^tag-h[1-6]$")}
    document = Document(html, url=url or None, **options)
    summary_html = document.summary(html_partial=True, keep_all_images=coverage_recovery)
    text = html_fragment_to_markdown(summary_html, base_url=url)
    metadata = {
        "title": clean_text(document.short_title() or title),
    }
    return ExtractionResult(text=text, engine="readability", metadata=metadata)


def _extract_builtin(html: str, *, url: str, title: str) -> ExtractionResult:
    return ExtractionResult(text=extract_main_text(html), engine="builtin", metadata={})


def _normalize_markdown(text: object) -> str:
    value = str(text or "")
    value = re.sub(r"\r\n?", "\n", value)
    value = "\n".join(line.rstrip() for line in value.split("\n"))
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def html_fragment_to_markdown(html: str, *, base_url: str = "") -> str:
    root = parse_html(html)
    blocks: list[str] = []
    _render_generic_markdown_blocks(root, base_url, blocks)
    return _join_markdown_blocks(blocks)


GENERIC_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "button"}
GENERIC_CONTAINER_TAGS = {"document", "html", "body", "main", "article", "section", "div", "header", "footer", "figure"}


def _render_generic_markdown_blocks(node: Node, base_url: str, blocks: list[str]) -> None:
    if node.tag in GENERIC_SKIP_TAGS:
        return
    heading_level = _generic_heading_level(node)
    if heading_level:
        text = _inline_generic_markdown(node, base_url)
        if text:
            blocks.append(f"{'#' * heading_level} {text}")
        return
    if node.tag == "img":
        image = _generic_image_markdown(node, base_url)
        if image:
            blocks.append(image)
        return
    if node.tag in {"p", "figcaption"}:
        text = _inline_generic_markdown(node, base_url)
        if text:
            # A numbered HTML paragraph is not an ordered-list item. Preserve
            # its literal marker when Markdown is rendered or compared.
            text = re.sub(r"^(\d{1,9})([.)])(\s)", r"\1\\\2\3", text)
            blocks.append(text)
        return
    if node.tag == "li":
        text = _inline_generic_markdown(node, base_url)
        if text:
            blocks.append(f"- {text}")
        return
    if node.tag == "blockquote":
        text = _inline_generic_markdown(node, base_url)
        if text:
            blocks.append("\n".join(f"> {line}" for line in text.splitlines() if line.strip()))
        return
    if node.tag == "pre":
        from .completeness import _text
        text = _text(node).strip("\r\n")
        if text.strip():
            fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
            blocks.append(f"{fence}\n{text}\n{fence}")
        return
    if node.tag in GENERIC_CONTAINER_TAGS or node.children:
        for child in node.children:
            _render_generic_markdown_blocks(child, base_url, blocks)
        return
    text = _inline_generic_markdown(node, base_url)
    if text:
        blocks.append(text)


def _generic_heading_level(node: Node) -> int:
    match = re.fullmatch(r"h([1-6])", node.tag)
    return int(match.group(1)) if match else 0


def _inline_generic_markdown(node: Node, base_url: str) -> str:
    parts: list[str] = []

    def walk(cur: Node) -> None:
        if cur.tag in GENERIC_SKIP_TAGS:
            return
        if cur.tag == "br":
            parts.append("\n")
            return
        if cur.tag == "img":
            image = _generic_image_markdown(cur, base_url)
            if image:
                parts.append(image)
            return
        if cur.tag == "a":
            label_parts: list[str] = []
            for item in _node_contents(cur):
                label_parts.append(item if isinstance(item, str) else _inline_generic_markdown(item, base_url))
            label = clean_text(" ".join(label_parts))
            href = cur.attr("href")
            if label and href:
                parts.append(f"[{label}]({urljoin(base_url, href)})")
            elif label:
                parts.append(label)
            return
        for item in _node_contents(cur):
            if isinstance(item, str):
                parts.append(item)
            else:
                walk(item)

    walk(node)
    text = " ".join(clean_text(part) if not _looks_like_markdown_image(part) else part for part in parts if part)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return " ".join(text.split())


def _generic_image_markdown(node: Node, base_url: str) -> str:
    src = node.attr("src") or node.attr("data-src") or node.attr("data-original")
    if not src:
        srcset = node.attr("srcset")
        if srcset:
            src = srcset.split(",", 1)[0].strip().split(" ", 1)[0]
    if not src:
        return ""
    alt = clean_text(node.attr("alt") or node.attr("title") or "image")
    return f"![{alt}]({urljoin(base_url, src)})"


def _extract_yuque(html: str, *, url: str, title: str) -> ExtractionResult:
    root = parse_html(html)
    content = _first_node(
        root,
        (
            "article#content div.ne-viewer-body",
            "div.yuque-doc-content div.ne-viewer-body",
            "article#content",
            "div.yuque-doc-content",
            "body",
        ),
    ) or root
    blocks: list[str] = []
    stats = {"blocks": 0, "headings": 0, "images": 0, "paragraphs": 0, "list_items": 0, "tables": 0}
    _render_yuque_blocks(content, url, blocks, stats)
    text = _join_markdown_blocks(blocks)
    if not text:
        text = extract_main_text(html, max_chars=60000)
    metadata = {
        "title": title,
        "site": "Yuque",
        "site_profile": "yuque_doc",
        "block_count": stats["blocks"],
        "heading_count": stats["headings"],
        "image_count": stats["images"],
        "paragraph_count": stats["paragraphs"],
        "list_item_count": stats["list_items"],
        "table_count": stats["tables"],
    }
    return ExtractionResult(text=text, engine="yuque", metadata=metadata)


def _first_node(root: Node, selectors: tuple[str, ...]) -> Node | None:
    for selector in selectors:
        nodes = select_nodes(root, selector)
        if nodes:
            return nodes[0]
    return None


YUQUE_CONTAINER_TAGS = {
    "document",
    "html",
    "body",
    "main",
    "article",
    "section",
    "div",
    "span",
    "ne-viewer",
    "ne-viewer-body",
    "ne-viewer-header",
    "ne-heading-content",
    "ne-uli-c",
    "ne-oli-c",
    "ne-alert-hole",
    "ne-alert",
    "ne-alert__content",
}
YUQUE_SKIP_TAGS = {"script", "style", "svg", "button", "ne-heading-ext", "ne-heading-anchor", "ne-heading-fold"}


def _render_yuque_blocks(node: Node, base_url: str, blocks: list[str], stats: dict[str, int]) -> None:
    if node.tag in YUQUE_SKIP_TAGS:
        return
    if _is_yuque_table(node):
        table = _yuque_table_markdown(node, base_url)
        if table:
            _append_yuque_block(blocks, stats, table, "table")
            stats["tables"] = stats.get("tables", 0) + 1
        return
    if _is_yuque_image_card(node):
        image = _yuque_image_markdown(node, base_url)
        if image:
            _append_yuque_block(blocks, stats, image, "image")
            stats["images"] += 1
        return
    heading_level = _yuque_heading_level(node)
    if heading_level:
        text = _inline_yuque_text(node)
        if text:
            _append_yuque_block(blocks, stats, f"{'#' * heading_level} {text}", "heading")
            stats["headings"] += 1
        return
    if node.tag in {"p", "ne-p"}:
        text = _inline_yuque_markdown(node, base_url)
        if text:
            _append_yuque_block(blocks, stats, text, "paragraph")
            stats["images"] += _count_yuque_images(node)
        return
    if node.tag in {"li", "ne-uli", "ne-oli"}:
        text = _inline_yuque_markdown(node, base_url)
        if text:
            _append_yuque_block(blocks, stats, f"- {text}", "list_item")
            stats["images"] += _count_yuque_images(node)
        return
    if node.tag in YUQUE_CONTAINER_TAGS or node.children:
        for child in node.children:
            _render_yuque_blocks(child, base_url, blocks, stats)
        return
    text = _inline_yuque_text(node)
    if text:
        _append_yuque_block(blocks, stats, text, "paragraph")


def _append_yuque_block(blocks: list[str], stats: dict[str, int], text: str, kind: str) -> None:
    value = text.strip()
    if not value:
        return
    blocks.append(value)
    stats["blocks"] = stats.get("blocks", 0) + 1
    if kind == "paragraph":
        stats["paragraphs"] = stats.get("paragraphs", 0) + 1
    elif kind == "list_item":
        stats["list_items"] = stats.get("list_items", 0) + 1


def _yuque_heading_level(node: Node) -> int:
    match = re.fullmatch(r"(?:ne-)?h([1-6])", node.tag)
    return int(match.group(1)) if match else 0


def _is_yuque_image_card(node: Node) -> bool:
    if node.tag == "img":
        return True
    return node.tag == "ne-card" and node.attr("data-card-name") == "image"


def _is_yuque_table(node: Node) -> bool:
    if node.tag in {"table", "ne-table"}:
        return True
    return node.tag == "ne-card" and node.attr("data-card-name") == "table"


def _yuque_table_markdown(node: Node, base_url: str) -> str:
    rows = _descendants_with_tags(node, {"tr", "ne-tr", "ne-table-row"})
    parsed_rows: list[list[str]] = []
    for row in rows:
        cells = _descendants_with_tags(row, {"th", "td", "ne-th", "ne-td", "ne-table-cell"})
        values = [_escape_markdown_table_cell(_inline_yuque_markdown(cell, base_url) or _inline_yuque_text(cell)) for cell in cells]
        values = [value for value in values if value]
        if values:
            parsed_rows.append(values)
    if not parsed_rows:
        return ""
    width = max(len(row) for row in parsed_rows)
    normalized = [row + [""] * (width - len(row)) for row in parsed_rows]
    header = normalized[0]
    separator = ["---"] * width
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in normalized[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _descendants_with_tags(node: Node, tags: set[str]) -> list[Node]:
    matches: list[Node] = []
    for child in node.children:
        if child.tag in tags:
            matches.append(child)
            continue
        matches.extend(_descendants_with_tags(child, tags))
    return matches


def _escape_markdown_table_cell(value: str) -> str:
    return clean_text(value).replace("|", r"\|")


def _yuque_image_markdown(node: Node, base_url: str) -> str:
    image_node = node if node.tag == "img" else _first_descendant(node, "img")
    if image_node is None:
        return ""
    src = image_node.attr("src") or image_node.attr("data-src") or image_node.attr("data-original")
    if not src:
        return ""
    url = urljoin(base_url, src)
    alt = clean_text(image_node.attr("alt") or image_node.attr("title") or "image")
    width = image_node.attr("width")
    suffix = f"<!-- width={width} -->" if width else ""
    return f"![{alt}]({url}){suffix}".rstrip()


def _first_descendant(node: Node, tag: str) -> Node | None:
    for cur in iter_nodes(node):
        if cur.tag == tag:
            return cur
    return None


def _inline_yuque_text(node: Node) -> str:
    parts: list[str] = []

    def walk(cur: Node) -> None:
        if cur.tag in YUQUE_SKIP_TAGS or _is_yuque_image_card(cur):
            return
        parts.extend(cur.texts)
        for child in cur.children:
            walk(child)

    walk(node)
    text = clean_text(" ".join(parts))
    noise = (
        "返回文档",
        "Press space bar to start a drag.",
        "When dragging you can use the arrow keys to move the item around and escape to cancel.",
        "Some screen readers may require you to be in focus mode or to use your pass through key",
    )
    for item in noise:
        text = text.replace(item, " ")
    return clean_text(text)


def _inline_yuque_markdown(node: Node, base_url: str) -> str:
    parts: list[str] = []

    def walk(cur: Node) -> None:
        if cur.tag in YUQUE_SKIP_TAGS:
            return
        if _is_yuque_image_card(cur):
            image = _yuque_image_markdown(cur, base_url)
            if image:
                parts.append(image)
            return
        for item in _node_contents(cur):
            if isinstance(item, str):
                parts.append(item)
            else:
                walk(item)

    walk(node)
    return _clean_yuque_inline_parts(parts)


def _clean_yuque_inline_parts(parts: list[str]) -> str:
    cleaned = [part if _looks_like_markdown_image(part) else clean_text(part) for part in parts if str(part or "").strip()]
    text = " ".join(part for part in cleaned if part)
    noise = (
        "返回文档",
        "Press space bar to start a drag.",
        "When dragging you can use the arrow keys to move the item around and escape to cancel.",
        "Some screen readers may require you to be in focus mode or to use your pass through key",
    )
    for item in noise:
        text = text.replace(item, " ")
    return " ".join(text.split())


def _count_yuque_images(node: Node) -> int:
    return sum(1 for cur in iter_nodes(node) if cur.tag == "img")


def _node_contents(node: Node) -> list[object]:
    contents = getattr(node, "contents", None)
    if contents:
        return list(contents)
    return [*node.texts, *node.children]


def _looks_like_markdown_image(value: object) -> bool:
    return str(value or "").lstrip().startswith("![")


def _join_markdown_blocks(blocks: list[str]) -> str:
    cleaned: list[str] = []
    seen_consecutive = ""
    for block in blocks:
        value = block.strip()
        if not value:
            continue
        if value == seen_consecutive:
            continue
        cleaned.append(value)
        seen_consecutive = value
    return "\n\n".join(cleaned)


def _is_useful(result: ExtractionResult, *, title: str = "", url: str = "") -> bool:
    quality = quality_for(result.text, title=title, url=url, engine=result.engine, metadata=result.metadata)
    return quality.get("status") == "ok"


def quality_for(text: str, *, title: str = "", url: str = "", engine: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    cleaned = clean_text(text)
    text_chars = len(cleaned)
    metadata = metadata or {}
    reasons: list[str] = []
    status = "ok"
    if text_chars == 0:
        status = "empty"
        reasons.append("no extracted text")
    elif text_chars < MIN_USEFUL_CHARS:
        status = "too_short"
        reasons.append(f"extracted text shorter than {MIN_USEFUL_CHARS} chars")

    lowered_title = (title or "").lower()
    lowered_text = cleaned.lower()
    host = urlparse(url or "").netloc.lower()
    shell_titles = ("notion", "腾讯文档", "语雀", "飞书云文档")
    shell_hosts = ("notion.so", "yuque.com", "docs.qq.com", "feishu.cn")
    shell_markers = (
        "loading",
        "加载中",
        "login",
        "登录/注册",
        "当前工作表",
        "notion-app",
        "帮助中心",
    )
    dynamic_doc_hint = (
        any(item in lowered_title for item in shell_titles)
        or any(item in host for item in shell_hosts)
    )
    shell_text_hint = any(item in lowered_text for item in shell_markers)
    feishu_login_shell = (
        ("feishu.cn" in host or "larksuite.com" in host)
        and (
            "飞书 - 登录" in (title or "")
            or "lark - login" in lowered_title
            or "feishu - login" in lowered_title
            or "title: feishu - log in" in lowered_text
            or "title: lark - log in" in lowered_text
        )
    )
    if feishu_login_shell:
        status = "suspect_shell"
        reasons.append("feishu/lark login shell likely needs browser login or document permission")
    if text_chars < 300 and (dynamic_doc_hint or shell_text_hint):
        status = "suspect_shell"
        reasons.append("dynamic document shell likely needs browser/session/backend fallback")

    structure_summary: dict[str, Any] = {}
    if metadata.get("site_profile") == "yuque_doc" or engine == "yuque":
        structure_summary = {
            "block_count": int(metadata.get("block_count") or 0),
            "heading_count": int(metadata.get("heading_count") or 0),
            "image_count": int(metadata.get("image_count") or 0),
            "paragraph_count": int(metadata.get("paragraph_count") or 0),
            "list_item_count": int(metadata.get("list_item_count") or 0),
        }
        if text_chars >= MIN_USEFUL_CHARS and structure_summary["block_count"] < 3:
            status = "suspect_incomplete"
            reasons.append("structured document extraction produced fewer than 3 content blocks")
        elif text_chars >= MIN_USEFUL_CHARS and structure_summary["heading_count"] == 0:
            structure_summary["warning"] = "no headings detected"
    elif engine == "browser_visible_dom" and int(metadata.get("block_count") or 0) > 0:
        structure_summary = {
            "block_count": int(metadata.get("block_count") or 0),
            "heading_count": int(metadata.get("heading_count") or 0),
            "image_count": int(metadata.get("image_count") or 0),
            "paragraph_count": int(metadata.get("paragraph_count") or 0),
            "list_item_count": int(metadata.get("list_item_count") or 0),
        }
        if text_chars >= MIN_USEFUL_CHARS and structure_summary["block_count"] < 3:
            status = "suspect_incomplete"
            reasons.append("browser-visible document extraction produced fewer than 3 content blocks")

    recommendation = ""
    if status in {"empty", "too_short", "suspect_shell", "suspect_incomplete"}:
        recommendation = "retry with browser/profile or a site-specific backend such as Crawl4AI"
    quality = {
        "engine": engine,
        "status": status,
        "text_chars": text_chars,
        "reasons": reasons,
        "recommendation": recommendation,
    }
    if structure_summary:
        quality["structure"] = structure_summary
    return quality
