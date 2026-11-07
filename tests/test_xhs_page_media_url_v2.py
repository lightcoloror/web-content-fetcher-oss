"""A 侧 v2 回归测试：媒体候选被拒绝时的最小失败范围 + 最小诊断。

冻结基线缺陷的中性表述（依据 ``logs/01-mechanism.txt`` 实测，不是推断）：

    ``_url`` 用 ``urlparse`` 校验后返回**原字符串**。``urlsplit`` 会在解析前静默
    删除 tab/CR/LF，因此 ``"https://cdn.test/a\\rb.jpg"`` 通过了 scheme/hostname
    校验，并被**原样（仍含控制字符）**当作可用媒体引用发布；``_url`` 与
    ``collect_note_images`` 都没有改写过它。真正的静默截断只发生在下游：把该字符串
    再喂给 ``urlparse(...).geturl()`` 会得到不同的 URL ``"https://cdn.test/ab.jpg"``。

因此本文件断言的是两件事：
  1. 含控制字符（或空格）的候选**不进入可用媒体引用**，且**不被改写**成别的 URL；
  2. **单个坏候选不得使整页失败** —— 同一媒体的正常备选、其他正常媒体、有效正文继续保留，
     被跳过的媒体引用以最小诊断字段显式记录（只记原因码，不记 URL）。

必须覆盖的场景（任务书要求）：
  坏主候选＋正常备选 / 正常主候选＋坏备选 / 一个坏媒体＋另一个好媒体 /
  仅正文＋坏媒体 / 正常页面行为不变
"""
import json
import unittest

from web_content_fetcher.xhs_page import (
    MEDIA_URL_SKIPPED_CONTROL_CHAR,
    _url,
    collect_note_images,
    collect_video_streams,
    normalize_note,
    parse_xhs_page,
)

BAD = "https://c/bad\rcandidate.jpg"
BAD_NUL = "https://c/bad\x00candidate.jpg"
BAD_TAB = "https://c/bad\tcandidate.jpg"
GOOD = "https://c/good.jpg"
GOOD2 = "https://c/good2.jpg"
OK_PAGE = "https://www.xiaohongshu.com/explore/abc"


def page(note):
    return ('window.__INITIAL_STATE__='
            + json.dumps({"noteData": {"data": {"noteData": note}}}, ensure_ascii=False)
            + ';</script>')


class UrlMechanismTests(unittest.TestCase):
    """意见1：把机制钉死在测试里，防止结论再次被写成“解析器改写了 URL”。"""

    def test_url_rejects_broken_candidates_and_never_rewrites_them(self):
        # 对每个坏候选同时钉两件事：
        #   v2 契约 = 返回 ""（不接受、不发布）
        #   并且返回值绝不可以是"另一个看起来正常的 URL"
        rewritten = "https://c/badcandidate.jpg"
        for bad in (BAD, BAD_NUL, BAD_TAB):
            with self.subTest(url=bad):
                self.assertEqual(_url(bad), "")
                self.assertNotEqual(_url(bad), rewritten)

    def test_rejected_candidates_are_not_rewritten_into_a_different_url(self):
        images = collect_note_images({"imageList": [{"url": BAD}, {"url": BAD_NUL}, {"url": BAD_TAB}]})
        self.assertEqual(images, [])

    def test_urlparse_is_what_rewrites_tab_cr_lf_not_this_module(self):
        from urllib.parse import urlparse
        # 记录下游风险：这个改写不是本模块做的，而是重新解析时发生的。
        self.assertEqual(urlparse(BAD).geturl(), "https://c/badcandidate.jpg")
        self.assertNotEqual(urlparse(BAD).geturl(), BAD)


class MinimalBlastRadiusTests(unittest.TestCase):
    """意见2：坏候选不得让整页失败，也不得连累正常内容。"""

    def test_bad_primary_candidate_keeps_the_good_alternative(self):
        note = {"imageList": [{"urlDefault": BAD, "url": GOOD,
                              "infoList": [{"url": GOOD2}]}]}
        out = normalize_note(note)
        self.assertEqual(out["image_count"], 1)
        self.assertEqual(out["images"][0]["url"], GOOD)
        self.assertEqual(out["images"][0]["fallback_urls"], [GOOD2])

    def test_bad_primary_candidate_falls_back_to_file_id(self):
        out = normalize_note({"imageList": [{"urlDefault": BAD, "fileId": "notes/a"}]})
        self.assertEqual(out["image_count"], 1)
        self.assertEqual(out["images"][0]["url"], "https://sns-img-hw.xhscdn.com/notes/a")

    def test_good_primary_candidate_survives_a_bad_alternative(self):
        out = normalize_note({"imageList": [{"url": GOOD, "infoList": [{"url": BAD}]}]})
        self.assertEqual(out["image_count"], 1)
        self.assertEqual(out["images"][0]["url"], GOOD)
        self.assertEqual(out["images"][0]["fallback_urls"], [])

    def test_one_bad_media_does_not_remove_the_other_good_media(self):
        out = normalize_note({"noteId": "abc", "desc": "正文", "imageList": [{"url": BAD}, {"url": GOOD}]})
        self.assertEqual(out["image_count"], 1)
        self.assertEqual([i["url"] for i in out["images"]], [GOOD])

    def test_body_only_with_a_bad_media_still_parses(self):
        out = parse_xhs_page(page({"noteId": "abc", "desc": "只有正文", "imageList": [{"url": BAD}]}),
                             source_url=OK_PAGE)
        self.assertEqual(out["description"], "只有正文")
        self.assertEqual(out["images"], [])
        self.assertEqual(out["image_count"], 0)
        self.assertEqual(out["media_diagnostics"], [MEDIA_URL_SKIPPED_CONTROL_CHAR])

    def test_bad_video_master_url_does_not_remove_the_rest_of_the_note(self):
        note = {"noteId": "abc", "desc": "正文",
                "imageList": [{"url": GOOD}],
                "video": {"media": {"stream": {"h264": [{"masterUrl": BAD, "backupUrls": [GOOD2]}]}}}}
        out = normalize_note(note)
        self.assertEqual(out["image_count"], 1)
        # 有效备选必须保留：坏 masterUrl 回落为第一个有效 backup 作为主引用。
        self.assertEqual(len(out["video_streams"]), 1)
        self.assertEqual(out["video_streams"][0]["url"], GOOD2)
        self.assertEqual(out["video_streams"][0]["backup_urls"], [])
        self.assertEqual(out["description"], "正文")
        self.assertIn(MEDIA_URL_SKIPPED_CONTROL_CHAR, out["media_diagnostics"])

    def test_normal_page_behaviour_is_unchanged(self):
        out = parse_xhs_page(page({"noteId": "abc", "title": "T", "desc": "D",
                                   "imageList": [{"fileId": "oss-ae/notes/a"}]}), source_url=OK_PAGE)
        self.assertEqual(out["title"], "T")
        self.assertEqual(out["images"][0]["url"], "https://sns-img-hw.xhscdn.com/oss-ae/notes/a")
        self.assertEqual(out["media_diagnostics"], [])
        self.assertEqual(out["media_status"], "references_only")
        self.assertEqual(out["comments_status"], "not_collected")


class DiagnosticContractTests(unittest.TestCase):
    """诊断字段：最小、向后兼容、且不泄漏可能带令牌的 URL。"""

    def test_diagnostic_is_additive_and_absent_from_unaffected_outputs(self):
        self.assertEqual(normalize_note({"noteId": "n", "imageList": [{"url": GOOD}]})["media_diagnostics"], [])

    def test_existing_signatures_still_work_positionally_and_by_keyword(self):
        # 兼容性：旧调用方式（无 diagnostics）必须完全不受影响。
        self.assertEqual(len(collect_note_images({"imageList": [{"url": GOOD}]})), 1)
        self.assertEqual(collect_video_streams({"video": {}}), [])
        out = normalize_note({"noteId": "n", "imageList": [{"url": GOOD}]}, source_url="u", source="s")
        self.assertEqual(out["source_url"], "u")
        self.assertEqual(out["source"], "s")

    def test_diagnostics_records_reason_codes_only_and_never_the_url(self):
        out = normalize_note({"noteId": "abc", "desc": "d",
                              "imageList": [{"url": "https://c/secret\x00token=abc123"}]})
        self.assertEqual(out["media_diagnostics"], [MEDIA_URL_SKIPPED_CONTROL_CHAR])
        # `raw` is by design the untouched source payload, so it is excluded here; the
        # diagnostic itself and every collected-media field must carry no URL content.
        leak_surface = {k: v for k, v in out.items() if k != "raw"}
        blob = json.dumps(leak_surface, ensure_ascii=False, default=str)
        self.assertNotIn("secret", blob)
        self.assertNotIn("abc123", blob)
        self.assertNotIn("\\u0000", blob)
        self.assertEqual(out["images"], [])
        self.assertEqual(out["image_count"], 0)

    def test_diagnostics_are_sorted_and_deduplicated(self):
        out = normalize_note({"noteId": "abc", "imageList": [{"url": BAD}, {"url": BAD_NUL}, {"url": BAD_TAB}]})
        self.assertEqual(out["media_diagnostics"], [MEDIA_URL_SKIPPED_CONTROL_CHAR])

    def test_collect_note_images_out_parameter_is_optional_and_off_by_default(self):
        sink = []
        collect_note_images({"imageList": [{"url": BAD}]}, diagnostics=sink)
        self.assertEqual(sink, [MEDIA_URL_SKIPPED_CONTROL_CHAR])

    def test_unparseable_page_keeps_the_original_error_semantics(self):
        # 本轮不发明新的“页面是否有用”标准：坏媒体不影响原有的缺状态错误。
        with self.assertRaisesRegex(ValueError, "xhs_initial_state_note_missing"):
            parse_xhs_page("<html>登录</html>", source_url=OK_PAGE)
        with self.assertRaisesRegex(ValueError, "not_a_xiaohongshu_url"):
            parse_xhs_page(page({"noteId": "abc"}), source_url="https://evil.test/explore/abc")


if __name__ == "__main__":
    unittest.main()
