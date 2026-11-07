import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from web_content_fetcher.platform_samples import build_catalog, canonical_url, channel_of, main, project_sample, write_catalog


class PlatformSamplesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('WCF_TEST_TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 9, 13, tzinfo=timezone.utc)
        self.topic = {'id': 'a', 'query': 'Organize personal files', 'research_ref': 'topic-a', 'upstream_quality': 'warn'}

    def entry(self, sid='one', channel='wechat', **changes):
        url = 'https://mp.weixin.qq.com/s/fictional' if channel == 'wechat' else 'https://www.xiaohongshu.com/explore/fictional'
        result = {'requested_url': url, 'final_url': url, 'http_status': 200, 'status': 'ok',
                  'fetched_at': '2026-09-12T00:00:00+00:00', 'raw_snapshot': '<article>First <a href="/s/ref">reference</a> last.</article>',
                  'extracted_text': 'First reference last.'}
        result.update(changes)
        path = self.root / (sid + '.json')
        data = json.dumps(result).encode()
        path.write_bytes(data)
        return {'sample_id': sid, 'topic_id': 'a', 'url': url, 'fetch_result_path': str(path),
                'fetch_result_sha256': hashlib.sha256(data).hexdigest(), 'rights_scope': 'synthetic', 'evidence_kind': 'synthetic'}

    def project(self, entry):
        return project_sample(entry, self.topic, roots=[self.root], now=self.now)

    def catalog(self, *entries):
        return build_catalog({'schema_version': 'wcf-platform-sample-input.v1', 'topics': [self.topic], 'samples': list(entries)},
                             roots=[self.root], now=self.now)

    def test_both_channels_saved_success_not_full_original(self):
        for channel in ['wechat', 'xiaohongshu']:
            with self.subTest(channel=channel):
                row = self.project(self.entry(sid=channel, channel=channel))
                self.assertEqual(row['body']['status'], 'available')
                self.assertFalse(row['full_original_verified'])
                self.assertEqual(row['original_images']['status'], 'not_requested')
                self.assertEqual(row['subtitles']['status'], 'not_requested')
                self.assertEqual(row['upstream_quality'], 'warn')

    def test_http_200_captcha_is_blocked(self):
        row = self.project(self.entry(final_url='https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?token=fictional-sensitive'))
        self.assertTrue(row['transport_ok'])
        self.assertEqual(row['body']['status'], 'blocked')
        self.assertEqual(row['original_images']['expected_count'], None)
        self.assertNotIn('fictional-sensitive', json.dumps(row))

    def test_login_flags_and_403_on_xhs(self):
        for changes in [{'http_status': 403}, {'extraction_quality': {'login_redirect_detected': True}}]:
            self.assertEqual(self.project(self.entry(channel='xiaohongshu', **changes))['sample_status'], 'blocked')

    def test_missing_link_label_and_body_tail(self):
        row = self.project(self.entry(extracted_text='First last.'))
        self.assertEqual(row['body']['missing_link_labels'], 1)
        self.assertEqual(row['body']['status'], 'partial')
        row = self.project(self.entry(extracted_text='First reference'))
        self.assertEqual(row['body']['status'], 'partial')

    def test_title_and_ai_summary_cannot_stand_in_for_body(self):
        for origin in ['title_only', 'ai_summary']:
            entry = self.entry()
            entry['content_origin'] = origin
            self.assertEqual(self.project(entry)['body']['status'], 'partial')
        self.assertEqual(self.project(self.entry(title='Only a title', raw_snapshot='<article>Only a title</article>',
                                                extracted_text='Only a title'))['body']['status'], 'partial')

    def test_spa_and_truncation_not_success(self):
        for changes in [{'payload': {'truncated': True}}, {'extraction_quality': {'spa_shell_detected': True}},
                        {'raw_snapshot': '<html><title>Only title</title><div id="app"></div></html>'}]:
            self.assertEqual(self.project(self.entry(**changes))['body']['status'], 'partial')

    def test_unknown_rights_does_not_read_input(self):
        entry = self.entry()
        entry['rights_scope'] = 'unknown'
        entry['evidence_kind'] = 'real'
        with patch('web_content_fetcher.platform_samples._read', side_effect=AssertionError('must not read')):
            self.assertEqual(self.project(entry)['body']['reason'], 'rights_scope_unknown')

    def test_duplicate_prefers_success_and_recovery_is_new_record(self):
        failed = self.entry('failed', http_status=403)
        recovered = self.entry('recovered')
        recovered['recovery_of'] = 'failed'
        before = Path(failed['fetch_result_path']).read_bytes()
        rows = self.catalog(failed, recovered)['samples']
        self.assertEqual(rows[0]['selection'], 'duplicate_reference')
        self.assertEqual(rows[0]['preferred_sample_id'], 'recovered')
        self.assertEqual(rows[1]['selection'], 'preferred')
        self.assertEqual(Path(failed['fetch_result_path']).read_bytes(), before)

    def test_expiry_unknown_and_future_keep_capture_time(self):
        for timestamp, expected in [('2026-08-01T00:00:00+00:00', 'expired'), (None, 'unknown'),
                                    ('2026-09-20T00:00:00+00:00', 'future_timestamp')]:
            row = self.project(self.entry(fetched_at=timestamp))
            self.assertEqual(row['freshness'], expected)
            self.assertEqual(row['sample_status'], 'partial')
            self.assertEqual(row['captured_at'], timestamp)

    def test_url_identity_and_host_validation(self):
        self.assertEqual(canonical_url('https://www.xiaohongshu.com/discovery/item/abc?xsec_token=fictional'),
                         'https://www.xiaohongshu.com/explore/abc')
        self.assertEqual(channel_of('https://xhslink.cn/o/example'), 'xiaohongshu')
        with self.assertRaises(ValueError):
            channel_of('https://mp.weixin.qq.com.evil.example/s/one')
        self.assertNotEqual(canonical_url('https://mp.weixin.qq.com/s?mid=1&idx=1'),
                            canonical_url('https://mp.weixin.qq.com/s?mid=2&idx=1'))

    def test_hash_mismatch_and_outside_root_fail_closed(self):
        entry = self.entry()
        Path(entry['fetch_result_path']).write_text('{}')
        self.assertEqual(self.project(entry)['sample_status'], 'blocked')
        entry = self.entry()
        other = self.root / 'other'
        other.mkdir()
        self.assertEqual(project_sample(entry, self.topic, roots=[other], now=self.now)['sample_status'], 'blocked')

    def test_missing_channels_never_count_as_collected(self):
        result = self.catalog()
        self.assertEqual(len(result['samples']), 2)
        self.assertTrue(all(r['real_body_available'] == 0 for r in result['coverage']))
        self.assertTrue(all(r['body']['reason'] == 'no_channel_sample_supplied' for r in result['samples']))

    def test_native_xhs_description_is_not_gallery(self):
        state = {'note': {'noteDetailMap': {'fictional': {'note': {'desc': 'Native description',
                         'imageList': [{'urlDefault': 'https://cdn.example/display.jpg'}]}}}}}
        raw = '<script>window.__INITIAL_STATE__=' + json.dumps(state) + '</script>'
        row = self.project(self.entry(channel='xiaohongshu', raw_snapshot=raw, extracted_text='Native description'))
        self.assertEqual(row['body']['status'], 'available')
        self.assertEqual(row['original_images']['status'], 'partial')
        self.assertEqual(row['source_order'][0]['node_id'], 'native-image-1')
        self.assertIsNotNone(row['native_source_ref'])

    def test_display_and_original_verified_separately_source_order(self):
        from PIL import Image
        image = self.root / 'synthetic.png'
        Image.new('RGB', (2, 3)).save(image)
        sha = hashlib.sha256(image.read_bytes()).hexdigest()
        url = 'https://cdn.example/display.png'
        row = self.project(self.entry(raw_snapshot=f'<article>First<img src="{url}"><img src="{url}" data-original="{url}">last.</article>',
                                     extracted_text='First last.', image_assets=[{'url': url, 'asset_path': str(image), 'asset_sha256': sha}]))
        self.assertEqual(row['display_images']['available_count'], 2)
        self.assertEqual(row['original_images']['available_count'], 1)
        self.assertEqual(row['original_images']['status'], 'partial')
        self.assertEqual([i['ordinal'] for i in row['source_order']], [1, 2])
        self.assertEqual(row['source_order'][0]['display']['sha256'], sha)

    def test_media_permissions_hash_and_missing_file(self):
        entry = self.entry()
        entry['media'] = {'video': {'requested': True}, 'subtitles': {'requested': True, 'authorized': True}}
        row = self.project(entry)
        self.assertEqual(row['video']['status'], 'blocked')
        self.assertEqual(row['subtitles']['status'], 'partial')
        path = self.root / 'synthetic.vtt'
        path.write_text('WEBVTT\n\n00:00.000 --> 00:01.000\nExample\n')
        entry['media']['subtitles'].update(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        row = self.project(entry)
        self.assertEqual(row['subtitles']['status'], 'available')
        self.assertIn('not_playback_or_timing_validated', row['subtitles']['reason'])

    def test_topic_isolation_and_synthetic_does_not_inflate_real(self):
        config = {'schema_version': 'wcf-platform-sample-input.v1', 'topics': [self.topic, {'id': 'b', 'query': 'Subtitle timing'}],
                  'samples': [self.entry()]}
        result = build_catalog(config, roots=[self.root], now=self.now)
        self.assertTrue(all(c['real_body_available'] == 0 for c in result['coverage']))
        self.assertTrue(all(s['body']['status'] == 'blocked' for s in result['samples'] if s['topic_id'] == 'b'))

    def test_per_channel_sample_cap_and_invalid_recovery(self):
        entries = [self.entry(str(i), requested_url=f'https://mp.weixin.qq.com/s/{i}') for i in range(4)]
        for i, e in enumerate(entries):
            e['url'] = f'https://mp.weixin.qq.com/s/{i}'
        with self.assertRaisesRegex(ValueError, 'max_three'):
            self.catalog(*entries)
        entry = self.entry()
        entry['recovery_of'] = 'absent'
        with self.assertRaisesRegex(ValueError, 'invalid_recovery'):
            self.catalog(entry)

    def test_cli_and_readable_output_no_overwrite(self):
        config = {'schema_version': 'wcf-platform-sample-input.v1', 'topics': [self.topic], 'samples': [self.entry()]}
        source = self.root / 'input.json'
        source.write_text(json.dumps(config))
        output = self.root / 'output'
        self.assertEqual(main(['--input', str(source), '--output', str(output), '--allowed-root', str(self.root)]), 0)
        self.assertTrue((output / 'index.html').is_file())
        self.assertTrue((output / 'platform_samples.md').is_file())
        self.assertTrue((output / 'recovery-input.template.json').is_file())
        self.assertIn('<table>', (output / 'index.html').read_text(encoding='utf-8'))
        with self.assertRaises(FileExistsError):
            write_catalog(self.catalog(), output)

    def test_recovery_template_replay_keeps_prior_record(self):
        failed = self.entry('old', http_status=403)
        output = self.root / 'failed-review'
        write_catalog(self.catalog(failed), output)
        template = json.loads((output / 'recovery-input.template.json').read_text(encoding='utf-8'))
        recovered = self.entry('new')
        recovered['recovery_of'] = 'old'
        template['samples'] = [e for e in template['samples'] if e['sample_id'] == 'old'] + [recovered]
        catalog = build_catalog(template, roots=[self.root], now=self.now)
        self.assertEqual(catalog['samples'][0]['selection'], 'duplicate_reference')
        self.assertEqual(catalog['samples'][1]['body']['status'], 'available')

    def test_two_captcha_redirects_keep_distinct_requested_identity(self):
        entries = []
        for i in range(2):
            url = f'https://mp.weixin.qq.com/s/fictional-{i}'
            entry = self.entry(str(i), requested_url=url,
                               final_url='https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha')
            entry['url'] = url
            entries.append(entry)
        rows = self.catalog(*entries)['samples'][:2]
        self.assertTrue(all(r['selection'] == 'preferred' for r in rows))
        self.assertTrue(all(r['body']['status'] == 'blocked' for r in rows))

    def test_wrong_capture_url_and_missing_digest_block(self):
        entry = self.entry(requested_url='https://mp.weixin.qq.com/s/unrelated')
        self.assertEqual(self.project(entry)['body']['status'], 'blocked')
        entry = self.entry()
        entry.pop('fetch_result_sha256')
        self.assertEqual(self.project(entry)['body']['status'], 'blocked')

    def test_untrusted_labels_are_not_executable_html(self):
        catalog = self.catalog()
        catalog['topics'][0]['query'] = '<script>fictional()</script>'
        output = self.root / 'escaped'
        write_catalog(catalog, output)
        rendered = (output / 'index.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)


if __name__ == '__main__':
    unittest.main()
