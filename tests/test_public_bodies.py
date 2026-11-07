import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

from web_content_fetcher import public_bodies as bodies
from web_content_fetcher import public_http
from web_content_fetcher.core import DirectPayload, FetchRequest, direct_fetch

TEXT = 'A synthetic library catalog preserves public references and collection evidence. ' * 15
HTML = ('<html><head><title>Library catalog</title></head><body><main><h1>Library catalog</h1><p>' + TEXT + '</p></main></body></html>').encode()


class PublicBodyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / 'store'
        self.manifest = self.root / 'manifest.json'
        self.plan = self.root / 'plan.json'
        self.allow = self.root / 'allow.json'
        self.no_network = patch('socket.socket.connect', side_effect=AssertionError('network_forbidden'))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def entry(self, name='one', *, data=HTML, format='html', **extra):
        row = {'id': name, 'topic_id': 'library', 'url': 'https://example.test/' + name,
               'format': format, 'rights_scope': 'synthetic', 'published_at': None,
               'collected_at': '2026-10-07T00:00:00+00:00', **extra}
        if format != 'missing':
            path = self.root / (name + '.source')
            path.write_bytes(data)
            row.update(path=path.name, sha256=hashlib.sha256(data).hexdigest())
        return row

    def ingest(self, rows):
        self.manifest.write_text(json.dumps({'schema': 'wcf-body-manifest.v1', 'entries': rows}))
        return bodies.import_manifest(self.manifest, self.store, self.root)

    def recovery(self, rows):
        self.ingest(rows)
        self.plan.write_text(json.dumps(bodies.make_plan(self.store)))
        self.allow.write_text(json.dumps({'urls': list(dict.fromkeys(r['url'] for r in rows))}))

    def response(self, request):
        return DirectPayload(HTML, 'text/html', 200), 'text/html', request.url

    def test_full_partial_missing_and_unknown_time(self):
        rows = [self.entry(), self.entry('short', format='markdown', data=b'Only a brief saved excerpt.', content_completeness='excerpt_only'),
                self.entry('truncated', truncated=True), self.entry('missing', format='missing'),
                self.entry('unknown-time', collected_at=None),
                self.entry('md', format='markdown', data=TEXT.encode(), content_completeness='full')]
        self.ingest(rows)
        records = {r['entry']['id']: r for r in bodies._state(self.store)['records'].values()}
        self.assertEqual(records['one']['completeness'], 'full')
        self.assertEqual(records['short']['completeness'], 'partial')
        self.assertEqual(records['truncated']['completeness'], 'partial')
        self.assertEqual(records['missing']['completeness'], 'missing')
        self.assertIsNone(records['unknown-time']['collected_at'])
        self.assertIsNone(records['one']['published_at'])
        self.assertEqual(records['md']['completeness'], 'full')
        self.assertFalse(records['one']['fact_verified'])

    def test_same_body_multiple_references_and_exact_resume(self):
        first = self.entry()
        second = {**first, 'id': 'feed-two', 'source_name': 'second feed'}
        with patch.object(bodies, 'structure_content', wraps=bodies.structure_content) as extract:
            self.ingest([first, second])
            frozen = (self.store / 'index.json').read_bytes()
            self.ingest([first, second])
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(len(list((self.store / 'bodies').glob('*.md'))), 1)
        self.assertEqual(frozen, (self.store / 'index.json').read_bytes())
        self.assertEqual(len(bodies._state(self.store)['records']), 2)

    def test_same_url_new_version_retains_both(self):
        self.ingest([self.entry()])
        self.ingest([self.entry(data=HTML.replace(b'library', b'updated library'), collected_at='2026-10-07T01:00:00+00:00')])
        self.assertEqual(len(list((self.store / 'bodies').glob('*.md'))), 2)
        self.assertEqual(len(bodies._state(self.store)['records']), 2)
        export = self.root / 'export'
        bodies.export_topic(self.store, export, 'library')
        self.assertEqual(len(bodies._json(export / 'pid-items.json')), 1)
        index = bodies._json(export / 'qrp-captures.json')
        self.assertEqual(len(index['captures']), 1)
        self.assertEqual(len(index['all_references']), 2)
        self.assertIn('updated library', bodies._json(export / index['captures'][0]['path'])['extracted_text'])

    def test_failure_isolation_and_input_drift(self):
        row = self.entry()
        self.ingest([row, {**self.entry('invalid'), 'sha256': 'invalid'}])
        self.assertEqual(bodies.report(self.store)['completeness']['missing'], 1)
        (self.root / row['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'previous_import_drift'):
            self.ingest([row])

    def test_topic_isolation_and_query_preservation(self):
        self.ingest([self.entry('catalog', url='https://example.test/doc?version=1'),
                     self.entry('garden', topic_id='garden', data=HTML.replace(b'library', b'garden'))])
        output = self.root / 'exports'
        bodies.export_topic(self.store, output, 'library')
        index = bodies._json(output / 'qrp-captures.json')
        self.assertEqual(index['captures'][0]['url'], 'https://example.test/doc?version=1')
        self.assertNotIn('garden', (output / 'readable.md').read_text())
        with self.assertRaises(ValueError):
            bodies.export_topic(self.store, output, 'garden')

    def test_plan_only_then_resume_no_network_repeat(self):
        self.recovery([self.entry(format='missing')])
        with patch.object(bodies, 'direct_fetch', side_effect=self.response) as fetch:
            self.assertEqual(bodies.recover(self.store, self.plan, self.allow)['status'], 'plan_only')
            fetch.assert_not_called()
            bodies.recover(self.store, self.plan, self.allow, execute=True)
            before = (self.store / 'index.json').read_bytes()
            bodies.recover(self.store, self.plan, self.allow, execute=True)
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(before, (self.store / 'index.json').read_bytes())

    def test_saved_response_after_interruption_resumes_locally(self):
        self.recovery([self.entry(format='missing')])
        with patch.object(bodies, 'direct_fetch', side_effect=self.response) as fetch:
            with patch.object(bodies, '_capture', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    bodies.recover(self.store, self.plan, self.allow, execute=True)
            bodies.recover(self.store, self.plan, self.allow, execute=True)
            self.assertEqual(fetch.call_count, 1)

    def test_unknown_request_outcome_is_not_resent(self):
        self.recovery([self.entry(format='missing')])
        with patch.object(bodies, 'direct_fetch', side_effect=KeyboardInterrupt) as fetch:
            with self.assertRaises(KeyboardInterrupt):
                bodies.recover(self.store, self.plan, self.allow, execute=True)
            result = bodies.recover(self.store, self.plan, self.allow, execute=True)
            self.assertEqual(fetch.call_count, 1)
        journal = bodies._json(result['checkpoint_path'])
        self.assertEqual(next(iter(journal['tasks'].values()))['status'], 'outcome_unknown_no_auto_resend')

    def test_failure_twice_does_not_stop_other_url(self):
        self.recovery([self.entry('fail', format='missing'), self.entry('ok', format='missing')])
        def response(request):
            if request.url.endswith('/fail'):
                raise TimeoutError('synthetic timeout')
            return self.response(request)
        with patch.object(bodies, 'direct_fetch', side_effect=response) as fetch:
            result = bodies.recover(self.store, self.plan, self.allow, execute=True)
            bodies.recover(self.store, self.plan, self.allow, execute=True)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(result['completeness'], {'full': 1, 'partial': 0, 'missing': 1})
        failed = next(r for r in bodies._state(self.store)['records'].values() if r['url'].endswith('/fail'))
        self.assertEqual(len(failed['recovery']['attempts']), 2)
        self.assertIn('TimeoutError', failed['recovery']['attempts'][0]['error'])

    def test_index_commit_interruption_resumes_without_request(self):
        self.recovery([self.entry(format='missing')])
        original = bodies.atomic_write_json
        def interrupt_after_index(path, value):
            original(path, value)
            if Path(path) == self.store / 'index.json':
                raise KeyboardInterrupt
        with patch.object(bodies, 'direct_fetch', side_effect=self.response) as fetch:
            with patch.object(bodies, 'atomic_write_json', side_effect=interrupt_after_index):
                with self.assertRaises(KeyboardInterrupt):
                    bodies.recover(self.store, self.plan, self.allow, execute=True)
            bodies.recover(self.store, self.plan, self.allow, execute=True)
        self.assertEqual(fetch.call_count, 1)
        row = next(iter(bodies._state(self.store)['records'].values()))
        self.assertEqual(len(row['previous_observations']), 1)

    def test_downstream_batches_and_body_alias_are_explicit(self):
        self.ingest([self.entry(str(i), published_at='unknown') for i in range(9)])
        output = self.root / 'exports'
        bodies.export_topic(self.store, output, 'library')
        batches = bodies._json(output / 'qrp-batches.json')['batches']
        self.assertEqual([b['sources'] for b in batches], [8, 1])
        self.assertEqual(len(bodies._json(output / 'qrp-captures.json')['captures']), 9)
        for item in bodies._json(output / 'pid-items.json'):
            self.assertEqual(item['content'], item['summary'])
            self.assertEqual(item['summary_role'], 'retained_body_compatibility_alias')
            self.assertIsNone(item['published'])
            self.assertEqual(item['published_original'], 'unknown')

    def test_drift_and_unapproved_url_fail_closed(self):
        self.recovery([self.entry(format='missing')])
        self.allow.write_text(json.dumps({'urls': ['https://another.test/']}))
        with self.assertRaisesRegex(ValueError, 'url_not_in_explicit_allowlist'):
            bodies.recover(self.store, self.plan, self.allow, execute=True)


class PublicHTTPTests(unittest.TestCase):
    def test_private_urls_and_redirects_rejected(self):
        for url in ['http://127.0.0.1/', 'https://localhost/', 'http://[::1]/',
                    'http://10.2.3.4/', 'file:///etc/hosts', 'https://u:p@example.com/', 'https://example.com:8888/']:
            with self.subTest(url=url), self.assertRaises(public_http.PublicFetchBlocked):
                public_http.public_url(url)
        req = urllib.request.Request('https://example.com/')
        with self.assertRaises(public_http.PublicFetchBlocked):
            public_http._Redirect().redirect_request(req, None, 302, '', {}, 'http://169.254.169.254/')

    def test_dns_private_answer_never_connects(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))]
        with patch('socket.getaddrinfo', return_value=answers), patch('socket.create_connection') as connect:
            with self.assertRaises(public_http.PublicFetchBlocked):
                public_http._public_connection(('example.com', 443), 1)
            connect.assert_not_called()

    def test_numeric_validated_address_used_for_connection(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 443))]
        with patch('socket.getaddrinfo', return_value=answers), patch('socket.create_connection') as connect:
            public_http._public_connection(('example.com', 443), 1)
            self.assertEqual(connect.call_args.args[0], ('1.1.1.1', 443))

    def test_non_text_body_is_not_downloaded(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {'content-type': 'image/png'}
        response.getcode.return_value = 200
        response.geturl.return_value = 'https://example.com/image'
        with patch.object(public_http, 'public_open', return_value=response):
            raw, _, _ = direct_fetch(FetchRequest(url='https://example.com/image', public_http_only=True))
        response.read.assert_not_called()
        self.assertTrue(raw.payload['binary'])


if __name__ == '__main__':
    unittest.main()
