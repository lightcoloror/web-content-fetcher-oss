import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from web_content_fetcher.batch import BatchFetchRequest
from web_content_fetcher.cli import build_parser
from web_content_fetcher.mcp_server import TOOLS
from web_content_fetcher.wechat_search import (
    WechatSearchArchiveRequest,
    load_discovery_manifest,
    normalize_wechat_article_url,
    run_get_wechat_oa_discovery,
    search_and_archive_wechat,
)


class WechatSearchTests(unittest.TestCase):
    def test_normalize_wechat_article_url_requires_exact_host(self):
        self.assertEqual(
            normalize_wechat_article_url("http://mp.weixin.qq.com/s/example?x=1#fragment"),
            "https://mp.weixin.qq.com/s/example?x=1",
        )
        self.assertEqual(normalize_wechat_article_url("https://mp.weixin.qq.com.example.com/s/example"), "")
        self.assertEqual(normalize_wechat_article_url("https://mp.weixin.qq.com/mp/appmsgalbum?id=1"), "")

    def test_manifest_filters_invalid_and_duplicate_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(
                json.dumps(
                    [
                        {"url": "https://mp.weixin.qq.com/s/one", "title": "One"},
                        {"url": "http://mp.weixin.qq.com/s/one#copy", "title": "Duplicate"},
                        {"url": "https://mp.weixin.qq.com.attacker.invalid/s/two", "title": "Bad"},
                    ]
                ),
                encoding="utf-8",
            )
            candidates = load_discovery_manifest(path)
        self.assertEqual([item["url"] for item in candidates], ["https://mp.weixin.qq.com/s/one"])

    def test_get_wechat_oa_adapter_uses_discover_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "provider-repo"
            provider.mkdir()
            script = provider / "collect_by_name.py"
            script.write_text("# fixture\n", encoding="utf-8")
            run_root = root / "run"
            run_root.mkdir()

            def fake_run(command, **kwargs):
                output = Path(command[command.index("--output") + 1])
                manifest_dir = output / "wechat_articles" / "example"
                manifest_dir.mkdir(parents=True)
                (manifest_dir / "discovered_articles.json").write_text(
                    json.dumps([{"url": "https://mp.weixin.qq.com/s/example", "title": "Example"}]),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

            request = WechatSearchArchiveRequest(
                account_name="Example Account",
                archive_dir=str(root / "archives"),
                provider_path=str(provider),
            )
            with mock.patch("web_content_fetcher.wechat_search.subprocess.run", side_effect=fake_run) as run:
                result = run_get_wechat_oa_discovery(request, run_root)

        command = run.call_args.args[0]
        self.assertIn("--discover-only", command)
        self.assertNotIn("--mode", command)
        self.assertEqual(result["candidates"][0]["url"], "https://mp.weixin.qq.com/s/example")

    def test_get_wechat_oa_adapter_maps_zero_results_to_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = root / "provider-repo"
            provider.mkdir()
            (provider / "collect_by_name.py").write_text("# fixture\n", encoding="utf-8")
            run_root = root / "run"
            run_root.mkdir()
            request = WechatSearchArchiveRequest(
                account_name="No Results",
                archive_dir=str(root / "archives"),
                provider_path=str(provider),
            )
            completed = subprocess.CompletedProcess(
                ["provider"],
                1,
                stdout="搜索阶段共发现 0 个候选链接\n没有找到匹配该公众号名称的可公开文章。",
                stderr="",
            )
            with mock.patch("web_content_fetcher.wechat_search.subprocess.run", return_value=completed):
                result = run_get_wechat_oa_discovery(request, run_root)

        self.assertEqual(result["provider_status"], "not_found")
        self.assertEqual(result["candidates"], [])

    def test_search_workflow_passes_candidates_to_batch_and_writes_index(self):
        captured: list[BatchFetchRequest] = []

        def fake_discovery(request, run_root):
            return {
                "provider": "fixture",
                "engine": request.engine,
                "candidates": [
                    {"url": "https://mp.weixin.qq.com/s/one", "title": "One"},
                    {"url": "https://mp.weixin.qq.com/s/two", "title": "Two"},
                ],
            }

        def fake_fetch(request):
            captured.append(request)
            return {
                "status": "partial",
                "total": 2,
                "ok": 1,
                "failed": 1,
                "items": [
                    {
                        "requested_url": "https://mp.weixin.qq.com/s/one",
                        "status": "ok",
                        "quality_status": "good",
                        "archive_path": "archive-one",
                    },
                    {
                        "requested_url": "https://mp.weixin.qq.com/s/two",
                        "status": "failed",
                        "quality_status": "empty",
                        "archive_path": "",
                    },
                ],
            }

        with tempfile.TemporaryDirectory() as tmp:
            result = search_and_archive_wechat(
                WechatSearchArchiveRequest(account_name="Example", archive_dir=tmp),
                discovery_runner=fake_discovery,
                fetch_runner=fake_fetch,
            )
            summary = Path(result["paths"]["summary_path"])
            index = Path(result["paths"]["index_path"])
            self.assertTrue(summary.is_file())
            self.assertIn("archive-one", index.read_text(encoding="utf-8"))
            saved = json.loads(summary.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["complete_account_history"])
        self.assertEqual(captured[0].urls, ["https://mp.weixin.qq.com/s/one", "https://mp.weixin.qq.com/s/two"])
        self.assertEqual(saved["scope"], "publicly_indexed_articles")

    def test_provider_failure_still_writes_agent_readable_summary(self):
        def fail_discovery(request, run_root):
            raise RuntimeError("provider unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            result = search_and_archive_wechat(
                WechatSearchArchiveRequest(account_name="Example", archive_dir=tmp),
                discovery_runner=fail_discovery,
            )
            summary = Path(result["paths"]["summary_path"])
            self.assertTrue(summary.is_file())
            self.assertIn("provider unavailable", summary.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["candidates"], [])

    def test_cli_and_mcp_publish_wechat_search_contract(self):
        args = build_parser().parse_args(
            ["wechat-search", "Example", "--archive-dir", "archive", "--discover-only"]
        )
        self.assertEqual(args.command, "wechat-search")
        self.assertTrue(args.discover_only)
        tool = next(item for item in TOOLS if item["name"] == "search_and_archive_wechat")
        self.assertEqual(tool["inputSchema"]["required"], ["account_name", "archive_dir"])


    def test_wechat_download_api_discovery_searches_matched_accounts(self):
        request = WechatSearchArchiveRequest(
            account_name="科技科普",
            article_query="如何整理资料",
            archive_dir="archive",
            discovery_provider="wechat_download_api",
            account_limit=2,
        )
        accounts = [
            {"fakeid": "one", "nickname": "科普一号"},
            {"fakeid": "two", "nickname": "科普二号"},
        ]

        def fake_articles(fakeid, **kwargs):
            return [{"url": f"https://mp.weixin.qq.com/s/{fakeid}", "title": kwargs["query"]}]

        from web_content_fetcher.wechat_search import run_wechat_download_api_discovery

        with mock.patch("web_content_fetcher.wechat_search.search_wechat_accounts", return_value=accounts):
            with mock.patch("web_content_fetcher.wechat_search.search_wechat_articles", side_effect=fake_articles) as articles:
                result = run_wechat_download_api_discovery(request, Path("archive"))

        self.assertEqual(result["provider"], "wechat-download-api")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(articles.call_args_list[0].kwargs["query"], "如何整理资料")

    def test_api_discovery_uses_api_backend_for_body_archive(self):
        captured = []

        def fake_discovery(request, run_root):
            return {
                "provider": "wechat-download-api",
                "candidates": [{"url": "https://mp.weixin.qq.com/s/one", "title": "One"}],
            }

        def fake_fetch(request):
            captured.append(request)
            return {"status": "ok", "total": 1, "ok": 1, "failed": 0, "items": []}

        with tempfile.TemporaryDirectory() as tmp:
            result = search_and_archive_wechat(
                WechatSearchArchiveRequest(
                    account_name="科技科普",
                    archive_dir=tmp,
                    wechat_api_base_url="http://127.0.0.1:5000",
                ),
                discovery_runner=fake_discovery,
                fetch_runner=fake_fetch,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(captured[0].backend, "wechat_download_api")
        self.assertFalse(captured[0].as_documents)
        self.assertEqual(captured[0].wechat_api_base_url, "http://127.0.0.1:5000")

    def test_cli_accepts_api_provider_and_article_query(self):
        args = build_parser().parse_args(
            [
                "wechat-search",
                "科技科普",
                "--archive-dir",
                "archive",
                "--discovery-provider",
                "wechat_download_api",
                "--article-query",
                "如何验证资料",
            ]
        )
        self.assertEqual(args.discovery_provider, "wechat_download_api")
        self.assertEqual(args.article_query, "如何验证资料")


if __name__ == "__main__":
    unittest.main()
