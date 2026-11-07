"""Bounded, offline projections of saved WCF captures, not a platform crawler."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .completeness import _body, _markdown_text, _normal, _text, inspect_capture
from .html_extract import parse_html
from .site_strategies import classify_site_strategy
from .xhs_backend import extract_initial_state_note, normalize_note

SCHEMA = 'wcf-platform-samples.v1'
MAX_BYTES = 16 * 1024 * 1024
CHANNELS = ('wechat', 'xiaohongshu')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_url(url: str) -> str:
    """Keep article identity, never echo authentication/challenge query values."""
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or p.username or p.password:
        raise ValueError('invalid_public_url')
    host = (p.hostname or '').lower()
    path = p.path or '/'
    query = []
    if host == 'mp.weixin.qq.com' and path.rstrip('/') == '/s':
        query = sorted((k, v) for k, v in parse_qsl(p.query) if k in {'__biz', 'mid', 'idx', 'sn'})
    if host in {'xiaohongshu.com', 'www.xiaohongshu.com'}:
        host = 'www.xiaohongshu.com'
        path = path.replace('/discovery/item/', '/explore/', 1)
    return urlunsplit(('https', host, path, urlencode(query), ''))


def channel_of(url: str) -> str:
    host = (urlsplit(url).hostname or '').lower()
    if host == 'mp.weixin.qq.com':
        return 'wechat'
    domains = ('xiaohongshu.com', 'xhslink.com', 'xhslink.cn', 'rednote.com')
    if any(host == domain or host.endswith('.' + domain) for domain in domains):
        return 'xiaohongshu'
    raise ValueError('unsupported_channel')


def _read(path: str | Path, roots: list[Path], expected: str = '') -> bytes:
    p = Path(path).absolute()
    if any(n.is_symlink() or getattr(n, 'is_junction', lambda: False)() for n in (p, *p.parents)):
        raise ValueError('linked_input_path')
    resolved = p.resolve(strict=True)
    if not any(resolved.is_relative_to(root.resolve(strict=True)) for root in roots):
        raise ValueError('input_outside_allowed_roots')
    if not resolved.is_file() or resolved.stat().st_size > MAX_BYTES:
        raise ValueError('input_size_or_type')
    with resolved.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or expected and digest(data) != expected:
        raise ValueError('input_size_or_digest_mismatch')
    return data


def _time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timestamp_requires_timezone')
    return dt


def _state(status: str, reason: str, **extra) -> dict:
    return {'status': status, 'reason': reason, **extra}


def project_sample(entry: dict, topic: dict, *, roots: list[Path], now: datetime,
                   max_age_seconds: int = 604800) -> dict:
    url = canonical_url(entry['url']) if entry.get('url') else ''
    channel = channel_of(url) if url else entry['channel']
    if channel not in CHANNELS or entry.get('channel', channel) != channel:
        raise ValueError('channel_mismatch')
    row = {'sample_id': entry['sample_id'], 'topic_id': topic['id'], 'query': topic['query'],
           'research_ref': topic.get('research_ref', ''), 'upstream_quality': topic.get('upstream_quality', 'unknown'),
           'url': url, 'canonical_url': url, 'channel': channel, 'captured_at': None,
           'evidence_kind': entry.get('evidence_kind', 'real'), 'rights_scope': entry.get('rights_scope', 'unknown'),
           'research_comparison_only': True, 'publish_authorized': False, 'insurance_fact_authority': False,
           'source_ref': entry.get('fetch_result_path', ''), 'source_sha256': None,
           'recovery_of': entry.get('recovery_of'), 'freshness': 'unknown', 'transport_ok': False,
           'source_order': [], 'native_source_ref': None, 'body_hash': None, 'raw_hash': None,
           'selection': 'preferred', 'diagnostics': [], 'full_original_verified': False}
    route = classify_site_strategy(url) if url else None
    row['route'] = {'site_key': route.site_key, 'allowed_transports': route.allowed_transports} if route else {}
    row['recovery'] = {'automatic': False, 'action': 'supply_authorized_capture',
                       'required_fields': ['url', 'fetch_result_path', 'fetch_result_sha256', 'rights_scope', 'recovery_of'],
                       'accepted_inputs': ['WCF_FetchResult_JSON', 'authorized_saved_HTML_via_WCF', 'original_image_files'],
                       'command': 'python -m web_content_fetcher.platform_samples --input INPUT.json --output NEW_OUTPUT --allowed-root AUTHORIZED_ROOT',
                       'preserve_failed_archive': True}
    missing = not entry.get('fetch_result_path')
    block = 'no_channel_sample_supplied' if missing else ''
    if row['rights_scope'] not in {'public_readonly', 'authorized_local', 'synthetic'}:
        block = 'rights_scope_unknown'
    if row['evidence_kind'] not in {'real', 'synthetic'} or (row['rights_scope'] == 'synthetic') != (row['evidence_kind'] == 'synthetic'):
        raise ValueError('evidence_kind_rights_mismatch')
    result = {}
    if not block:
        try:
            if not entry.get('fetch_result_sha256'):
                raise ValueError('fetch_result_digest_required')
            data = _read(entry['fetch_result_path'], roots, entry['fetch_result_sha256'])
            row['source_sha256'] = digest(data)
            result = json.loads(data.decode('utf-8-sig'))
            result = result.get('result', result)
            if canonical_url(result.get('requested_url') or result.get('url') or '') != url:
                raise ValueError('capture_url_mismatch')
        except (OSError, ValueError, TypeError):
            block = 'saved_capture_unreadable_or_unverified'
    raw = str(result.get('raw_snapshot') or '')
    text = str(result.get('extracted_text') or '')
    row['captured_at'] = result.get('fetched_at')
    try:
        age = (now - _time(row['captured_at'])).total_seconds()
        row['freshness'] = 'fresh' if 0 <= age <= max_age_seconds else 'expired' if age > max_age_seconds else 'future_timestamp'
    except (ValueError, TypeError, AttributeError):
        pass
    row['transport_ok'] = result.get('http_status') == 200
    row['raw_hash'] = digest(raw.encode()) if raw else None
    if raw and result.get('raw_sha256') and result['raw_sha256'] != row['raw_hash']:
        row['diagnostics'].append('raw_bytes_digest_differs_from_decoded_html_digest')
    assets = []
    for asset in (result.get('image_assets') or [])[:128]:
        try:
            _read(asset.get('asset_path', ''), roots, asset.get('asset_sha256') or 'missing')
            assets.append(asset)
        except (OSError, ValueError, TypeError):
            row['diagnostics'].append('asset_unverified_or_outside_allowed_roots')
    capture = inspect_capture(raw, text, page_url=url, final_url=result.get('final_url', url),
                              http_status=result.get('http_status'), payload=result.get('payload'), image_assets=assets)
    quality = result.get('extraction_quality') or {}
    if capture['access']['status'] == 'access_blocked':
        block = capture['access']['reason']
    elif quality.get('login_redirect_detected') or result.get('status') in {'access_blocked', 'login_required'}:
        block = 'login_or_access_restricted'
    row['capture_completeness'] = {k: capture[k] for k in ('status', 'comparison_scope', 'payload_truncated')}
    body = _body(parse_html(raw)) if not block else None
    body_text = _text(body).strip() if body else ''
    basis = 'saved_DOM_only'
    # Native XHS description and image sequence are not an HTML or detached gallery reconstruction.
    note = extract_initial_state_note(raw, url) if channel == 'xiaohongshu' and not block else {}
    if note:
        native = normalize_note(note, source_url=url, source='saved_initial_state')
        body_text = native['description']
        basis = 'saved_XHS_native_description_only'
        row['native_source_ref'] = {'path': row['source_ref'], 'sha256': row['source_sha256'], 'pointer': 'raw_snapshot.__INITIAL_STATE__.note'}
        if not capture['body_images']['nodes']:
            from .completeness import _local_image
            mapping = {a.get('url'): a for a in assets}
            memo = {}
            capture['body_images']['nodes'] = [
                {'ordinal': i, 'node_id': f'native-image-{i}', 'source_sha256': row['raw_hash'],
                 'display': _local_image(image['url'], mapping, memo),
                 'original': {'availability': 'unknown', 'reason': 'native_variant_not_proven_original'}}
                for i, image in enumerate(native['images'], 1)]
            capture['body_images']['count'] = len(native['images'])
    known = bool(body_text)
    complete = known and _normal(body_text) in _normal(_markdown_text(text)) and not capture['dom_links']['missing']
    title_only = bool(result.get('title')) and _normal(body_text) == _normal(str(result['title']))
    if entry.get('content_origin') in {'ai_summary', 'title_only'} or title_only:
        complete = False
        known = False
        row['diagnostics'].append('summary_or_title_is_not_original')
    if quality.get('spa_shell_detected') or capture['payload_truncated']:
        complete = False
    if block:
        row['body'] = _state('blocked', block, chars=0, comparison_scope=basis)
    else:
        row['body'] = _state('available' if complete else 'partial', 'matches_saved_source' if complete else 'body_missing_or_incomplete',
                             chars=len(text), comparison_scope=basis, missing_link_labels=len(capture['dom_links']['missing']))
        row['body_hash'] = digest(text.encode()) if text else None
    row['source_order'] = capture['body_images']['nodes'] if not block else []
    # Keep resource paths/hashes, but never copy signed CDN query strings into the public projection.
    for node in row['source_order']:
        for key in ('page_url',):
            if node.get(key):
                node[key] = canonical_url(node[key])
        for role in ('display', 'original'):
            if node[role].get('url'):
                node[role]['url'] = canonical_url(node[role]['url'])
    for role, key in [('display_images', 'display'), ('original_images', 'original')]:
        nodes = row['source_order']
        count = sum(n[key].get('availability') == 'available' for n in nodes)
        total = capture['body_images']['count']
        status = 'blocked' if block else 'not_requested' if total == 0 and known else 'available' if total and count == total else 'partial'
        row[role] = _state(status, block or ('none_declared_in_saved_source' if total == 0 and known else 'local_verified' if status == 'available' else 'missing_or_unverified_resources'),
                           available_count=count, expected_count=None if block else total)
    for role in ('video', 'subtitles'):
        request = (entry.get('media') or {}).get(role) or {}
        state = _state('not_requested', 'media_access_not_requested')
        if request.get('requested'):
            state = _state('blocked', 'media_access_not_authorized')
            if request.get('authorized') and not block:
                try:
                    if not request.get('sha256'):
                        raise ValueError('missing_digest')
                    data = _read(request['path'], roots, request['sha256'])
                    if not data:
                        raise ValueError('empty_media')
                    state = _state('available', 'local_file_digest_verified_not_playback_or_timing_validated',
                                   path=request['path'], sha256=digest(data), bytes=len(data))
                except (KeyError, OSError, ValueError, TypeError):
                    state = _state('partial', 'authorized_file_missing_or_unverified')
        row[role] = state
    row['recovery']['action'] = 'manual_capture_required' if block else 'supply_missing_components_or_refresh' if not complete or row['freshness'] != 'fresh' or row['original_images']['status'] == 'partial' else 'review_saved_source'
    components_ready = all(row[k]['status'] in {'available', 'not_requested'} for k in ('display_images', 'original_images', 'video', 'subtitles'))
    row['sample_status'] = 'blocked' if block else 'available' if complete and row['freshness'] == 'fresh' and components_ready else 'partial'
    return row


def build_catalog(config: dict, *, roots: list[Path], now: datetime | None = None) -> dict:
    if config.get('schema_version') != 'wcf-platform-sample-input.v1':
        raise ValueError('input_schema_version')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now_requires_timezone')
    topics, entries = config['topics'], config.get('samples', [])
    if not 1 <= len(topics) <= 8 or len(entries) > 48 or len({t['id'] for t in topics}) != len(topics):
        raise ValueError('topic_or_sample_bound')
    if len({e['sample_id'] for e in entries}) != len(entries):
        raise ValueError('duplicate_sample_id')
    topic_map = {t['id']: t for t in topics}
    rows = []
    for entry in entries:
        if entry['topic_id'] not in topic_map:
            raise ValueError('unknown_topic')
        rows.append(project_sample(entry, topic_map[entry['topic_id']], roots=roots, now=now))
    by_id = {row['sample_id']: row for row in rows}
    for row in rows:
        if row['recovery_of']:
            prior = by_id.get(row['recovery_of'])
            if not prior or prior is row or (prior['topic_id'], prior['canonical_url']) != (row['topic_id'], row['canonical_url']):
                raise ValueError('invalid_recovery_reference')
    groups = {}
    for row in rows:
        key = (row['topic_id'], row['channel'], row['canonical_url'], row['evidence_kind'])
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        preferred = max(group, key=lambda r: (r['body']['status'] == 'available', r['freshness'] == 'fresh', r['body']['chars'], r['captured_at'] or ''))
        for row in group:
            if row is not preferred:
                row.update(selection='duplicate_reference', preferred_sample_id=preferred['sample_id'])
    coverage = []
    for topic in topics:
        for channel in CHANNELS:
            selected = [r for r in rows if r['topic_id'] == topic['id'] and r['channel'] == channel and r['selection'] == 'preferred']
            if len(selected) > 3:
                raise ValueError('max_three_samples_per_channel_topic')
            if not selected:
                row = project_sample({'sample_id': f"{topic['id']}-{channel}-missing", 'channel': channel,
                                      'rights_scope': 'public_readonly'}, topic, roots=roots, now=now)
                rows.append(row)
                selected = [row]
            real = [r for r in selected if r['evidence_kind'] == 'real']
            coverage.append({'topic_id': topic['id'], 'channel': channel,
                             'real_body_available': sum(r['body']['status'] == 'available' for r in real),
                             'fresh_real_samples': sum(r['sample_status'] == 'available' for r in real),
                             'status': 'available' if real and all(r['sample_status'] == 'available' for r in real) else 'partial_or_missing'})
    return {'schema_version': SCHEMA, 'generated_at': now.isoformat(), 'topics': topics, 'samples': rows,
            'coverage': coverage, 'publish_authorized': False, 'network_requests': 0,
            'assessment_scope': 'saved_local_evidence_not_live_platform_coverage'}


def write_catalog(catalog: dict, output: Path) -> None:
    """Exclusive output prevents a recovery run overwriting an earlier receipt."""
    output.mkdir(parents=True, exist_ok=False)
    (output / 'platform_samples.v1.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding='utf-8')
    columns = ['sample_id', 'topic_id', 'channel', 'captured_at', 'freshness', 'body', 'display_images', 'original_images', 'video', 'subtitles', 'recovery']
    lines = ['# Platform Sample Review', '', 'Saved evidence only. Synthetic fixtures and blocked pages are not live collection success.', '']
    for topic in catalog['topics']:
        lines.append(f"Topic {topic['id']}: {topic['query']} (upstream quality: {topic.get('upstream_quality', 'unknown')})")
        lines.append('')
    lines.extend(['| ' + ' | '.join(columns) + ' |', '| ' + ' | '.join(['---'] * len(columns)) + ' |'])
    for row in catalog['samples']:
        values = []
        for col in columns:
            value = row.get(col) or 'unknown'
            if isinstance(value, dict):
                value = value.get('status', value.get('action')) + ': ' + value.get('reason', '')
            values.append(str(value).replace('|', '\\|').replace('\n', ' '))
        lines.append('| ' + ' | '.join(values) + ' |')
    for row in catalog['samples']:
        lines.extend(['', f"## {row['sample_id']}", '', f"Source: {row['canonical_url']} | evidence: {row['evidence_kind']} | selection: {row['selection']}", '',
                      f"Input: `{row['source_ref']}`", ''])
    (output / 'platform_samples.md').write_text('\n'.join(lines), encoding='utf-8')
    # Reuse Markdown conversion, no second webpage/article renderer.
    from markdown_it import MarkdownIt
    rendered = MarkdownIt('commonmark', {'html': False}).enable('table').render('\n'.join(lines))
    (output / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Platform Samples</title>'
                                      '<style>body{font:16px sans-serif;margin:24px}table{border-collapse:collapse;display:block;overflow:auto}td,th{border:1px solid #aaa;padding:8px}p{overflow-wrap:anywhere}</style>' + rendered, encoding='utf-8')
    templates = [{'sample_id': r['sample_id'] + '-recovered', 'topic_id': r['topic_id'], 'channel': r['channel'],
                  'url': r['canonical_url'], 'recovery_of': r['sample_id'] if r['canonical_url'] else None,
                  'fetch_result_path': '', 'fetch_result_sha256': '', 'rights_scope': 'unknown', 'evidence_kind': 'real'}
                 for r in catalog['samples'] if r['selection'] == 'preferred' and r['recovery']['action'] != 'review_saved_source']
    prior = [{'sample_id': r['sample_id'], 'topic_id': r['topic_id'], 'channel': r['channel'],
              'url': r['canonical_url'], 'fetch_result_path': r['source_ref'], 'fetch_result_sha256': r['source_sha256'],
              'rights_scope': r['rights_scope'], 'evidence_kind': r['evidence_kind']} for r in catalog['samples']]
    (output / 'recovery-input.template.json').write_text(json.dumps({'schema_version': 'wcf-platform-sample-input.v1',
                       'instructions': 'Keep prior entries unchanged. Fill only the relevant recovered entry with authorized WCF result path/hash and rights. Remove unused recovered placeholders. Use a NEW output directory.',
                       'topics': catalog['topics'], 'samples': prior + templates}, ensure_ascii=False, indent=2), encoding='utf-8')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--allowed-root', type=Path, action='append', required=True)
    args = parser.parse_args(argv)
    try:
        config = json.loads(_read(args.input, args.allowed_root).decode('utf-8-sig'))
        catalog = build_catalog(config, roots=args.allowed_root)
        write_catalog(catalog, args.output)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # Paths, raw responses and challenge tokens are not printed on failure.
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__}))
        return 2
    print(json.dumps({'status': 'ok', 'schema_version': SCHEMA, 'output': str(args.output), 'coverage': catalog['coverage']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
