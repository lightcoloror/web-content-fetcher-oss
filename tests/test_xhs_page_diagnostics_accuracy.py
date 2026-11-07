"""Pro-review 回归：v2 待审查版本中仍存在的四个缺陷。

这些测试在“v2 补丁应用后的副本”上必须失败，才能证明缺陷属于 v2 版本；
随后在本轮最小修复后的副本上通过。文件内每个用例都写明：
缺陷（含最小合成输入、期望、实际）、修复前失败原因、修复后断言。
"""
import json
import unittest

from web_content_fetcher.xhs_page import (
    MEDIA_URL_SKIPPED_CONTROL_CHAR,
    collect_note_images,
    collect_video_streams,
    normalize_note,
)


class OuterWhitespaceDiagnosticConsistency(unittest.TestCase):
    """Owner rule: reject controls before strip, but accept outer plain spaces.

    Acceptance and diagnostics must agree in both cases. This deliberately
    tightens the isolated pro candidate's outer-tab/newline acceptance.
    """

    def test_outer_tab_leading_is_rejected_before_strip(self):
        sink = []
        images = collect_note_images({"imageList": [{"url": "\thttps://c/a.jpg"}]}, diagnostics=sink)
        self.assertEqual(images, [])
        self.assertEqual(sink, [MEDIA_URL_SKIPPED_CONTROL_CHAR])

    def test_outer_newline_trailing(self):
        sink = []
        images = collect_note_images({"imageList": [{"url": "https://c/a.jpg\n"}]}, diagnostics=sink)
        self.assertEqual(images, [])
        self.assertEqual(sink, [MEDIA_URL_SKIPPED_CONTROL_CHAR])

    def test_outer_spaces_trailing(self):
        sink = []
        images = collect_note_images({"imageList": [{"url": "  https://c/a.jpg  "}]}, diagnostics=sink)
        self.assertEqual(len(images), 1)
        self.assertEqual(sink, [])

    def test_inner_control_char_still_skipped_and_diagnosed(self):
        sink = []
        images = collect_note_images({"imageList": [{"url": "https://c/a\rb.jpg"}]}, diagnostics=sink)
        self.assertEqual(images, [])
        self.assertEqual(sink, [MEDIA_URL_SKIPPED_CONTROL_CHAR])

    def test_video_master_outer_whitespace(self):
        sink = []
        videos = collect_video_streams(
            {"video": {"media": {"stream": {"h264": [{"masterUrl": "\thttps://v/a.mp4"}]}}}},
            diagnostics=sink)
        self.assertEqual(videos, [])
        self.assertEqual(sink, [MEDIA_URL_SKIPPED_CONTROL_CHAR])


class VideoBackupFallback(unittest.TestCase):
    """缺陷 PA-3（本轮补）：masterUrl 无效时，正常 backupUrls 不得被丢弃。"""

    def test_invalid_master_url_falls_back_to_first_valid_backup(self):
        note = {"video": {"media": {"stream": {"h264": [
            {"masterUrl": "https://v/bad\rmaster.mp4",
             "backupUrls": ["https://v/b1.mp4", "https://v/b2.mp4"]}]}}}}
        videos = collect_video_streams(note)
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["url"], "https://v/b1.mp4")
        self.assertEqual(videos[0]["backup_urls"], ["https://v/b2.mp4"])

    def test_invalid_master_url_with_single_backup(self):
        note = {"video": {"media": {"stream": {"h264": [
            {"masterUrl": "", "backupUrls": ["https://v/b1.mp4"]}]}}}}
        videos = collect_video_streams(note)
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["url"], "https://v/b1.mp4")
        self.assertEqual(videos[0]["backup_urls"], [])

    def test_all_candidates_invalid_still_drops_the_stream(self):
        note = {"video": {"media": {"stream": {"h264": [
            {"masterUrl": "https://v/bad\rmaster.mp4",
             "backupUrls": ["https://v/bad\rb1.mp4"]}]}}}}
        videos = collect_video_streams(note)
        self.assertEqual(videos, [])

    def test_valid_master_url_is_not_displaced_by_backups(self):
        note = {"video": {"media": {"stream": {"h264": [
            {"masterUrl": "https://v/a.mp4", "backupUrls": ["https://v/b1.mp4"]}]}}}}
        videos = collect_video_streams(note)
        self.assertEqual(videos[0]["url"], "https://v/a.mp4")
        self.assertEqual(videos[0]["backup_urls"], ["https://v/b1.mp4"])


class NonStringCandidateDiagnostics(unittest.TestCase):
    """缺陷 PA-2：非字符串候选被误记 control_char。

    最小输入：video.backupUrls = [{"url": "https://v/b1.mp4"}]（dict，非字符串）。
    期望：dict 无法是“URL 字符串”，既不该被当作可用引用（_url 已排除），
    也不该被记成“控制字符”跳过（它不是控制字符）。
    实际（v2）：sink 记了 media_url_skipped:control_char —— 原因码撒谎。
    """

    def test_dict_backup_entry_is_not_reported_as_control_char(self):
        sink = []
        collect_video_streams(
            {"video": {"media": {"stream": {"h264": [
                {"masterUrl": "https://v/a.mp4", "backupUrls": [{"url": "https://v/b1.mp4"}]}]}}}},
            diagnostics=sink)
        self.assertEqual(sink, [])

    def test_integer_backup_entry_is_not_reported(self):
        sink = []
        collect_video_streams(
            {"video": {"media": {"stream": {"h264": [
                {"masterUrl": "https://v/a.mp4", "backupUrls": [1, 2]}]}}}},
            diagnostics=sink)
        self.assertEqual(sink, [])

    def test_string_backup_with_control_char_still_diagnosed(self):
        sink = []
        collect_video_streams(
            {"video": {"media": {"stream": {"h264": [
                {"masterUrl": "https://v/a.mp4", "backupUrls": ["https://v/bad\rb2.mp4"]}]}}}},
            diagnostics=sink)
        self.assertEqual(sink, [MEDIA_URL_SKIPPED_CONTROL_CHAR])


class NormalizeDiagnosticsStayConsistent(unittest.TestCase):
    """缺陷 PA-1 经 normalize_note 的可见面：media_diagnostics 不得虚报。"""

    def test_normalize_note_outer_whitespace_only(self):
        out = normalize_note({"noteId": "n", "imageList": [{"url": "\thttps://c/a.jpg"}]})
        self.assertEqual(out["image_count"], 0)
        self.assertEqual(out["media_diagnostics"], [MEDIA_URL_SKIPPED_CONTROL_CHAR])

    def test_normalize_note_inner_control_char(self):
        out = normalize_note({"noteId": "n", "imageList": [{"url": "https://c/a\rb.jpg"}]})
        self.assertEqual(out["image_count"], 0)
        self.assertEqual(out["media_diagnostics"], [MEDIA_URL_SKIPPED_CONTROL_CHAR])


if __name__ == "__main__":
    unittest.main()
