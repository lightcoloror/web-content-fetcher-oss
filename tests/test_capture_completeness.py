import hashlib
import json
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

from web_content_fetcher.completeness import inspect_capture
from web_content_fetcher.core import FetchRequest, DirectPayload, fetch_content, structure_content


class CaptureCompletenessTests(unittest.TestCase):
    def inspect(self, raw, text='', **kwargs):
        return inspect_capture(raw, text, page_url='https://example.test/article', **kwargs)

    def test_http_200_captcha_is_terminal(self):
        report = self.inspect('<h1>环境异常</h1>', final_url='https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha', http_status=200)
        self.assertEqual(report['access']['status'], 'access_blocked')
        self.assertEqual(report['manual_capture']['action'], 'manual_capture_required')
        self.assertIsNone(report['body_images']['count'])

    def test_403_and_truncated_are_not_complete(self):
        for kwargs in ({'http_status': 403}, {'payload': {'truncated': True}}):
            report = self.inspect('<main><p>Some body</p></main>', 'Some body', **kwargs)
            self.assertNotEqual(report['status'], 'complete_against_saved_dom')

    def test_links_have_dom_order_and_missing_label(self):
        raw = '<main><p>before <a href="/a">first <strong>link</strong></a> after</p><p><a href="/b">second link</a></p></main>'
        report = self.inspect(raw, 'before first link after')
        self.assertEqual([x['text'] for x in report['dom_links']['missing']], ['second link'])
        self.assertEqual([x['ordinal'] for x in report['dom_links']['nodes']], [1, 2])
        self.assertEqual(report['source_sha256'], hashlib.sha256(raw.encode()).hexdigest())

    def test_no_body_is_unknown_not_no_images(self):
        self.assertIsNone(self.inspect('<div>loading</div>')['body_images']['count'])
        self.assertIsNone(self.inspect('<main></main>')['body_images']['count'])

    def test_known_body_without_images_is_not_applicable(self):
        report = self.inspect('<main><p>hello</p></main>', 'hello')
        self.assertEqual(report['body_images']['original_availability'], 'not_applicable')
        self.assertEqual(report['status'], 'complete_against_saved_dom')

    def test_article_discussing_captcha_is_not_blocked(self):
        raw = '<article><h1>How captcha works</h1><p>验证码 captcha verify</p><a href="/captcha">challenge explanation</a></article>'
        self.assertEqual(self.inspect(raw, 'How captcha works 验证码 captcha verify challenge explanation', final_url='https://example.test/captcha')['access']['status'], 'not_observed')

    def test_generic_captcha_controls_block_without_provider_specific_url(self):
        raw = '<title>安全验证</title><form><input id="captcha"></form>'
        self.assertEqual(self.inspect(raw)['access']['status'], 'access_blocked')

    def test_matching_declared_original_requires_local_validation(self):
        from PIL import Image
        root = Path(os.environ['TEST_TMP_ROOT']) / uuid.uuid4().hex
        root.mkdir()
        image = root / 'original.png'
        Image.new('RGB', (4, 4)).save(image)
        asset = {'url': 'https://example.test/o.png', 'asset_path': str(image), 'asset_sha256': hashlib.sha256(image.read_bytes()).hexdigest()}
        report = self.inspect('<main><img src="/o.png" data-original="/o.png"></main>', image_assets=[asset])
        self.assertEqual(report['body_images']['original_availability'], 'available')
        self.assertEqual(report['body_images']['original_downloaded'], 1)

    def test_lazy_original_and_corrupt_display_are_distinct(self):
        root = Path(os.environ['TEST_TMP_ROOT']) / uuid.uuid4().hex
        root.mkdir()
        bad = root / 'bad.png'
        bad.write_bytes(b'not an image')
        raw = '<main><img data-src="/display.png" data-original="/original.png"></main>'
        asset = {'url': 'https://example.test/display.png', 'asset_path': str(bad), 'asset_sha256': hashlib.sha256(bad.read_bytes()).hexdigest()}
        image = self.inspect(raw, image_assets=[asset])['body_images']['nodes'][0]
        self.assertEqual(image['display']['availability'], 'invalid')
        self.assertEqual(image['original']['availability'], 'unknown')
        self.assertFalse(image['original']['downloaded'])

    def test_valid_display_does_not_prove_original(self):
        from PIL import Image
        root = Path(os.environ['TEST_TMP_ROOT']) / uuid.uuid4().hex
        root.mkdir()
        path = root / 'synthetic.png'
        Image.new('RGB', (2, 3)).save(path)
        asset = {'url': 'https://example.test/d.png', 'asset_path': str(path), 'asset_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        report = self.inspect('<main><img src="/d.png" data-original="/o.png"></main>', image_assets=[asset])
        image = report['body_images']['nodes'][0]
        self.assertEqual(image['display']['availability'], 'available')
        self.assertEqual(image['display']['dimensions'], [2, 3])
        self.assertEqual(image['original']['availability'], 'unknown')
        self.assertEqual(report['body_images']['original_downloaded'], 0)

    def test_source_order_links_and_images_share_dom_index(self):
        report = self.inspect('<main><a href="/a">A</a><img src="/x"><a href="/b">B</a></main>', 'A B')
        self.assertLess(report['dom_links']['nodes'][0]['dom_index'], report['body_images']['nodes'][0]['dom_index'])
        self.assertLess(report['body_images']['nodes'][0]['dom_index'], report['dom_links']['nodes'][1]['dom_index'])

    def test_fetch_stops_on_captcha_without_fallback_or_assets(self):
        raw = DirectPayload('<h1>环境异常</h1>'.encode(), 'text/html', 200)
        with patch('web_content_fetcher.core.direct_fetch', return_value=(raw, 'text/html', 'https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha')), \
             patch('web_content_fetcher.core.browser_fetch', side_effect=AssertionError('no browser')), \
             patch('web_content_fetcher.core._try_auto_firecrawl_fallback', side_effect=AssertionError('no remote fallback')):
            result = fetch_content(FetchRequest('https://example.test/article', check_robots=False))
        self.assertEqual(result.status, 'access_blocked')
        self.assertTrue(result.transport_ok)
        self.assertFalse(result.content_ok)
        self.assertEqual(result.extracted_text, '')

    def test_archive_contains_machine_and_human_diagnosis(self):
        output = Path(os.environ['TEST_TMP_ROOT']) / uuid.uuid4().hex
        result = structure_content(FetchRequest('https://example.test/article', archive_dir=str(output), check_robots=False),
            '<h1>blocked</h1>', 'text/html', 'https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha', 'direct', [], {'http_status': 200})
        data = json.loads(Path(result.archive['structured_path']).read_text(encoding='utf-8'))
        self.assertEqual(data['capture_completeness']['access']['status'], 'access_blocked')
        self.assertIn('manual_capture_required', Path(result.reading_report_path).read_text(encoding='utf-8'))
        self.assertIn('manual_capture_required', Path(result.archive['preferred_readable_html_path']).read_text(encoding='utf-8'))

    def test_plain_text_does_not_invent_html_supplement_request(self):
        result = structure_content(FetchRequest('https://example.test/data', check_robots=False),
                                   'synthetic text ' * 50, 'text/plain', 'https://example.test/data', 'direct', [])
        self.assertEqual(result.capture_completeness['status'], 'not_applicable_non_html')
        self.assertEqual(result.capture_completeness['manual_capture'], {})
