"""Offline evidence comparison. URL declarations are never download proof."""
from __future__ import annotations

import hashlib
from itertools import islice
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

from .html_extract import IGNORED_TEXT_TAGS, Node, iter_nodes, parse_html


def access_boundary(raw: str, final_url: str = '', http_status: int | None = None) -> dict:
    parsed = urlsplit(final_url)
    route = parsed.path.lower()
    reason = ''
    if http_status in {401, 403}:
        reason = f'http_{http_status}_access_restricted'
    elif parsed.hostname == 'mp.weixin.qq.com' and route.rstrip('/') == '/mp/wappoc_appmsgcaptcha':
        reason = 'captcha_endpoint'
    elif re.search(r'<(?:input|iframe)\b[^>]*(?:captcha|recaptcha|hcaptcha)', raw, re.I) and re.search(r'<title[^>]*>\s*(?:安全验证|验证码|验证|security check|just a moment)', raw, re.I):
        reason = 'captcha_controls'
    return {'status': 'access_blocked' if reason else 'not_observed', 'reason': reason,
            'automatic_retry_allowed': not bool(reason), 'login_requirement': 'unknown'}


def _text(node: Node) -> str:
    if node.tag in IGNORED_TEXT_TAGS:
        return ''
    return ''.join(_text(part) if isinstance(part, Node) else str(part) for part in node.contents)


def _normal(text: str) -> str:
    return re.sub(r'\s+', '', text)


def _markdown_text(text: str) -> str:
    try:
        from markdown_it import MarkdownIt
        return _text(parse_html(MarkdownIt().enable('table').render(text)))
    except ImportError:
        return text


def _body_selection(root: Node) -> tuple[Node | None, str]:
    nodes = list(islice(iter_nodes(root), 20001))
    if len(nodes) > 20000:
        return None, 'node_budget_exceeded'
    for node in nodes:
        if node.attr('id') == 'js_content' or {'RichText', 'ztext'} <= node.classes():
            return node, 'existing_document_body_marker'
    markers = {'entrycontent', 'postcontent', 'articlecontent', 'articlebody',
               'postbody', 'contentbody', 'postcontentbody'}
    candidates = [n for n in nodes if n.tag in {'main', 'article', 'section', 'div'} and (
        'articleBody' in n.attr('itemprop').split() or any(
            re.sub('[^a-z]', '', token.lower()) in markers
            for token in [n.attr('id'), *n.classes()]))]
    outer = [n for n in candidates if not any(id(p) in {id(c) for c in candidates} for p in _parents(n))]
    if len(outer) == 1:
        return outer[0], 'explicit_body_marker'
    main = next((n for n in nodes if n.tag == 'main'), None)
    articles = [n for n in nodes if n.tag == 'article' and not _excluded(n, main)]
    articles = [n for n in articles if not any(p.tag == 'article' for p in _parents(n))]
    if len(articles) == 1 and not candidates:
        return articles[0], 'single_article'
    return main, 'main_conservative_fallback' if main else 'body_scope_unknown'


def _body(root: Node) -> Node | None:
    return _body_selection(root)[0]


def _parents(node):
    current = node.parent
    while current is not None:
        yield current
        current = current.parent


def _excluded(node: Node, body: Node | None) -> str:
    for current in (node, *_parents(node)):
        if current is body:
            break
        if current.tag in IGNORED_TEXT_TAGS or current.tag in {'nav', 'footer'}:
            return 'non_body_semantic:' + current.tag
        if current.attr('role') in {'navigation', 'contentinfo', 'complementary'}:
            return 'non_body_role:' + current.attr('role')
        markers = {re.sub('[^a-z]', '', token.lower()) for token in current.classes()}
        if markers & {'authorbio', 'authorcard', 'byline', 'postmeta', 'breadcrumb', 'breadcrumbs'}:
            return 'non_body_metadata'
        if current.attr('aria-hidden') == 'true' or 'hidden' in current.attrs:
            return 'explicitly_hidden'
    return ''


def _position(node, index):
    parts = []
    for current in (node, *_parents(node)):
        siblings = current.parent.children if current.parent else [current]
        ordinal = next(i for i, child in enumerate(siblings, 1) if child is current)
        parts.append(current.tag + '[' + str(ordinal) + ']')
    return {'dom_index': index, 'dom_path': '/'.join(reversed(parts)),
            'source_line': node.source_line, 'source_column': node.source_column}


def compare_body_blocks(body: Node | None, extracted_text: str) -> dict:
    plain = _normal(_markdown_text(extracted_text))
    nodes = list(islice(iter_nodes(body), 20001)) if body else []
    blocks, cursor = [], 0
    for index, node in enumerate(nodes, 1):
        if _excluded(node, body) or node.tag not in {'p', 'li', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'td', 'th', 'figcaption'}:
            continue
        # A block with nested blocks is checked at its leaves to avoid double counting.
        if any(n is not node and n.tag in {'p', 'li', 'pre', 'td', 'th'} for n in iter_nodes(node)):
            continue
        text = _text(node).strip()
        normalized = _normal(text)
        if not normalized:
            continue
        offset = plain.find(normalized, cursor)
        if offset >= 0:
            cursor = offset + len(normalized)
        blocks.append({**_position(node, index), 'tag': node.tag, 'text': text,
            'normalized_sha256': hashlib.sha256(normalized.encode()).hexdigest(),
            'output_normalized_span': [offset, offset + len(normalized)] if offset >= 0 else None,
            'match': 'matched_in_source_order' if offset >= 0 else 'missing_or_out_of_order'})
    missing = [b for b in blocks if b['output_normalized_span'] is None]
    return {'scope': 'saved_body_text_blocks', 'checked_blocks': len(blocks),
            'budget_exceeded': len(nodes) > 20000, 'nodes': blocks,
            'missing_block_sha256': [b['normalized_sha256'] for b in missing], 'missing': missing,
            'comparison_text_sha256': hashlib.sha256(plain.encode()).hexdigest()}


def _local_image(url: str, assets: dict, memo: dict) -> dict:
    result = {'url': url, 'availability': 'unknown', 'downloaded': False,
              'reason': 'not_downloaded' if url else 'source_not_declared'}
    if not url or url not in assets:
        return result
    asset = assets[url]
    path = Path(str(asset.get('asset_path') or ''))
    digest = str(asset.get('asset_sha256') or '')
    key = (str(path), digest)
    if key in memo:
        return {**result, **memo[key]}
    state = {'availability': 'unknown', 'downloaded': False, 'reason': 'missing_file_or_digest'}
    try:
        if not digest or not path.is_file() or path.stat().st_size > 15_000_000:
            return {**result, **state}
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            state.update(availability='invalid', reason='digest_mismatch')
        else:
            from PIL import Image
            with Image.open(path) as image:
                dimensions = list(image.size)
                image.verify()
            state.update(availability='available', downloaded=True, reason='local_digest_and_decode_verified',
                         path=str(path), sha256=digest, dimensions=dimensions)
    except ImportError:
        state.update(reason='image_validator_unavailable')
    except Exception:
        state.update(availability='invalid', reason='image_decode_failed')
    memo[key] = state
    return {**result, **state}


def inspect_capture(raw: str, extracted_text: str, *, page_url: str = '', final_url: str = '',
                    http_status: int | None = None, payload: dict | None = None,
                    image_assets: list | None = None) -> dict:
    access = access_boundary(raw, final_url, http_status)
    incomplete = bool((payload or {}).get('truncated') or (payload or {}).get('binary'))
    tree = parse_html(raw)
    body, selection = _body_selection(tree) if not incomplete and access['status'] != 'access_blocked' else (None, 'payload_or_access_limited')
    if body is not None and not _text(body).strip() and not any(n.tag == 'img' for n in iter_nodes(body)):
        body = None
    source_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    assets = {item.get('url'): item for item in image_assets or []}
    nodes, images, missing, excluded = [], [], [], []
    plain = _normal(_markdown_text(extracted_text))
    memo = {}
    cursor = 0
    for index, node in enumerate(iter_nodes(body) if body else [], 1):
        exclusion = _excluded(node, body)
        if exclusion:
            if node.tag in {'a', 'img', 'p'}:
                excluded.append({**_position(node, index), 'tag': node.tag, 'reason': exclusion,
                                 'text': _text(node).strip()[:500]})
            continue
        if node.tag == 'a' and _text(node).strip():
            item = {'ordinal': len(nodes) + 1, **_position(node, index), 'node_id': node.attr('id') or f'dom-{index}',
                    'text': _text(node).strip(), 'url': urljoin(final_url or page_url, node.attr('href'))}
            label = _normal(item['text'])
            offset = plain.find(label, cursor)
            item['output_normalized_span'] = [offset, offset + len(label)] if offset >= 0 else None
            nodes.append(item)
            if offset < 0:
                missing.append(item)
            else:
                cursor = offset + len(label)
        if node.tag == 'img':
            src = node.attr('src')
            lazy = node.attr('data-src') or node.attr('data-actualsrc')
            if lazy and (not src or src.startswith('data:')):
                src = lazy
            original = node.attr('data-original') or node.attr('data-original-src')
            display_url = urljoin(final_url or page_url, src) if src else ''
            original_url = urljoin(final_url or page_url, original) if original else ''
            images.append({'ordinal': len(images) + 1, **_position(node, index),
                           'node_id': node.attr('id') or f'dom-{index}', 'source_sha256': source_hash,
                           'page_url': page_url, 'display': _local_image(display_url, assets, memo),
                           'original': {**_local_image(original_url, assets, memo),
                                        'claim_basis': 'DOM_declared_original_not_independently_proven_maximum_resolution'}})
    known = body is not None
    displays = sum(i['display']['availability'] == 'available' for i in images)
    originals = sum(i['original']['downloaded'] for i in images)
    availability = 'unknown' if not known else ('not_applicable' if not images else 'available' if originals == len(images) else 'unknown')
    blocks = compare_body_blocks(body, extracted_text)
    text_missing = bool(missing or blocks['missing'] or blocks['budget_exceeded'])
    status = 'access_blocked' if access['status'] == 'access_blocked' else 'unknown_body' if not known else 'partial' if text_missing or displays != len(images) else 'complete_against_saved_dom'
    manual = {}
    if access['status'] == 'access_blocked' or not known:
        manual = {'action': 'manual_capture_required', 'automatic': False,
                  'accepted_inputs': ['authorized_saved_HTML', 'official_PDF_or_DOCX_export', 'original_image_files'],
                  'instructions': 'Stop automatic retries. Supply an authorized saved page/export and original images with the source URL; preserve this failed snapshot separately. No automatic login or challenge solving.'}
    elif text_missing or images and originals != len(images):
        manual = {'action': 'review_saved_dom_and_supply_originals', 'automatic': False,
                  'accepted_inputs': ['saved_HTML_with_link_labels', 'original_image_files'],
                  'instructions': 'Inspect missing labels at the recorded DOM indexes in raw_snapshot.html. Supply originals if available; do not append a detached gallery or claim display variants are originals.'}
    return {'schema_version': 'wcf-capture-completeness.v2', 'status': status,
            'checks': ['DOM_link_labels', 'body_text_blocks', 'body_image_variants'],
            'body_scope': {'selection': selection, 'node': _position(body, 1) if body else None,
                           'excluded_nodes': excluded, 'whole_page_completeness': 'not_verified'},
            'text_blocks': blocks,
            'text_status': 'unknown' if not known else 'partial' if text_missing else 'complete_against_saved_dom',
            'comparison_scope': 'saved_DOM_only_not_independent_full_original', 'source_sha256': source_hash,
            'access': access, 'payload_truncated': bool((payload or {}).get('truncated')),
            'dom_links': {'checked': known, 'nodes': nodes, 'missing': missing},
            'body_images': {'count': len(images) if known else None, 'nodes': images,
                            'display_available': displays if known else None,
                            'original_downloaded': originals if known else None, 'original_availability': availability},
            'manual_capture': manual}
