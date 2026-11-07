import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from web_content_fetcher.core import FetchRequest, fetch_content
from web_content_fetcher.mcp_server import TOOLS
from web_content_fetcher.wechat_download_api import (
    fetch_wechat_article,
    normalize_wechat_api_base_url,
    normalize_wechat_article_url,
    search_wechat_accounts,
    search_wechat_articles,
)


class FakeResponse:
    def __init__(self, payload):
        self.raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, _limit=-1):
        return self.raw


class WechatDownloadApiTests(unittest.TestCase):
    def test_base_url_allows_local_http_and_rejects_remote_plain_http(self):
        self.assertEqual(normalize_wechat_api_base_url("http://127.0.0.1:5000/"), "http://127.0.0.1:5000")
        self.assertEqual(normalize_wechat_api_base_url("http://host.docker.internal:5000"), "http://host.docker.internal:5000")
        self.assertEqual(normalize_wechat_api_base_url("https://wechat.example.com/api"), "https://wechat.example.com/api")
        with self.assertRaisesRegex(ValueError, "plain_http_requires_local_host"):
            normalize_wechat_api_base_url("http://wechat.example.com")
        with self.assertRaisesRegex(ValueError, "userinfo_not_allowed"):
            normalize_wechat_api_base_url("http://user:secret@127.0.0.1:5000")

    def test_account_search_uses_public_searchbiz_without_exposing_credentials(self):
        payload = {
            "success": True,
            "data": {
                "list": [
                    {"fakeid": "fake-1", "nickname": "科技科普", "alias": "safe", "service_type": 1}
                ]
            },
        }
        with mock.patch("web_content_fetcher.wechat_download_api.urlopen", return_value=FakeResponse(payload)) as opener:
            accounts = search_wechat_accounts("效率工具", base_url="http://127.0.0.1:5000")
        request = opener.call_args.args[0]
        self.assertIn("/api/public/searchbiz?", request.full_url)
        self.assertIn("query=%E6%95%88%E7%8E%87%E5%B7%A5%E5%85%B7", request.full_url)
        self.assertNotIn("token", request.full_url.lower())
        self.assertEqual(accounts[0]["fakeid"], "fake-1")

    def test_article_search_returns_only_real_wechat_article_links(self):
        payload = {
            "success": True,
            "data": {
                "articles": [
                    {"aid": "1", "title": "如何整理资料", "link": "http://mp.weixin.qq.com/s/example?x=1#rd"},
                    {"aid": "2", "title": "普通网页", "link": "https://example.com/not-wechat"},
                ]
            },
        }
        with mock.patch("web_content_fetcher.wechat_download_api.urlopen", return_value=FakeResponse(payload)) as opener:
            articles = search_wechat_articles(
                "fake-1",
                query="资料整理",
                account={"nickname": "科技科普"},
                base_url="http://127.0.0.1:5000",
            )
        request = opener.call_args.args[0]
        self.assertIn("/api/public/articles/search?", request.full_url)
        self.assertEqual([item["url"] for item in articles], ["https://mp.weixin.qq.com/s/example?x=1"])
        self.assertEqual(articles[0]["account_name"], "科技科普")

    def test_article_fetch_posts_direct_link_and_returns_html(self):
        payload = {
            "success": True,
            "data": {
                "title": "如何选择工具",
                "content": "<article><h1>如何选择工具</h1><p>正文内容足够长。</p></article>",
                "plain_content": "如何选择工具 正文内容足够长。",
                "images": ["https://mmbiz.qpic.cn/example"],
                "author": "科技科普",
                "publish_time": 1,
            },
        }
        with mock.patch("web_content_fetcher.wechat_download_api.urlopen", return_value=FakeResponse(payload)) as opener:
            article = fetch_wechat_article(
                "https://mp.weixin.qq.com/s/example",
                base_url="http://127.0.0.1:5000",
            )
        request = opener.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data.decode("utf-8"))["url"], "https://mp.weixin.qq.com/s/example")
        self.assertEqual(article["title"], "如何选择工具")

    def test_backend_archives_api_html_with_provider_metadata(self):
        article = {
            "url": "https://mp.weixin.qq.com/s/example",
            "title": "如何验证资料",
            "content": "<html><body><article><h1>如何验证资料</h1><p>这是用于测试的完整正文内容，包含选择逻辑和注意事项。</p></article></body></html>",
            "plain_content": "如何验证资料",
            "images": [],
            "author": "科技科普",
            "publish_time": 1,
            "publish_time_str": "1970-01-01 08:00:01",
            "provider": "wechat-download-api",
            "api_base_url": "http://127.0.0.1:5000",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("web_content_fetcher.core.fetch_wechat_article", return_value=article):
                result = fetch_content(
                    FetchRequest(
                        url=article["url"],
                        backend="wechat_download_api",
                        archive_dir=tmp,
                        check_robots=False,
                    )
                )
            structured = Path(result.archive["structured_path"])
            saved = json.loads(structured.read_text(encoding="utf-8"))
        self.assertEqual(result.transport, "wechat_download_api")
        self.assertEqual(saved["site_data"]["wechat_download_api"]["author"], "科技科普")

    def test_mcp_publishes_discovery_tools(self):
        names = {tool["name"] for tool in TOOLS}
        self.assertIn("search_wechat_accounts", names)
        self.assertIn("search_wechat_articles", names)
        search_tool = next(tool for tool in TOOLS if tool["name"] == "search_and_archive_wechat")
        self.assertIn("discovery_provider", search_tool["inputSchema"]["properties"])
        self.assertIn("article_query", search_tool["inputSchema"]["properties"])

    def test_normalizer_rejects_non_wechat_hosts(self):
        self.assertEqual(normalize_wechat_article_url("https://mp.weixin.qq.com/s/ok"), "https://mp.weixin.qq.com/s/ok")
        self.assertEqual(normalize_wechat_article_url("https://mp.weixin.qq.com.example.com/s/no"), "")


if __name__ == "__main__":
    unittest.main()
