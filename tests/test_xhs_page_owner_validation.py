"""Offline Owner regressions for untrusted saved-page media references."""
import ast
import inspect
import unittest

from web_content_fetcher import xhs_page
from web_content_fetcher import xhs_backend


class OwnerUrlValidationTests(unittest.TestCase):
    def test_controls_rejected_at_every_position(self):
        url = 'https://cdn.test/image.jpg'
        for code in (*range(32), *range(127, 160)):
            for position in (0, 18, len(url)):
                with self.subTest(code=code, position=position):
                    value = url[:position] + chr(code) + url[position:]
                    result = xhs_page.normalize_note({'desc': 'Body', 'images': [value]})
                    self.assertEqual(result['description'], 'Body')
                    self.assertEqual(result['images'], [])
                    self.assertEqual(result['media_diagnostics'],
                                     [xhs_page.MEDIA_URL_SKIPPED_CONTROL_CHAR])

    def test_malformed_urls_keep_valid_image_alternative(self):
        for bad in ('https://[broken/a.jpg', 'https://host:bad/a.jpg',
                    'https://user:password@host/a.jpg', 'https://host/a b.jpg',
                    'https://host\\other/a.jpg', 'javascript:alert(1)'):
            with self.subTest(candidate=bad):
                out = xhs_page.normalize_note({'images': [
                    {'urlDefault': bad, 'url': 'https://cdn.test/valid.jpg'}]})
                self.assertEqual(out['images'][0]['url'], 'https://cdn.test/valid.jpg')
                self.assertEqual(out['media_diagnostics'],
                                 [xhs_page.MEDIA_URL_SKIPPED_INVALID_URL])

    def test_malformed_master_keeps_backup_and_reason(self):
        out = xhs_page.normalize_note({'video': {'media': {'stream': {'h264': [
            {'masterUrl': 'https://[broken', 'backupUrls': [
                'https://host:bad/file', 'https://cdn.test/valid.mp4']} ]}}}})
        self.assertEqual(out['video_streams'][0]['url'], 'https://cdn.test/valid.mp4')
        self.assertEqual(out['media_diagnostics'], [xhs_page.MEDIA_URL_SKIPPED_INVALID_URL])

    def test_control_in_file_id_is_not_repaired_into_a_valid_reference(self):
        for value in ('\tnotes/a', 'notes/a\n', 'notes/\x00a'):
            with self.subTest(value=value):
                out = xhs_page.normalize_note({'images': [{'fileId': value}]})
                self.assertEqual(out['images'], [])
                self.assertEqual(out['media_diagnostics'],
                                 [xhs_page.MEDIA_URL_SKIPPED_CONTROL_CHAR])

    def test_valid_explicit_url_survives_invalid_file_id(self):
        out = xhs_page.normalize_note({'images': [
            {'url': 'https://cdn.test/image.jpg', 'fileId': '\tnotes/a'}]})
        self.assertEqual(out['images'][0]['url'], 'https://cdn.test/image.jpg')
        self.assertEqual(out['images'][0]['fallback_urls'], [])

    def test_source_url_errors_are_stable(self):
        for url in ('https://[broken', '\nhttps://www.xiaohongshu.com/explore/a',
                    'https://www.xiaohongshu.com:bad/explore/a'):
            with self.subTest(url=url):
                self.assertFalse(xhs_page.is_xhs_url(url))
                with self.assertRaisesRegex(ValueError, '^not_a_xiaohongshu_url$'):
                    xhs_page.parse_xhs_page('', source_url=url)

    def test_backend_facade_preserves_additive_diagnostics(self):
        note = {'desc': 'Body', 'imageList': [
            {'url': '\nhttps://cdn.test/bad.jpg'}, {'url': 'https://cdn.test/good.jpg'}]}
        out = xhs_backend.normalize_note(note, source='synthetic', source_url='local-fixture')
        self.assertEqual(out, xhs_page.normalize_note(note, source='synthetic', source_url='local-fixture'))
        self.assertEqual(len(xhs_backend.collect_note_images(note)), 1)
        self.assertEqual(out['media_status'], 'references_only')
        self.assertEqual(out['comments_status'], 'not_collected')
        self.assertEqual(out['asr_status'], 'not_run')

    def test_no_duplicate_top_level_definitions(self):
        tree = ast.parse(inspect.getsource(xhs_page))
        names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
        self.assertEqual(len(names), len(set(names)))


if __name__ == '__main__':
    unittest.main()
