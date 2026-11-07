import json
import unittest
from web_content_fetcher.xhs_page import (
    extract_initial_state, extract_initial_state_note, is_xhs_url,
    normalize_note, parse_xhs_page,
)


def page(note):
    return '<script>window.__INITIAL_STATE__=' + json.dumps({"noteData": {"data": {"noteData": note}}}) + ';</script>'


class XhsPageTests(unittest.TestCase):
    def test_share_domains_and_spoof(self):
        for host in ("xhslink.cn", "xhsurl.cn", "xhsurl.com", "www.xiaohongshu.com"):
            self.assertTrue(is_xhs_url('https://' + host + '/x'))
        for url in ('https://xiaohongshu.com.evil.test', 'https://evilxiaohongshu.com', 'file://xiaohongshu.com/x'):
            self.assertFalse(is_xhs_url(url))

    def test_literals_preserve_body(self):
        result = extract_initial_state('window.__INITIAL_STATE__={"body":"undefined NaN } \\\"", "missing":undefined,"n":NaN}; more()')
        self.assertEqual(result['body'], 'undefined NaN } "')
        self.assertIsNone(result['missing'])

    def test_mobile_and_wrong_note(self):
        html = page({'noteId': 'abc123', 'desc': '正文'})
        self.assertEqual(parse_xhs_page(html, source_url='https://www.xiaohongshu.com/explore/abc123')['description'], '正文')
        self.assertEqual(extract_initial_state_note(html, 'https://www.xiaohongshu.com/explore/other'), {})

    def test_desktop_reactive(self):
        html = 'window.__INITIAL_STATE__=' + json.dumps({'note': {'noteDetailMap': {'_value': {'abc': {'note': {'desc': '正文'}}}}}})
        self.assertEqual(extract_initial_state_note(html, 'https://www.xiaohongshu.com/explore/abc')['desc'], '正文')

    def test_images_keep_prefix_and_one_item_per_picture(self):
        note = {'imageList': [{'fileId': 'oss-ae/notes/a', 'infoList': []},
                              {'fileId': 'notes/b', 'url': 'https://cdn.test/b.jpg', 'infoList': [{'url': 'https://cdn.test/b-small.jpg'}]}]}
        images = normalize_note(note)['images']
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0]['url'], 'https://sns-img-hw.xhscdn.com/oss-ae/notes/a')
        self.assertEqual(images[1]['url'], 'https://cdn.test/b.jpg')
        self.assertEqual(len(images[1]['fallback_urls']), 2)

    def test_video_metrics_do_not_claim_download_or_comments(self):
        note = {'video': {'media': {'stream': {'h264': json.dumps([{'masterUrl': 'https://cdn.test/a.mp4', 'videoDuration': 3020}])}}},
                'interactInfo': {'likedCount': 0, 'commentCount': '1.2万'}}
        result = normalize_note(note)
        self.assertEqual(result['video_streams'][0]['duration_seconds'], 3.02)
        self.assertEqual(result['metrics']['likes'], 0)
        self.assertEqual(result['metrics']['comments'], '1.2万')
        self.assertIsNone(result['metrics']['shares'])
        self.assertEqual(result['comments'], [])
        self.assertEqual(result['comments_status'], 'not_collected')
        self.assertEqual(result['media_status'], 'references_only')

    def test_existing_backend_delegates(self):
        from web_content_fetcher.xhs_backend import normalize_note as old, extract_initial_state_note as extract
        note = {'noteId': 'abc', 'desc': '正文'}
        self.assertEqual(old(note), normalize_note(note))
        self.assertEqual(extract(page(note))['noteId'], 'abc')

    def test_missing_page_is_not_success(self):
        with self.assertRaisesRegex(ValueError, 'missing'):
            parse_xhs_page('<html>登录</html>', source_url='https://www.xiaohongshu.com/explore/abc')


if __name__ == '__main__':
    unittest.main()
