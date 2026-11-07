"""Source-bound body retention using WCF extraction and evidence archives.

CLI defaults are offline import/plan. Network recovery requires an explicit,
hash-bound URL allowlist and --execute; no browser or external provider is used.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from importlib.metadata import version, PackageNotFoundError

from .archive import atomic_write_bytes, atomic_write_json, sha256_file, write_archive
from .core import DirectPayload, FetchRequest, direct_fetch, structure_content, markdown_to_readable_html
from .public_http import public_url

SCHEMA = 'wcf-public-bodies.v1'
MAX_INPUT = 2 * 1024 * 1024
MAX_ENTRIES = 200
MAX_STORE_RECORDS = 2000


def processing_identity():
    dependencies = {}
    for name in ('trafilatura', 'markdown-it-py', 'readability-lxml', 'lxml'):
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            dependencies[name] = 'not_installed'
    identity = {'revision': 'saved-body-completeness-v2', 'dependencies': dependencies,
        'sources': {name: sha256_file(Path(__file__).parent / name) for name in (
            'public_bodies.py', 'completeness.py', 'html_extract.py', 'extractors.py', 'core.py')}}
    return {**identity, 'sha256': _key(identity)}


def _reference_key(entry, processing):
    return _key({'entry': entry, 'processing_sha256': processing['sha256']})


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _key(value):
    return _sha(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def _now():
    return datetime.now(timezone.utc).isoformat()


def _timestamp_or_none(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value if parsed.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _read(path, limit=MAX_INPUT):
    with Path(path).open('rb') as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError('input_byte_budget_exceeded')
    return data


def _json(path):
    return json.loads(_read(path, 8 * MAX_INPUT).decode('utf-8-sig'))


def _immutable(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path, 8 * MAX_INPUT) != data:
            raise ValueError('artifact_drift:' + path.name)
    else:
        atomic_write_bytes(path, data)


def _immutable_json(path, value):
    _immutable(path, json.dumps(value, ensure_ascii=False, indent=2).encode())


def _safe_path(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError('path_outside_root')
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or getattr(candidate, 'is_junction', lambda: False)():
            raise ValueError('linked_path')
        if candidate == root:
            break
    return path


@contextmanager
def _lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.body-store.lock').open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        import os
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _manifest(path):
    data = _read(path, MAX_INPUT)
    value = json.loads(data.decode('utf-8-sig'))
    rows = value.get('entries', [])
    if value.get('schema') != 'wcf-body-manifest.v1' or not 1 <= len(rows) <= MAX_ENTRIES:
        raise ValueError('invalid_body_manifest')
    ids = set()
    for row in rows:
        if not all(isinstance(row.get(k), str) and 0 < len(row[k]) <= 200 for k in ('id', 'topic_id')):
            raise ValueError('entry_identity_required')
        if (row['topic_id'], row['id']) in ids:
            raise ValueError('duplicate_manifest_entry')
        ids.add((row['topic_id'], row['id']))
        public_url(row['url'])
        if row.get('format') not in {'html', 'markdown', 'missing'}:
            raise ValueError('unsupported_saved_format')
        if row.get('rights_scope') not in {'public_readonly', 'synthetic'}:
            raise ValueError('public_or_synthetic_source_required')
        for key in ('collected_at', 'published_at'):
            if row.get(key) is not None and not isinstance(row[key], str):
                raise ValueError('invalid_source_time')
        if row.get('collected_at'):
            stamp = datetime.fromisoformat(row['collected_at'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('collection_time_requires_timezone')
    return rows, _sha(data)


def _state(root):
    path = root / 'index.json'
    state = _json(path) if path.exists() else {'schema': SCHEMA, 'records': {}}
    if state.get('schema') != SCHEMA or len(state['records']) > MAX_STORE_RECORDS:
        raise ValueError('invalid_or_oversized_body_store')
    return state


def _body(root, record):
    if record.get('capture_sha256'):
        capture = root / 'captures' / record['capture_id'] / 'capture.json'
        if sha256_file(capture) != record['capture_sha256']:
            raise ValueError('capture_receipt_drift')
        saved = _json(capture)
        if sha256_file(saved['archive']['raw_snapshot_path']) != saved['raw_sha256']:
            raise ValueError('raw_archive_drift')
    if not record.get('body_sha256'):
        return ''
    raw = _read(_safe_path(root, record['body_path']))
    if _sha(raw) != record['body_sha256']:
        raise ValueError('retained_body_drift')
    return raw.decode('utf-8')


def _capture(root, raw, content_type, url, final_url, status, truncated=False):
    processing = processing_identity()
    capture_id = _key([_sha(raw), content_type, url, final_url, status, truncated, processing['sha256']])
    receipt = root / 'captures' / capture_id / 'capture.json'
    if receipt.exists():
        saved = _json(receipt)
        archive = saved['archive']
        if sha256_file(archive['raw_snapshot_path']) != _sha(raw):
            raise ValueError('raw_archive_drift')
        _body(root, saved)
        return saved
    payload = DirectPayload(raw, content_type, status, truncated)
    request = FetchRequest(url=url, backend='direct', check_robots=False,
                           download_images=False, max_bytes=MAX_INPUT)
    result = structure_content(request, payload, content_type, final_url, 'saved_public_body', [])
    body = result.extracted_text
    body_hash = _sha(body.encode()) if body else None
    body_path = 'bodies/' + body_hash + '.md' if body_hash else None
    if body:
        _immutable(root / body_path, body.encode())
    saved = asdict(result)
    saved.pop('raw_snapshot', None)
    saved.pop('extracted_text', None)
    saved.update(capture_id=capture_id, body_sha256=body_hash, body_path=body_path,
                 retained_at=_now(), extraction_performed=True, processing=processing)
    if 'html' in content_type:
        saved['body_comparison'] = result.capture_completeness.get('text_blocks', {})
    # Raw observations and canonical body blobs have independent identities.
    # The per-capture text slot stays empty; metadata points to the shared body.
    archive = write_archive(root / 'captures', capture_id, url=url, title=result.title,
        raw_bytes=raw, extracted_text='', metadata={'schema': SCHEMA,
        'body_path': body_path, 'body_sha256': body_hash, 'body_stored_separately': True,
        'extraction_quality': result.extraction_quality, 'capture_completeness': result.capture_completeness})
    saved['archive'] = archive
    atomic_write_json(receipt, saved)
    return saved


def _assessment(saved, entry):
    if not saved or not saved.get('body_sha256'):
        return 'missing', ['no_usable_body']
    reasons = []
    quality = saved.get('extraction_quality') or {}
    if not saved.get('content_ok'):
        reasons.append('wcf_content_not_ok')
    if (saved.get('payload') or {}).get('truncated') or entry.get('truncated'):
        reasons.append('source_truncated')
    if entry.get('content_completeness') in {'excerpt_only', 'partial', 'unknown'}:
        reasons.append('source_declared_incomplete_or_unknown')
    if entry.get('format') == 'markdown' and entry.get('content_completeness') != 'full':
        reasons.append('markdown_fullness_not_attested')
    if 'html' in saved.get('content_type', ''):
        comparison = saved.get('body_comparison') or {}
        if not comparison.get('checked_blocks') or comparison.get('budget_exceeded') or comparison.get('missing_block_sha256'):
            reasons.append('saved_body_blocks_not_fully_covered')
    if quality.get('status') not in {'ok', 'good'}:
        reasons.append('extraction_quality:' + str(quality.get('status', 'unknown')))
    if saved.get('status') in {'access_blocked', 'failed', 'login_required'}:
        return 'missing', ['access_or_transport_failed']
    return ('partial', reasons) if reasons else ('full', ['passes_WCF_against_retained_source'])


def _record(root, entry, saved, manifest_hash, error=None, collected_at=None):
    complete, reasons = _assessment(saved, entry)
    if error:
        complete, reasons = 'missing', [error]
    return {'entry': entry, 'manifest_sha256': manifest_hash,
        'reference_id': _reference_key(entry, saved['processing'] if saved else processing_identity()),
        'processing': saved['processing'] if saved else processing_identity(),
        'url': entry['url'], 'topic_id': entry['topic_id'],
        'capture_id': saved.get('capture_id') if saved else None,
        'capture_sha256': sha256_file(root / 'captures' / saved['capture_id'] / 'capture.json') if saved else None,
        'body_sha256': saved.get('body_sha256') if saved else None,
        'body_path': saved.get('body_path') if saved else None,
        'collected_at': collected_at if collected_at is not None else entry.get('collected_at'),
        'published_at': entry.get('published_at'),
        'extracted_published_at': (saved.get('extracted_metadata') or {}).get('date') if saved else None,
        'retained_at': _now(), 'completeness': complete, 'completeness_basis': reasons,
        'completeness_scope': 'saved_body_text_not_images_or_whole_page',
        'completeness_dimensions': {'text': complete,
            'capture': (saved.get('capture_completeness') or {}).get('status', 'unknown') if saved else 'unknown',
            'images': (saved.get('capture_completeness', {}).get('body_images') or {}) if saved else {},
            'whole_page': 'not_verified'},
        'comparison_scope': 'retained_source_only', 'fact_verified': False,
        'synthetic': entry.get('rights_scope') == 'synthetic', 'error': error}


def import_manifest(manifest, store, input_root):
    rows, binding = _manifest(manifest)
    root = Path(store).resolve()
    with _lock(root):
        state = _state(root)
        processing = processing_identity()
        for entry in rows:
            key = _reference_key(entry, processing)
            saved, error = None, None
            try:
                if entry['format'] != 'missing':
                    source = _safe_path(input_root, entry['path'])
                    data = _read(source)
                    if not entry.get('sha256') or _sha(data) != entry['sha256']:
                        raise ValueError('saved_input_digest_mismatch')
                if key in state['records'] and not state['records'][key].get('error'):
                    _body(root, state['records'][key])
                    continue
                if entry['format'] != 'missing':
                    mime = 'text/html' if entry['format'] == 'html' else 'text/markdown'
                    saved = _capture(root, data, mime, entry['url'], entry.get('final_url', entry['url']),
                                     int(entry.get('http_status', 200)), bool(entry.get('truncated')))
            except (OSError, ValueError, KeyError) as exc:
                error = type(exc).__name__ + ':' + str(exc)[:100]
                if key in state['records']:
                    raise ValueError('previous_import_drift:' + entry['id']) from exc
            if len(state['records']) >= MAX_STORE_RECORDS:
                raise ValueError('store_record_budget')
            state['records'][key] = _record(root, entry, saved, binding, error)
            atomic_write_json(root / 'index.json', state)
        return report(root, state)


def report(root, state=None):
    root = Path(root)
    state = state or _state(root)
    counts = {key: 0 for key in ('full', 'partial', 'missing')}
    for row in state['records'].values():
        counts[row['completeness']] += 1
    result = {'schema': SCHEMA, 'store': str(root.resolve()), 'references': len(state['records']),
        'unique_bodies': len({r['body_sha256'] for r in state['records'].values() if r.get('body_sha256')}),
        'completeness': counts, 'fact_verified': False}
    atomic_write_json(root / 'summary.json', result)
    return result


def make_plan(store, topic_id=None):
    root = Path(store).resolve()
    state = _state(root)
    grouped = {}
    for key, row in state['records'].items():
        if row['completeness'] == 'full' or topic_id and row['topic_id'] != topic_id:
            continue
        task = grouped.setdefault(row['url'], {'url': row['url'], 'reference_ids': [], 'reasons': []})
        task['reference_ids'].append(key)
        task['reasons'].extend(row['completeness_basis'])
    return {'schema': 'wcf-body-recovery-plan.v1', 'store_index_sha256': sha256_file(root / 'index.json'),
        'automatic_execution': False, 'budget': {'max_urls': 15, 'attempts_per_url': 2, 'concurrency': 1},
        'tasks': list(grouped.values()), 'command': 'python -m web_content_fetcher.public_bodies recover --store STORE --plan PLAN --allowlist ALLOWLIST --execute'}


def recover(store, plan_path, allowlist_path, *, execute=False, max_items=15, timeout=15):
    root = Path(store).resolve()
    plan = _json(plan_path)
    allow = _json(allowlist_path)
    urls = allow.get('urls', [])
    if not 1 <= len(set(urls)) <= 15 or not 1 <= max_items <= 15 or not 1 <= timeout <= 60:
        raise ValueError('public_recovery_budget')
    for url in urls:
        public_url(url)
    if not execute:
        return {'status': 'plan_only', 'network_called': False, 'tasks': plan['tasks']}
    if len(plan['tasks']) > 15 or len({t['url'] for t in plan['tasks']}) != len(plan['tasks']):
        raise ValueError('recovery_plan_url_budget')
    if any(t['url'] not in urls for t in plan['tasks']):
        raise ValueError('url_not_in_explicit_allowlist')
    binding = _key([sha256_file(plan_path), sha256_file(allowlist_path)])
    journal = root / 'recovery' / (binding + '.json')
    journal.parent.mkdir(parents=True, exist_ok=True)
    with _lock(root):
        state = _state(root)
        if journal.exists():
            checkpoint = _json(journal)
            current = sha256_file(root / 'index.json')
            if current not in {checkpoint['last_index_sha256'], checkpoint.get('next_index_sha256')}:
                raise ValueError('recovery_index_drift')
            checkpoint['last_index_sha256'] = current
        else:
            if plan['store_index_sha256'] != sha256_file(root / 'index.json'):
                raise ValueError('recovery_plan_drift')
            checkpoint = {'binding': binding, 'tasks': {}, 'last_index_sha256': sha256_file(root / 'index.json')}
            atomic_write_json(journal, checkpoint)
        count = 0
        for task in plan['tasks']:
            url = task['url']
            for ref in task['reference_ids']:
                if ref not in state['records'] or state['records'][ref]['url'] != url:
                    raise ValueError('recovery_reference_mismatch')
            progress = checkpoint['tasks'].setdefault(url, {'attempts': [], 'status': 'pending'})
            if progress['status'] == 'done':
                for ref in task['reference_ids']:
                    _body(root, state['records'][ref])
                continue
            if count >= max_items:
                break
            count += 1
            start = len(progress['attempts'])
            if start and progress['attempts'][-1]['status'] == 'reserved_outcome_unknown':
                start -= 1
            for attempt in range(start, 2):
                attempt_path = root / 'recovery' / (binding + '-' + _key(url)[:16] + '-' + str(attempt) + '.json')
                fresh = attempt == len(progress['attempts'])
                if fresh:
                    pending = {'number': attempt, 'status': 'reserved_outcome_unknown', 'collected_at': _now()}
                    progress['attempts'].append(pending)
                    atomic_write_json(journal, checkpoint)
                else:
                    pending = progress['attempts'][attempt]
                    if not attempt_path.exists():
                        progress['status'] = 'outcome_unknown_no_auto_resend'
                        atomic_write_json(journal, checkpoint)
                        break
                try:
                    if fresh:
                        raw, mime, final_url = direct_fetch(FetchRequest(url=url, backend='direct', public_http_only=True,
                            timeout_seconds=timeout, max_bytes=1024 * 1024, check_robots=False))
                        raw_path = attempt_path.with_suffix('.raw')
                        atomic_write_bytes(raw_path, raw.raw_bytes)
                        response = {'path': raw_path.name, 'sha256': sha256_file(raw_path), 'content_type': mime,
                                    'final_url': final_url, 'http_status': raw.http_status, 'payload': raw.payload}
                        atomic_write_json(attempt_path, response)
                    else:
                        response = _json(attempt_path)
                        raw_data = _read(_safe_path(attempt_path.parent, response['path']))
                        if _sha(raw_data) != response['sha256']:
                            raise ValueError('saved_response_drift')
                        mime, final_url = response['content_type'], response['final_url']
                        raw = DirectPayload(raw_data, mime, response['http_status'], response['payload']['truncated'])
                    saved = _capture(root, raw.raw_bytes, mime, url, final_url, raw.http_status, raw.payload['truncated'])
                    for ref in task['reference_ids']:
                        old = state['records'][ref]
                        if old.get('capture_id') == saved['capture_id'] and old.get('collected_at') == pending['collected_at']:
                            continue
                        row = _record(root, {**old['entry'], 'format': 'html'}, saved,
                                      old['manifest_sha256'], collected_at=pending['collected_at'])
                        row['entry'] = old['entry']
                        row['reference_id'] = ref
                        row['previous_observations'] = old.get('previous_observations', []) + [
                            {k: v for k, v in old.items() if k not in {'entry', 'previous_observations'}}]
                        state['records'][ref] = row
                    checkpoint['next_index_sha256'] = _sha(json.dumps(state, ensure_ascii=False, indent=2).encode())
                    atomic_write_json(journal, checkpoint)
                    atomic_write_json(root / 'index.json', state)
                    checkpoint['last_index_sha256'] = sha256_file(root / 'index.json')
                    checkpoint.pop('next_index_sha256', None)
                    pending.update(status='captured', capture_id=saved['capture_id'])
                    progress['status'] = 'done'
                    atomic_write_json(journal, checkpoint)
                    break
                except Exception as exc:
                    # Retained bytes resume locally, without another HTTP attempt.
                    if attempt_path.exists():
                        pending['error'] = type(exc).__name__ + ':' + str(exc)[:180]
                        progress['status'] = 'local_processing_pending'
                        atomic_write_json(journal, checkpoint)
                        break
                    pending.update(status='failed', error=type(exc).__name__ + ':' + str(exc)[:180])
                    progress['status'] = 'done' if attempt == 1 else 'pending'
                    atomic_write_json(journal, checkpoint)
                    if type(exc).__name__ == 'PublicFetchBlocked':
                        progress['status'] = 'done'
                        atomic_write_json(journal, checkpoint)
                        break
            if progress['status'] != 'done' and any(a['status'] == 'reserved_outcome_unknown' for a in progress['attempts']):
                if progress['status'] != 'local_processing_pending':
                    progress['status'] = 'outcome_unknown_no_auto_resend'
                atomic_write_json(journal, checkpoint)
            for ref in task['reference_ids']:
                state['records'][ref]['recovery'] = progress
            checkpoint['next_index_sha256'] = _sha(json.dumps(state, ensure_ascii=False, indent=2).encode())
            atomic_write_json(journal, checkpoint)
            atomic_write_json(root / 'index.json', state)
            checkpoint['last_index_sha256'] = sha256_file(root / 'index.json')
            checkpoint.pop('next_index_sha256', None)
            atomic_write_json(journal, checkpoint)
        result = report(root, state)
        result['checkpoint_path'] = str(journal)
        result['attempts'] = sum(len(t['attempts']) for t in checkpoint['tasks'].values())
        return result


def export_topic(store, output, topic_id):
    root, output = Path(store).resolve(), Path(output).resolve()
    state = _state(root)
    rows = [r for r in state['records'].values() if r['topic_id'] == topic_id]
    if not rows:
        raise ValueError('topic_has_no_references')
    output.mkdir(parents=True, exist_ok=True)
    binding = {'schema': SCHEMA, 'topic_id': topic_id, 'records_sha256': _key(rows)}
    _immutable_json(output / 'binding.json', binding)
    pid, captures = {}, {}
    rows.sort(key=lambda row: row['retained_at'])
    for row in rows:
        text = _body(root, row)
        saved = _json(root / 'captures' / row['capture_id'] / 'capture.json') if row.get('capture_id') else {}
        content = {**saved, 'requested_url': row['url'], 'extracted_text': text,
            'final_url': saved.get('final_url', row['url']),
            'fetched_at': row['collected_at'], 'fixture': row['synthetic'],
            'status': 'ok' if row['completeness'] == 'full' else 'partial' if text else 'failed',
            'content_ok': row['completeness'] == 'full', 'content_completeness': row['completeness'],
            'retention': row, 'fact_verified': False}
        name = 'captures/' + row['reference_id'] + '.json'
        _immutable_json(output / name, content)
        pid[row['entry']['id']] = {'evidence_item_id': row['entry']['id'], 'title': row['entry'].get('title') or saved.get('title', ''),
            'url': row['url'], 'content': text, 'summary': text, 'summary_role': 'retained_body_compatibility_alias',
            'source_name': row['entry'].get('source_name', ''),
            'published': _timestamp_or_none(row['published_at']), 'published_original': row['published_at'],
            'collected_at': row['collected_at'],
            'content_completeness': 'complete' if row['completeness'] == 'full' else 'excerpt_only' if text else 'missing',
            'content_completeness_scope': row.get('completeness_scope', 'retained_source_only'),
            'completeness_dimensions': row.get('completeness_dimensions', {}),
            'processing': row.get('processing', {}),
            'body_sha256': row['body_sha256'], 'fetch_result_path': name, 'fact_verified': False}
        # Keep redirect provenance; QRP requires its index URL to match final_url.
        final_url = content['final_url']
        captures[final_url] = {'url': final_url, 'requested_url': row['url'], 'path': name,
                              'sha256': sha256_file(output / name)}
    _immutable_json(output / 'pid-items.json', list(pid.values()))
    _immutable_json(output / 'qrp-captures.json', {'captures': list(captures.values()), 'topic_id': topic_id,
        'selection': 'latest_retained_observation_per_final_url_not_publication_date',
        'all_references': rows})
    batches = []
    selected = list(captures.values())
    for offset in range(0, len(selected), 8):
        name = 'qrp-captures-' + str(offset // 8 + 1).zfill(2) + '.json'
        _immutable_json(output / name, {'captures': selected[offset:offset + 8], 'topic_id': topic_id})
        batches.append({'path': name, 'sha256': sha256_file(output / name),
                        'sources': len(selected[offset:offset + 8])})
    _immutable_json(output / 'qrp-batches.json', {'topic_id': topic_id, 'max_sources_per_batch': 8,
        'batches': batches, 'complete_index': 'qrp-captures.json'})
    markdown = '# ' + topic_id + '\n\nCompleteness is assessed against retained sources; facts are not verified.\n\n'
    for row in rows:
        markdown += ('## ' + str(row['entry'].get('title') or row['entry']['id']) + '\n\n' +
            row['url'] + '\n\nCompleteness: ' + row['completeness'] + '\n\n' + _body(root, row) + '\n\n')
    _immutable(output / 'readable.md', markdown.encode())
    _immutable(output / 'readable.html', markdown_to_readable_html(markdown, title=topic_id).encode())
    return {'topic_id': topic_id, 'pid_items': str(output / 'pid-items.json'),
            'qrp_captures': str(output / 'qrp-captures.json'), 'qrp_batches': str(output / 'qrp-batches.json'),
            'readable': str(output / 'readable.html'), 'references': len(rows)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    imp = sub.add_parser('import')
    imp.add_argument('--manifest', required=True)
    imp.add_argument('--input-root', required=True)
    for name in ('plan', 'recover', 'export'):
        sub.add_parser(name)
    for name in ('import', 'plan', 'recover', 'export'):
        sub.choices[name].add_argument('--store', required=True)
    sub.choices['plan'].add_argument('--output', required=True)
    sub.choices['plan'].add_argument('--topic-id')
    rec = sub.choices['recover']
    rec.add_argument('--plan', required=True)
    rec.add_argument('--allowlist', required=True)
    rec.add_argument('--execute', action='store_true')
    rec.add_argument('--max-items', type=int, default=15)
    rec.add_argument('--timeout', type=int, default=15)
    exp = sub.choices['export']
    exp.add_argument('--topic-id', required=True)
    exp.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    if args.action == 'import':
        result = import_manifest(args.manifest, args.store, args.input_root)
    elif args.action == 'plan':
        result = make_plan(args.store, args.topic_id)
        _immutable_json(Path(args.output), result)
    elif args.action == 'recover':
        result = recover(args.store, args.plan, args.allowlist, execute=args.execute,
                         max_items=args.max_items, timeout=args.timeout)
    else:
        result = export_topic(args.store, args.output, args.topic_id)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
