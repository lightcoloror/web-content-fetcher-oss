import unittest
from unittest import mock

from web_content_fetcher.core import FetchRequest, fetch_content
from web_content_fetcher.diagnostics import detect_document_mismatch, detect_login_redirect


class DocumentDiagnosticsTests(unittest.TestCase):
    def test_navigation_and_script_login_are_not_access_gates(self):
        html = '<script>const login="/login";</script><header><a id="userlogin">请登录</a></header><article>Public notice</article>'
        self.assertFalse(detect_login_redirect(html, title="Public notice")["detected"])

    def test_login_route_and_title(self):
        self.assertTrue(detect_login_redirect("", final_url="https://example.org/#/login")["detected"])
        self.assertTrue(detect_login_redirect("", title="Login - Example")["detected"])
        self.assertFalse(detect_login_redirect("", final_url="https://example.org/login-guide")["detected"])

    def test_visible_requirement_and_form(self):
        self.assertTrue(detect_login_redirect("<main>请登录后查看</main>")["detected"])
        self.assertTrue(detect_login_redirect('<form><label>Password</label><input type="password"></form>')["detected"])
        self.assertFalse(detect_login_redirect('<div hidden>扫码登录</div><article>Public article</article>')["detected"])

    def test_tencent_document_id_not_query_or_scheme(self):
        url = "https://cloud.tencent.com/document/product/243/97691"
        self.assertTrue(detect_document_mismatch(url, url.replace("97691", "53141"))["detected"])
        self.assertFalse(detect_document_mismatch(url, url + "?from=search")["detected"])
        self.assertFalse(detect_document_mismatch(url, url.replace("https:", "http:"))["detected"])
        self.assertFalse(detect_document_mismatch("https://example.org/a", "https://example.org/b")["checked"])

    def test_wrong_document_cannot_be_content_ok(self):
        url = "https://cloud.tencent.com/document/product/243/97691"
        html = '<html><title>Other document</title><article>' + '<p>A complete but unrelated public document.</p>' * 30 + '</article></html>'
        with mock.patch("web_content_fetcher.core.direct_fetch", return_value=(html, "text/html", url.replace("97691", "53141"))):
            result = fetch_content(FetchRequest(url=url, backend="direct", extractor="builtin", check_robots=False))
        result.assess_validity()
        self.assertFalse(result.content_ok)
        self.assertNotEqual(result.status, "ok")
        self.assertTrue(result.extraction_quality["document_identity_mismatch"])
        self.assertIn("document_identity_mismatch", result.errors)


if __name__ == "__main__":
    unittest.main()
