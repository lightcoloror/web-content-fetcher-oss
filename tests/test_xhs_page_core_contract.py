"""Shared XHS parser -> existing WCF core/archive, synthetic transport only."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from web_content_fetcher import FetchRequest, fetch_content


class XhsCoreContractTests(unittest.TestCase):
    def test_saved_browser_note_fields_survive_partial_quality(self):
        note = {'noteId': 'abc123', 'title': 'Synthetic note', 'desc': 'Short body.',
                'images': [{'url': '\nhttps://cdn.test/bad.jpg'},
                           {'url': 'https://cdn.test/good.jpg'}]}
        html = '<script>window.__INITIAL_STATE__=' + json.dumps(
            {'noteData': {'data': {'noteData': note}}}) + ';</script>'
        url = 'https://www.xiaohongshu.com/explore/abc123'
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            'web_content_fetcher.core.browser_fetch',
            return_value=(html, 'text/html', url, 'browser', {'title': 'Synthetic note'})
        ) as browser:
            result = fetch_content(FetchRequest(url=url, backend='xhs_browser_state',
                                                archive_dir=tmp, check_robots=False))
            self.assertEqual(result.status, 'partial')
            self.assertEqual(result.transport, 'xhs_browser_state')
            self.assertIn('Short body.', result.extracted_text)
            self.assertEqual(result.site_data['xhs']['media_diagnostics'],
                             ['media_url_skipped:control_char'])
            self.assertEqual(result.images[0]['url'], 'https://cdn.test/good.jpg')
            structured = json.loads(Path(result.archive['structured_path']).read_text(encoding='utf-8'))
            self.assertEqual(structured['site_data']['xhs']['media_diagnostics'],
                             ['media_url_skipped:control_char'])
            browser.assert_called_once()

    def test_redbook_archive_preserves_body_media_and_provenance(self):
        note = {'title': 'Synthetic note', 'desc': 'Short body.',
                'image_list': [{'url': 'https://cdn.test/good.jpg'}]}
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            'web_content_fetcher.core.redbook_cli_fetch', return_value=note
        ) as redbook:
            result = fetch_content(FetchRequest(
                url='https://www.xiaohongshu.com/explore/abc123',
                backend='xhs_redbook_cli', archive_dir=tmp, check_robots=False))
            self.assertEqual(result.status, 'partial')
            self.assertEqual(result.transport, 'xhs_redbook_cli')
            self.assertIn('Short body.', result.extracted_text)
            self.assertEqual(result.site_data['xhs']['media_diagnostics'], [])
            self.assertEqual(result.site_data['xhs']['media_status'], 'references_only')
            structured = json.loads(Path(result.archive['structured_path']).read_text(encoding='utf-8'))
            roles = {item['role'] for item in structured['archive_index']['files']}
            self.assertIn('xhs_raw_tool_result', roles)
            self.assertTrue(Path(result.archive['extracted_text_path']).is_file())
            redbook.assert_called_once()


if __name__ == '__main__':
    unittest.main()
