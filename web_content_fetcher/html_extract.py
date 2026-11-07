from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any


IGNORED_TEXT_TAGS = {"script", "style", "noscript", "template"}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    contents: list[Any] = field(default_factory=list)
    parent: "Node | None" = None
    source_line: int | None = None
    source_column: int | None = None

    def classes(self) -> set[str]:
        return {part for part in self.attrs.get("class", "").split() if part}

    def attr(self, name: str, default: str = "") -> str:
        return self.attrs.get(name, default)


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        line, column = self.getpos()
        node = Node(tag, {k.lower(): v or "" for k, v in attrs}, parent=self.stack[-1],
                    source_line=line, source_column=column)
        self.stack[-1].children.append(node)
        self.stack[-1].contents.append(node)
        if tag in VOID_TAGS:
            return
        self.stack.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        for idx in range(len(self.stack) - 1, 0, -1):
            if self.stack[idx].tag == tag:
                del self.stack[idx:]
                break

    def handle_startendtag(self, tag, attrs):
        line, column = self.getpos()
        node = Node(tag.lower(), {k.lower(): v or "" for k, v in attrs}, parent=self.stack[-1],
                    source_line=line, source_column=column)
        self.stack[-1].children.append(node)
        self.stack[-1].contents.append(node)

    def handle_data(self, data):
        if data:
            self.stack[-1].texts.append(data)
            self.stack[-1].contents.append(data)


def parse_html(html: str) -> Node:
    parser = TreeBuilder()
    parser.feed(html or "")
    parser.close()
    return parser.root


def clean_text(text: object) -> str:
    value = str(text or "")
    value = unescape(value)
    value = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", value, flags=re.S)
    value = re.sub(r"<script\b.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(value.split())


def node_text(node: Node) -> str:
    parts: list[str] = []

    def walk(cur: Node):
        if cur.tag in IGNORED_TEXT_TAGS:
            return
        parts.extend(cur.texts)
        for child in cur.children:
            walk(child)

    walk(node)
    return clean_text(" ".join(parts))


def iter_nodes(root: Node):
    yield root
    for child in root.children:
        yield from iter_nodes(child)


def _match_simple(node: Node, token: str) -> bool:
    token = token.strip()
    if not token:
        return False
    if token == "*":
        return True
    if token.startswith("#"):
        return node.attr("id") == token[1:]
    if token.startswith("."):
        return token[1:] in node.classes()
    if "." in token:
        tag, cls = token.split(".", 1)
        return node.tag == tag.lower() and cls in node.classes()
    if "#" in token:
        tag, ident = token.split("#", 1)
        return node.tag == tag.lower() and node.attr("id") == ident
    return node.tag == token.lower()


def select_nodes(root: Node, selector: str) -> list[Node]:
    """Small CSS-like selector subset: descendant selectors, tag, .class, #id."""
    tokens = [part for part in re.split(r"\s+", selector.strip()) if part]
    if not tokens:
        return []
    current = [root]
    for token in tokens:
        found: list[Node] = []
        for node in current:
            for candidate in iter_nodes(node):
                if candidate is node:
                    continue
                if _match_simple(candidate, token):
                    found.append(candidate)
        current = found
    return current


def collect_links(node: Node, base_url: str) -> list[str]:
    links: list[str] = []
    seen = set()
    for cur in iter_nodes(node):
        href = cur.attr("href")
        if not href:
            continue
        absolute = urllib.parse.urljoin(base_url, href)
        if absolute not in seen:
            seen.add(absolute)
            links.append(absolute)
    return links


def collect_images(node: Node, base_url: str) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    seen = set()
    for cur in iter_nodes(node):
        if cur.tag != "img":
            continue
        src = cur.attr("src") or cur.attr("data-src") or cur.attr("data-original")
        if not src:
            srcset = cur.attr("srcset")
            if srcset:
                src = srcset.split(",", 1)[0].strip().split(" ", 1)[0]
        if not src:
            continue
        absolute = urllib.parse.urljoin(base_url, src)
        if absolute in seen:
            continue
        seen.add(absolute)
        images.append(
            {
                "url": absolute,
                "alt": cur.attr("alt"),
                "title": cur.attr("title"),
                "width": cur.attr("width"),
                "height": cur.attr("height"),
            }
        )
    return images


def title_from_html(html: str, root: Node | None = None) -> str:
    root = root or parse_html(html)
    title_nodes = select_nodes(root, "title")
    if title_nodes:
        return node_text(title_nodes[0])
    match = re.search(r"<title[^>]*>(.*?)</title>", html or "", flags=re.I | re.S)
    return clean_text(match.group(1)) if match else ""


def extract_main_text(html: str, *, max_chars: int = 12000) -> str:
    root = parse_html(html)
    for selector in ("article", "main", "div.article", "div.content", "div.post", "body"):
        nodes = select_nodes(root, selector)
        texts = [node_text(node) for node in nodes[:3]]
        text = "\n\n".join(part for part in texts if part)
        if len(text) >= 120 or selector == "body":
            return text[:max_chars]
    return clean_text(html)[:max_chars]


def extract_selector_results(html: str, base_url: str, selectors: list[dict], *, max_matches_default: int = 5) -> list[dict]:
    root = parse_html(html)
    results: list[dict] = []
    for spec in selectors or []:
        selector = str(spec.get("selector") or "").strip()
        if not selector:
            continue
        max_matches = int(spec.get("max_matches") or max_matches_default)
        for idx, node in enumerate(select_nodes(root, selector)[:max_matches], start=1):
            results.append(
                {
                    "selector": selector,
                    "name": spec.get("name") or selector,
                    "index": idx,
                    "text": node_text(node),
                    "links": collect_links(node, base_url),
                }
            )
    return results
