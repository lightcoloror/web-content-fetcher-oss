"""Offline status/quality contract, including saved XHS evidence and HTTP outcomes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from web_content_fetcher import FetchRequest, fetch_content
from web_content_fetcher.core import DirectPayload, structure_content
from web_content_fetcher.extractors import MIN_USEFUL_CHARS

URL = 'https://www.xiaohongshu.com/explore/abc123'
IMAGE = 'https://cdn.test/synthetic.jpg'
BODY = '\n\n'.join(
    f'Synthetic paragraph {index}. This saved note describes a different step in a fictional process. '
    'The body is retained as source evidence with its image reference and capture provenance.'
    for index in range(4)
)


class XhsStatusSemanticsTests(unittest.TestCase):
    def capture(self, body, root, *, truncated=False, http_status=200):
        raw = DirectPayload(body.encode('utf-8'), 'text/markdown', http_status, truncated)
        return structure_content(
            FetchRequest(url=URL, archive_dir=root, backend='xhs_browser_state', check_robots=False),
            raw, 'text/markdown', URL, 'xhs_browser_state', [],
            {'title': 'Synthetic saved note', 'images': [{'url': IMAGE}],
             'site_data': {'xhs': {'source': 'synthetic_saved_note',
                                  'media_diagnostics': ['media_url_skipped:control_char'],
                                  'media_status': 'references_only'}}},
        )

    def assert_archive_status(self, result):
        saved = json.loads(Path(result.archive['structured_path']).read_text(encoding='utf-8'))
        self.assertEqual(saved['status'], result.status)
        self.assertEqual(saved['transport_ok'], result.transport_ok)
        self.assertEqual(saved['content_ok'], result.content_ok)
        self.assertEqual(saved['site_data']['xhs']['media_diagnostics'],
                         ['media_url_skipped:control_char'])
        self.assertEqual(saved['images'][0]['url'], IMAGE)
        self.assertEqual(Path(result.archive['extracted_text_path']).read_text(encoding='utf-8'),
                         result.extracted_text)
        self.assertEqual(result.to_dict()['status'], result.status)

    def test_short_valid_source_stays_partial_with_body_and_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.capture('A valid but short source note.', tmp)
            self.assertEqual(result.status, 'partial')
            self.assertTrue(result.transport_ok)
            self.assertFalse(result.content_ok)
            self.assertEqual(result.extraction_quality['status'], 'too_short')
            self.assertEqual(result.extracted_text, 'A valid but short source note.')
            self.assert_archive_status(result)

    def test_empty_http_200_is_not_content_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.capture('', tmp)
            self.assertEqual(result.http_status, 200)
            self.assertTrue(result.transport_ok)
            self.assertFalse(result.content_ok)
            self.assertEqual(result.status, 'partial')
            self.assertEqual(result.extraction_quality['status'], 'empty')
            self.assert_archive_status(result)

    def test_truncated_sufficient_text_stays_partial_with_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.capture(BODY, tmp, truncated=True)
            self.assertGreater(result.extraction_quality['text_chars'], MIN_USEFUL_CHARS)
            self.assertEqual(result.extraction_quality['status'], 'ok')
            self.assertTrue(result.transport_ok)
            self.assertFalse(result.content_ok)
            self.assertEqual(result.status, 'partial')
            self.assertIn('payload_truncated:max_bytes', result.errors)
            self.assertEqual(result.extracted_text, BODY)
            self.assert_archive_status(result)

    def test_sufficient_body_passes_only_with_successful_transport(self):
        for http_status in (200, 503):
            with self.subTest(http_status=http_status), tempfile.TemporaryDirectory() as tmp:
                result = self.capture(BODY, tmp, http_status=http_status)
                self.assertEqual(result.extraction_quality['status'], 'ok')
                self.assertEqual(result.transport_ok, http_status == 200)
                self.assertEqual(result.content_ok, http_status == 200)
                self.assertEqual(result.status, 'ok' if http_status == 200 else 'failed')
                self.assert_archive_status(result)

    def test_empty_xhs_page_reports_missing_note(self):
        with mock.patch('web_content_fetcher.core.browser_fetch',
                        return_value=('<html></html>', 'text/html', URL, 'browser', {})):
            result = fetch_content(FetchRequest(url=URL, backend='xhs_browser_state', check_robots=False))
        serialized = result.to_dict()
        self.assertEqual(serialized['status'], 'failed')
        self.assertFalse(serialized['transport_ok'])
        self.assertFalse(serialized['content_ok'])
        self.assertTrue(any('xhs_initial_state_note_missing' in error for error in result.errors))


if __name__ == '__main__':
    unittest.main()
