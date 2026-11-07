from __future__ import annotations

import json
import sys
from typing import Any

from .browser_fetch import COMMON_LOGIN_URLS, browser_profile_status, default_profile_dir
from .core import FetchRequest, fetch_content
from .document_router import fetch_document_link
from .zsxq_cli import ZsxqRequest, run_zsxq


FIRECRAWL_SCHEMA = {
    "firecrawl_api_url": {"type": "string", "default": ""},
    "firecrawl_api_key_env": {"type": "string", "default": "FIRECRAWL_API_KEY"},
    "firecrawl_formats": {"type": "array", "items": {"type": "string"}, "default": ["markdown", "html", "links"]},
    "firecrawl_only_main_content": {"type": "boolean", "default": True},
    "firecrawl_wait_for_ms": {"type": "integer", "default": 0},
}


SCRAPLING_SCHEMA = {
    "scrapling_mode": {"type": "string", "default": "static"},
    "scrapling_headless": {"type": "boolean", "default": True},
    "scrapling_network_idle": {"type": "boolean", "default": False},
    "scrapling_wait_ms": {"type": "integer", "default": 0},
    "scrapling_wait_selector": {"type": "string", "default": ""},
    "scrapling_real_chrome": {"type": "boolean", "default": False},
    "scrapling_user_data_dir": {"type": "string", "default": ""},
    "scrapling_python_path": {"type": "string", "default": ""},
    "scrapling_python_executable": {"type": "string", "default": ""},
}


SNAPSHOT_SCHEMA = {
    "snapshot_backend": {"type": "string", "default": ""},
    "monolith_executable": {"type": "string", "default": ""},
    "monolith_mhtml": {"type": "boolean", "default": False},
}

WECHAT_API_SCHEMA = {
    "wechat_api_base_url": {"type": "string", "default": ""},
}


CRAWL4AI_SCHEMA = {
    "crawl4ai_wait_for": {"type": "string", "default": ""},
    "crawl4ai_css_selector": {"type": "string", "default": ""},
    "crawl4ai_screenshot": {"type": "boolean", "default": False},
    "crawl4ai_pdf": {"type": "boolean", "default": False},
    "crawl4ai_cache_mode": {"type": "string", "default": "bypass"},
}


TOOLS = [
    {
        "name": "fetch_url",
        "description": "Fetch a URL and return structured text, links, images, selector results, robots hint, and hashes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "fetch_profile": {"type": "string", "default": "generic"},
                "backend": {"type": "string", "default": "auto"},
                "extractor": {"type": "string", "default": "auto"},
                "compare_feishu_export": {"type": "boolean", "default": False},
                "selectors": {"type": "array", "items": {"type": "object"}},
                "use_web_access": {"type": "boolean", "default": False},
                "prefer_browser": {"type": "boolean", "default": False},
                "timeout_seconds": {"type": "integer", "default": 15},
                **CRAWL4AI_SCHEMA,
                **FIRECRAWL_SCHEMA,
                **SCRAPLING_SCHEMA,
                **SNAPSHOT_SCHEMA,
                **WECHAT_API_SCHEMA,
            },
            "required": ["url"],
        },
    },
    {
        "name": "fetch_url_with_archive",
        "description": "Fetch a URL and save raw_snapshot, extracted_text, and metadata/hash archive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "archive_dir": {"type": "string"},
                "fetch_profile": {"type": "string", "default": "generic"},
                "backend": {"type": "string", "default": "auto"},
                "extractor": {"type": "string", "default": "auto"},
                "compare_feishu_export": {"type": "boolean", "default": False},
                "use_web_access": {"type": "boolean", "default": False},
                "use_browser": {"type": "boolean", "default": False},
                "download_images": {"type": "boolean", "default": False},
                "timeout_seconds": {"type": "integer", "default": 15},
                **CRAWL4AI_SCHEMA,
                **FIRECRAWL_SCHEMA,
                **SCRAPLING_SCHEMA,
                **SNAPSHOT_SCHEMA,
                **WECHAT_API_SCHEMA,
            },
            "required": ["url", "archive_dir"],
        },
    },
    {
        "name": "extract_selectors",
        "description": "Fetch a URL and extract configured CSS-like selector regions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "selectors": {"type": "array", "items": {"type": "object"}},
                "extractor": {"type": "string", "default": "auto"},
                "timeout_seconds": {"type": "integer", "default": 15},
            },
            "required": ["url", "selectors"],
        },
    },
    {
        "name": "fetch_with_browser",
        "description": "Fetch a URL through local Playwright browser state. Uses only configured logged-in credentials and does not bypass paywalls, CAPTCHAs, or access controls.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "archive_dir": {"type": "string", "default": ""},
                "backend": {"type": "string", "default": "auto"},
                "extractor": {"type": "string", "default": "auto"},
                "compare_feishu_export": {"type": "boolean", "default": False},
                "profile": {"type": "string", "default": ""},
                "chrome_user_data_dir": {"type": "string", "default": ""},
                "headless": {"type": "boolean", "default": False},
                "screenshot": {"type": "boolean", "default": True},
                "download_images": {"type": "boolean", "default": True},
                "singlefile": {"type": "boolean", "default": False},
                **SNAPSHOT_SCHEMA,
                "auto_scroll": {"type": "boolean", "default": False},
                "scroll_steps": {"type": "integer", "default": 8},
                "timeout_seconds": {"type": "integer", "default": 30},
            },
            "required": ["url"],
        },
    },
    {
        "name": "fetch_document_link",
        "description": "Fetch a document-like URL with automatic routing. Feishu docx links use browser rendering, auto-scroll, screenshot, and session-aware image downloads.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "archive_dir": {"type": "string", "default": ""},
                "backend": {"type": "string", "default": "auto"},
                "extractor": {"type": "string", "default": "auto"},
                "compare_feishu_export": {"type": "boolean", "default": False},
                "profile": {"type": "string", "default": ""},
                "chrome_user_data_dir": {"type": "string", "default": ""},
                "headless": {"type": "boolean", "default": False},
                "timeout_seconds": {"type": "integer", "default": 30},
                **CRAWL4AI_SCHEMA,
                **FIRECRAWL_SCHEMA,
                **SCRAPLING_SCHEMA,
                **WECHAT_API_SCHEMA,
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_profile_info",
        "description": "Return the tool-owned browser profile path and login guidance. This is read-only and does not open a browser.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "bridge_doctor",
        "description": "Read-only readiness check for optional external bridges: Crawl4AI, MarkItDown, ArchiveBox, and ebook_markdown_pipeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "archivebox_executable": {"type": "string", "default": "archivebox"},
                "visual_command": {"type": "string", "default": ""},
            },
        },
    },
    {
        "name": "run_quality_smoke",
        "description": "Run the opt-in real-link quality corpus and write smoke_summary, quality_failures, and rerun_plan reports.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "corpus_path": {"type": "string", "default": ""},
                "output_root": {"type": "string", "default": ""},
                "backend": {"type": "string", "default": "auto"},
                "extractor": {"type": "string", "default": "auto"},
                "timeout_seconds": {"type": "integer", "default": 90},
                "limit": {"type": "integer", "default": 0},
                "compare_feishu_export": {"type": "boolean", "default": False},
                "headless": {"type": "boolean", "default": False},
                "baseline_path": {"type": "string", "default": ""},
                "make_recovery_pack": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "inspect_quality_run",
        "description": "Read a quality smoke run directory or smoke_summary.json.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_path": {"type": "string"}},
            "required": ["run_path"],
        },
    },
    {
        "name": "make_rerun_plan",
        "description": "Build a rerun plan from a saved quality smoke run.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_path": {"type": "string"}},
            "required": ["run_path"],
        },
    },
    {
        "name": "make_manual_recovery_pack",
        "description": "Write manual_recovery.md/json and rerun-urls.txt for a quality smoke run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_path": {"type": "string"},
                "output_dir": {"type": "string", "default": ""},
            },
            "required": ["run_path"],
        },
    },
    {
        "name": "fetch_batch",
        "description": "Fetch multiple URLs and return a stable summary with status, transport, extraction quality, archive paths, and errors.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"}},
                "archive_dir": {"type": "string", "default": ""},
                "backend": {"type": "string", "default": "auto"},
                "extractor": {"type": "string", "default": "auto"},
                "compare_feishu_export": {"type": "boolean", "default": False},
                "as_documents": {"type": "boolean", "default": True},
                "profile": {"type": "string", "default": ""},
                "chrome_user_data_dir": {"type": "string", "default": ""},
                "headless": {"type": "boolean", "default": False},
                "timeout_seconds": {"type": "integer", "default": 30},
                "include_results": {"type": "boolean", "default": False},
                **WECHAT_API_SCHEMA,
            },
            "required": ["urls"],
        },
    },
    {
        "name": "search_and_archive_wechat",
        "description": "Search WeChat public accounts and article links through local wechat-download-api, with get-WeChat-OA as an optional public-index fallback, then archive results with WCF.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_name": {"type": "string"},
                "archive_dir": {"type": "string"},
                "provider_path": {"type": "string", "default": ""},
                "discovery_provider": {"type": "string", "enum": ["auto", "wechat_download_api", "get_wechat_oa"], "default": "auto"},
                "article_query": {"type": "string", "default": ""},
                "account_limit": {"type": "integer", "default": 5},
                "begin": {"type": "integer", "default": 0},
                **WECHAT_API_SCHEMA,
                "engine": {"type": "string", "enum": ["sogou", "bing", "duckduckgo", "all"], "default": "sogou"},
                "pages": {"type": "integer", "default": 2},
                "limit": {"type": "integer", "default": 20},
                "delay_seconds": {"type": "number", "default": 1.0},
                "discovery_timeout_seconds": {"type": "integer", "default": 300},
                "fetch_articles": {"type": "boolean", "default": True},
                "backend": {"type": "string", "default": "auto"},
                "extractor": {"type": "string", "default": "auto"},
                "profile": {"type": "string", "default": ""},
                "chrome_user_data_dir": {"type": "string", "default": ""},
                "browser_executable": {"type": "string", "default": ""},
                "storage_state": {"type": "string", "default": ""},
                "headless": {"type": "boolean", "default": False},
                "timeout_seconds": {"type": "integer", "default": 30}
            },
            "required": ["account_name", "archive_dir"]
        }
    },
    {
        "name": "search_wechat_accounts",
        "description": "Search public-account identities through a locally deployed wechat-download-api official-login session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "timeout_seconds": {"type": "integer", "default": 30},
                **WECHAT_API_SCHEMA,
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_wechat_articles",
        "description": "List recent articles or search within one public account and return validated mp.weixin.qq.com links.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fakeid": {"type": "string"},
                "query": {"type": "string", "default": ""},
                "begin": {"type": "integer", "default": 0},
                "count": {"type": "integer", "default": 20},
                "timeout_seconds": {"type": "integer", "default": 30},
                **WECHAT_API_SCHEMA,
            },
            "required": ["fakeid"],
        },
    },
    {
        "name": "get_archive",
        "description": "Read structured.json from a Web Content Fetcher archive folder, falling back to metadata.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "archive_path": {"type": "string"},
            },
            "required": ["archive_path"],
        },
    },
    {
        "name": "inspect_archive",
        "description": "Read a Web Content Fetcher archive and return the structured archive index.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "archive_path": {"type": "string"},
            },
            "required": ["archive_path"],
        },
    },
    {
        "name": "list_archives",
        "description": "List Web Content Fetcher archives in an evidence root directory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "archive_dir": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["archive_dir"],
        },
    },
    {
        "name": "list_batch_reports",
        "description": "List resumable batch reports, including review.md and rerun-urls.txt paths.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "archive_dir": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["archive_dir"],
        },
    },
    {
        "name": "rerun_batch_report",
        "description": "Rerun URLs from a previous batch report's rerun-urls.txt and write a new batch report.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "batch_path": {"type": "string", "default": ""},
                "rerun_urls_path": {"type": "string", "default": ""},
                "archive_dir": {"type": "string", "default": ""},
                "backend": {"type": "string", "default": "auto"},
                "extractor": {"type": "string", "default": "auto"},
                "compare_feishu_export": {"type": "boolean", "default": False},
                "as_documents": {"type": "boolean", "default": True},
                "profile": {"type": "string", "default": ""},
                "chrome_user_data_dir": {"type": "string", "default": ""},
                "headless": {"type": "boolean", "default": False},
                "timeout_seconds": {"type": "integer", "default": 30},
                "include_results": {"type": "boolean", "default": False},
                **WECHAT_API_SCHEMA,
            },
        },
    },
    {
        "name": "review_batch",
        "description": "Read a saved batch report and return batch_review.json style review data for human/agent triage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "batch_path": {"type": "string"},
            },
            "required": ["batch_path"],
        },
    },
    {
        "name": "rerun_incomplete_batch",
        "description": "Rerun failed or low-quality URLs from a saved batch report. Optional strategy maps to browser, crawl4ai, feishu_export, or manual review.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "batch_path": {"type": "string", "default": ""},
                "rerun_urls_path": {"type": "string", "default": ""},
                "archive_dir": {"type": "string", "default": ""},
                "strategy": {"type": "string", "default": ""},
                "backend": {"type": "string", "default": "auto"},
                "extractor": {"type": "string", "default": "auto"},
                "compare_feishu_export": {"type": "boolean", "default": False},
                "as_documents": {"type": "boolean", "default": True},
                "profile": {"type": "string", "default": ""},
                "chrome_user_data_dir": {"type": "string", "default": ""},
                "headless": {"type": "boolean", "default": False},
                "timeout_seconds": {"type": "integer", "default": 30},
                "include_results": {"type": "boolean", "default": False},
                **WECHAT_API_SCHEMA,
            },
        },
    },
    {
        "name": "pack_archive",
        "description": "Zip one Web Content Fetcher evidence archive directory and return zip path, size, and hash.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "archive_path": {"type": "string"},
                "output_path": {"type": "string", "default": ""},
            },
            "required": ["archive_path"],
        },
    },
    {
        "name": "compare_feishu_export_archive",
        "description": "Add official Feishu/Lark DOCX export comparison to an existing archive without refetching the web page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "archive_path": {"type": "string"},
                "timeout_seconds": {"type": "integer", "default": 60},
            },
            "required": ["archive_path"],
        },
    },
    {
        "name": "rebuild_archive",
        "description": "Build final_readable.html, final_agent.md, final_structured.json, completeness_report.json, and rebuild_input/ from an evidence archive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "archive_path": {"type": "string"},
                "with_visual_check": {"type": "boolean", "default": False},
                "visual_dir": {"type": "string", "default": ""},
            },
            "required": ["archive_path"],
        },
    },
    {
        "name": "fetch_and_rebuild",
        "description": "Fetch a document URL, rebuild its archive into human/agent readable files, optionally run ebook_markdown_pipeline visual recovery, then rebuild again.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "archive_dir": {"type": "string", "default": ""},
                "backend": {"type": "string", "default": "auto"},
                "extractor": {"type": "string", "default": "auto"},
                "compare_feishu_export": {"type": "boolean", "default": False},
                "profile": {"type": "string", "default": ""},
                "chrome_user_data_dir": {"type": "string", "default": ""},
                "browser_executable_path": {"type": "string", "default": ""},
                "storage_state": {"type": "string", "default": ""},
                "headless": {"type": "boolean", "default": False},
                "check_robots": {"type": "boolean", "default": True},
                "timeout_seconds": {"type": "integer", "default": 30},
                "with_visual_check": {"type": "boolean", "default": True},
                "visual_command": {"type": "string", "default": ""},
                "visual_timeout_seconds": {"type": "integer", "default": 180},
                **CRAWL4AI_SCHEMA,
                **FIRECRAWL_SCHEMA,
                **SCRAPLING_SCHEMA,
            },
            "required": ["url"],
        },
    },
    {
        "name": "rebuild_existing_archive",
        "description": "Run archive rebuild, optional ebook_markdown_pipeline visual recovery, and final rebuild for an existing evidence archive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "archive_path": {"type": "string"},
                "with_visual_check": {"type": "boolean", "default": True},
                "visual_command": {"type": "string", "default": ""},
                "visual_timeout_seconds": {"type": "integer", "default": 180},
            },
            "required": ["archive_path"],
        },
    },
    {
        "name": "rebuild_archive_batch",
        "description": "Run rebuild_existing_archive for archives under an evidence root and return an ok/failed summary.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "archive_dir": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
                "with_visual_check": {"type": "boolean", "default": True},
                "visual_command": {"type": "string", "default": ""},
                "visual_timeout_seconds": {"type": "integer", "default": 180},
                "continue_on_error": {"type": "boolean", "default": True},
            },
            "required": ["archive_dir"],
        },
    },
    {
        "name": "convert_attachment",
        "description": "Convert a local PDF/DOCX/PPTX/etc. attachment to Markdown with MarkItDown.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "input_path": {"type": "string"},
                "output_path": {"type": "string", "default": ""},
            },
            "required": ["input_path"],
        },
    },
    {
        "name": "archivebox_add",
        "description": "Submit URLs to an external ArchiveBox CLI and optionally index the command output in Web Content Fetcher archives.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"}},
                "archivebox_dir": {"type": "string", "default": ""},
                "archive_dir": {"type": "string", "default": ""},
                "executable": {"type": "string", "default": "archivebox"},
                "timeout_seconds": {"type": "integer", "default": 120},
                "extra_args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["urls"],
        },
    },
    {
        "name": "readeck_add",
        "description": "Submit a URL or captured HTML to a self-hosted Readeck instance. The token value is read only from the named local environment variable and is never returned.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "html": {"type": "string", "default": ""},
                "title": {"type": "string", "default": ""},
                "labels": {"type": "array", "items": {"type": "string"}},
                "base_url": {"type": "string", "default": ""},
                "token_env": {"type": "string", "default": "READECK_TOKEN"},
                "archive_dir": {"type": "string", "default": ""},
                "timeout_seconds": {"type": "integer", "default": 30},
                "wait_seconds": {"type": "integer", "default": 0}
            },
            "required": ["url"]
        }
    },
    {
        "name": "browsertrix_archive",
        "description": "Create a local high-fidelity Browsertrix WACZ archive through Docker. Optional profile archives must come from Browsertrix authorized login flow; no credentials are copied or returned.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"}},
                "output_dir": {"type": "string"},
                "collection": {"type": "string", "default": "wcf-capture"},
                "archive_dir": {"type": "string", "default": ""},
                "docker_executable": {"type": "string", "default": "docker"},
                "image": {"type": "string", "default": "webrecorder/browsertrix-crawler"},
                "timeout_seconds": {"type": "integer", "default": 900},
                "profile_path": {"type": "string", "default": ""},
                "copy_artifacts_to_archive": {"type": "boolean", "default": True},
                "extra_args": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["urls", "output_dir"]
        }
    },
    {
        "name": "zsxq_auth_status",
        "description": "Check official zsxq-cli login status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout_seconds": {"type": "integer", "default": 30},
            },
        },
    },
    {
        "name": "zsxq_list_groups",
        "description": "List 知识星球 groups available to the logged-in zsxq-cli account.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "timeout_seconds": {"type": "integer", "default": 30},
                "archive_dir": {"type": "string", "default": ""},
            },
        },
    },
    {
        "name": "zsxq_list_topics",
        "description": "List recent topics in a 知识星球 group through official zsxq-cli.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
                "end_time": {"type": "string", "default": ""},
                "timeout_seconds": {"type": "integer", "default": 30},
                "archive_dir": {"type": "string", "default": ""},
            },
            "required": ["group_id"],
        },
    },
    {
        "name": "zsxq_search_topics",
        "description": "Search topics in a 知识星球 group through official zsxq-cli.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string"},
                "query": {"type": "string"},
                "timeout_seconds": {"type": "integer", "default": 30},
                "archive_dir": {"type": "string", "default": ""},
            },
            "required": ["group_id", "query"],
        },
    },
    {
        "name": "zsxq_get_topic",
        "description": "Get full topic details from 知识星球 through official zsxq-cli.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic_id": {"type": "string"},
                "timeout_seconds": {"type": "integer", "default": 30},
                "archive_dir": {"type": "string", "default": ""},
            },
            "required": ["topic_id"],
        },
    },
    {
        "name": "zsxq_fetch_topic",
        "description": "Search a 知识星球 group and fetch/archive the most relevant topic through official zsxq-cli.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
                "timeout_seconds": {"type": "integer", "default": 30},
                "archive_dir": {"type": "string", "default": ""},
            },
            "required": ["group_id", "query"],
        },
    },
]


def _read_message() -> dict[str, Any] | None:
    header = sys.stdin.buffer.readline()
    if not header:
        return None
    if header.startswith(b"Content-Length:"):
        length = int(header.split(b":", 1)[1].strip())
        # Consume blank line.
        sys.stdin.buffer.readline()
        return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))
    text = header.decode("utf-8").strip()
    return json.loads(text) if text else None


def _write_message(message: dict[str, Any]) -> None:
    raw = json.dumps(message, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw)
    sys.stdout.buffer.flush()


def _content(data: dict[str, Any]) -> list[dict[str, str]]:
    return [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}]


def _fetch(args: dict[str, Any], *, require_archive: bool = False) -> dict[str, Any]:
    selectors = args.get("selectors") or []
    if selectors and isinstance(selectors[0], str):
        selectors = [{"selector": item, "name": item} for item in selectors]
    request = FetchRequest(
        url=args["url"],
        fetch_profile=args.get("fetch_profile") or "generic",
        backend=str(args.get("backend", "auto") or "auto"),
        extractor=str(args.get("extractor", "auto") or "auto"),
        timeout_seconds=int(args.get("timeout_seconds") or 15),
        selectors=selectors,
        use_web_access=bool(args.get("use_web_access", False)),
        prefer_browser=bool(args.get("prefer_browser", False)),
        use_browser=bool(args.get("use_browser", False)),
        browser_profile=str(args.get("profile", "")),
        browser_user_data_dir=str(args.get("chrome_user_data_dir", "")),
        browser_headless=bool(args.get("headless", False)),
        browser_auto_scroll=bool(args.get("auto_scroll", False)),
        browser_scroll_steps=int(args.get("scroll_steps") or 8),
        save_screenshot=bool(args.get("screenshot", False)),
        save_singlefile=bool(args.get("singlefile", False)),
        snapshot_backend=str(args.get("snapshot_backend", "") or ""),
        monolith_executable=str(args.get("monolith_executable", "") or ""),
        monolith_mhtml=bool(args.get("monolith_mhtml", False)),
        archive_dir=args.get("archive_dir") or "",
        download_images=bool(args.get("download_images", False)),
        cache_dir=args.get("cache_dir") or "",
        cache_max_age_seconds=int(args.get("cache_max_age_seconds") or 0),
        crawl4ai_wait_for=str(args.get("crawl4ai_wait_for", "") or ""),
        crawl4ai_css_selector=str(args.get("crawl4ai_css_selector", "") or ""),
        crawl4ai_screenshot=bool(args.get("crawl4ai_screenshot", False)),
        crawl4ai_pdf=bool(args.get("crawl4ai_pdf", False)),
        crawl4ai_cache_mode=str(args.get("crawl4ai_cache_mode", "bypass") or "bypass"),
        firecrawl_api_url=str(args.get("firecrawl_api_url", "") or ""),
        firecrawl_api_key_env=str(args.get("firecrawl_api_key_env", "FIRECRAWL_API_KEY") or "FIRECRAWL_API_KEY"),
        firecrawl_formats=[str(item) for item in (args.get("firecrawl_formats") or ["markdown", "html", "links"])],
        firecrawl_only_main_content=bool(args.get("firecrawl_only_main_content", True)),
        firecrawl_wait_for_ms=int(args.get("firecrawl_wait_for_ms") or 0),
        scrapling_mode=str(args.get("scrapling_mode", "static") or "static"),
        scrapling_headless=bool(args.get("scrapling_headless", True)),
        scrapling_network_idle=bool(args.get("scrapling_network_idle", False)),
        scrapling_wait_ms=int(args.get("scrapling_wait_ms") or 0),
        scrapling_wait_selector=str(args.get("scrapling_wait_selector", "") or ""),
        scrapling_real_chrome=bool(args.get("scrapling_real_chrome", False)),
        scrapling_user_data_dir=str(args.get("scrapling_user_data_dir", "") or ""),
        scrapling_python_path=str(args.get("scrapling_python_path", "") or ""),
        scrapling_python_executable=str(args.get("scrapling_python_executable", "") or ""),
        compare_feishu_export=bool(args.get("compare_feishu_export", False)),
        wechat_api_base_url=str(args.get("wechat_api_base_url", "") or ""),
    )
    if require_archive and not request.archive_dir:
        raise ValueError("archive_dir is required")
    return fetch_content(request).to_dict(include_raw=False)


def _get_archive(args: dict[str, Any]) -> dict[str, Any]:
    from .archive import read_archive_index

    def sample(value: Any, limit: int) -> list[Any]:
        return list(value or [])[:limit] if isinstance(value, list) else []

    data = read_archive_index(str(args.get("archive_path", "")))
    archive_index = data.get("archive_index") or {}
    evidence = data.get("evidence_archive") or data.get("archive") or {}
    files = archive_index.get("files") or []
    readable_path = evidence.get("readable_html_path") or ""
    preferred_markdown = archive_index.get("preferred_markdown") or {}
    preferred_markdown_path = evidence.get("preferred_markdown_path") or preferred_markdown.get("path") or ""
    extracted_path = evidence.get("extracted_text_path") or ""
    structured_path = evidence.get("structured_path") or ""
    browser_visible_path = evidence.get("browser_visible_path") or ""
    browser_visible_html_path = evidence.get("browser_visible_html_path") or ""
    for item in files:
        role = item.get("role")
        if role == "readable_html" and not readable_path:
            readable_path = item.get("path") or ""
        elif role == "preferred_markdown" and not preferred_markdown_path:
            preferred_markdown_path = item.get("path") or ""
        elif role == "extracted_text" and not extracted_path:
            extracted_path = item.get("path") or ""
        elif role == "structured" and not structured_path:
            structured_path = item.get("path") or ""
        elif role == "browser_visible" and not browser_visible_path:
            browser_visible_path = item.get("path") or ""
        elif role == "browser_visible_html" and not browser_visible_html_path:
            browser_visible_html_path = item.get("path") or ""
    markdown_structure = archive_index.get("markdown_structure") or {}
    browser_visible = archive_index.get("browser_visible") or {}
    browser_visible_structure = browser_visible.get("markdown_structure") or {}
    browser_visible_metrics = browser_visible.get("metrics") or {}
    scroll_metrics = browser_visible_metrics.get("scroll") or {}
    preferred_markdown_structure = preferred_markdown.get("structure") or {}
    completeness = archive_index.get("completeness_check") or {}
    content_completeness = archive_index.get("content_completeness") or {}
    asset_completeness = archive_index.get("asset_completeness_check") or {}
    preferred_readable = archive_index.get("preferred_readable") or {}
    preferred_views = archive_index.get("preferred_views") or {}
    primary_views = preferred_views.get("primary") or {}
    primary_human = primary_views.get("human") or {}
    primary_agent = primary_views.get("agent") or {}
    primary_images = primary_views.get("images") or {}
    primary_attachments = primary_views.get("attachments") or {}
    preferred_readable_path = primary_human.get("path") or preferred_readable.get("html_path") or readable_path or browser_visible_html_path
    preferred_agent_path = primary_agent.get("path") or preferred_markdown_path
    review_recommendation = archive_index.get("review_recommendation") or {}
    feishu_comparison = archive_index.get("feishu_comparison") or {}
    extracted_metadata = archive_index.get("extracted_metadata") or data.get("extracted_metadata") or {}
    visible_metadata = extracted_metadata.get("browser_visible_dom") if isinstance(extracted_metadata, dict) else {}
    extraction_quality = archive_index.get("extraction_quality") or data.get("extraction_quality") or {}
    data.setdefault(
        "agent_summary",
        {
            "title": archive_index.get("title") or data.get("title") or "",
            "final_url": archive_index.get("final_url") or data.get("final_url") or "",
            "readable_html_path": readable_path,
            "preferred_markdown_path": preferred_markdown_path,
            "preferred_markdown_source": preferred_markdown.get("source", ""),
            "preferred_markdown_chars": preferred_markdown.get("chars", 0),
            "preferred_markdown_heading_count": preferred_markdown_structure.get("heading_count", 0),
            "preferred_markdown_image_count": preferred_markdown_structure.get("image_count", 0),
            "preferred_markdown_block_count": preferred_markdown_structure.get("block_count", 0),
            "preferred_markdown_outline_sample": sample(preferred_markdown.get("outline"), 10),
            "preferred_markdown_images_sample": sample(preferred_markdown.get("images"), 10),
            "preferred_markdown_blocks_sample": sample(preferred_markdown.get("blocks"), 10),
            "preferred_readable_path": preferred_readable_path,
            "preferred_readable_role": primary_human.get("role") or preferred_readable.get("role", ""),
            "preferred_readable_source": primary_human.get("source") or preferred_readable.get("source", ""),
            "preferred_agent_path": preferred_agent_path,
            "preferred_agent_role": primary_agent.get("role", ""),
            "preferred_images_path": primary_images.get("path", ""),
            "preferred_attachments_path": primary_attachments.get("path", ""),
            "preferred_views": preferred_views,
            "browser_visible_path": browser_visible_path,
            "browser_visible_html_path": browser_visible_html_path,
            "extracted_text_path": extracted_path,
            "structured_path": structured_path,
            "heading_count": markdown_structure.get("heading_count", 0),
            "image_count": markdown_structure.get("image_count", 0),
            "block_count": markdown_structure.get("block_count", 0),
            "browser_visible_block_count": browser_visible.get("block_count", 0),
            "browser_visible_markdown_chars": browser_visible.get("markdown_chars", 0),
            "browser_visible_markdown_block_count": browser_visible_structure.get("block_count", 0),
            "browser_visible_dom_heading_count": browser_visible_structure.get("dom_heading_count", 0),
            "browser_visible_heading_count": browser_visible_structure.get("dom_heading_count") or browser_visible_structure.get("heading_count", 0),
            "browser_visible_image_count": browser_visible_structure.get("image_count", 0),
            "browser_visible_leaf_text_block_count": browser_visible_metrics.get("leaf_text_block_count", 0),
            "browser_scroll_steps_run": scroll_metrics.get("steps_run", 0),
            "browser_scroll_final_height": scroll_metrics.get("final_height", 0),
            "browser_visible_used": bool((visible_metadata or {}).get("used_as_extracted_text")),
            "completeness_status": completeness.get("status", ""),
            "content_completeness_status": content_completeness.get("overall_status", ""),
            "content_text_score": content_completeness.get("text_score", 0),
            "content_image_score": content_completeness.get("image_score", 0),
            "content_table_score": content_completeness.get("table_score", 0),
            "content_structure_score": content_completeness.get("structure_score", 0),
            "content_attachment_score": content_completeness.get("attachment_score", 0),
            "content_completeness_reasons": content_completeness.get("reasons", []),
            "content_recommended_next_action": content_completeness.get("recommended_next_action", ""),
            "completeness_char_ratio": completeness.get("char_ratio", 0),
            "completeness_block_ratio": completeness.get("block_ratio", 0),
            "completeness_reasons": completeness.get("reasons", []),
            "asset_completeness_status": asset_completeness.get("status", ""),
            "asset_completeness_reasons": asset_completeness.get("reasons", []),
            "downloaded_asset_count": asset_completeness.get("downloaded_asset_count", 0),
            "downloaded_only_image_count": asset_completeness.get("downloaded_only_count", 0),
            "browser_visible_only_image_count": asset_completeness.get("browser_visible_only_count", 0),
            "failed_image_asset_count": asset_completeness.get("failed_count", 0),
            "missing_headings": completeness.get("missing_headings", []),
            "missing_images": completeness.get("missing_images", []),
            "review_action": review_recommendation.get("action", ""),
            "review_recommendation": review_recommendation.get("reason", ""),
            "review_path": review_recommendation.get("path", ""),
            "quality_status": extraction_quality.get("status", ""),
            "quality_reasons": extraction_quality.get("reasons", []),
            "quality_recommendation": extraction_quality.get("recommendation", ""),
            "feishu_comparison_status": feishu_comparison.get("status", ""),
            "feishu_comparison_reasons": feishu_comparison.get("reasons", []),
            "feishu_comparison": feishu_comparison,
            "default_browser_profile_dir": str(default_profile_dir()),
            "browser_profile_login_command": f'python -m web_content_fetcher.cli browser-profile login "{data.get("final_url") or archive_index.get("final_url") or "https://my.feishu.cn"}"',
            "extraction_engine": archive_index.get("extraction_engine") or data.get("extraction_engine") or "",
        },
    )
    return data


def _browser_profile_info() -> dict[str, Any]:
    profile_dir = str(default_profile_dir())
    status = browser_profile_status(profile_dir)
    return {
        "default_user_data_dir": profile_dir,
        "profile_status": status,
        "status": status.get("status", ""),
        "has_cookies_db": status.get("has_cookies_db", False),
        "possibly_anonymous": status.get("possibly_anonymous", True),
        "modified_at": status.get("modified_at", ""),
        "login_command": 'python -m web_content_fetcher.cli browser-profile login "https://my.feishu.cn"',
        "login_all_command": "python -m web_content_fetcher.cli browser-profile login-all",
        "check_command": "python -m web_content_fetcher.cli browser-profile check",
        "common_login_urls": COMMON_LOGIN_URLS,
        "fetch_doc_example": "python -m web_content_fetcher.cli fetch-doc <url> --format json",
        "note": "Browser fetches use the tool-owned persistent profile by default when chrome_user_data_dir/storage_state is omitted. Log in manually once; do not copy or bypass credentials.",
    }


def _bridge_doctor(args: dict[str, Any]) -> dict[str, Any]:
    from .bridge_doctor import bridge_doctor

    return bridge_doctor(
        archivebox_executable=str(args.get("archivebox_executable", "archivebox") or "archivebox"),
        visual_command=str(args.get("visual_command", "") or ""),
    )


def _run_quality_smoke(args: dict[str, Any]) -> dict[str, Any]:
    from .quality import QualitySmokeRequest, run_quality_smoke

    data = run_quality_smoke(
        QualitySmokeRequest(
            corpus_path=str(args.get("corpus_path", "") or ""),
            output_root=str(args.get("output_root", "") or ""),
            timeout_seconds=int(args.get("timeout_seconds") or 90),
            backend=str(args.get("backend", "auto") or "auto"),
            extractor=str(args.get("extractor", "auto") or "auto"),
            limit=int(args.get("limit") or 0),
            compare_feishu_export=bool(args.get("compare_feishu_export", False)),
            headless=bool(args.get("headless", False)),
            baseline_path=str(args.get("baseline_path", "") or ""),
        )
    )
    if bool(args.get("make_recovery_pack", False)):
        from .recovery import make_manual_recovery_pack

        data["manual_recovery"] = make_manual_recovery_pack(str(data.get("output_root", "")))
    return data


def _inspect_quality_run(args: dict[str, Any]) -> dict[str, Any]:
    from .quality import inspect_quality_run

    return inspect_quality_run(str(args.get("run_path", "")))


def _make_rerun_plan(args: dict[str, Any]) -> dict[str, Any]:
    from .quality import inspect_quality_run, make_rerun_plan

    data = inspect_quality_run(str(args.get("run_path", "")))
    return make_rerun_plan(data.get("items", []), output_root=str(data.get("output_root", "")))


def _make_manual_recovery_pack(args: dict[str, Any]) -> dict[str, Any]:
    from .recovery import make_manual_recovery_pack

    return make_manual_recovery_pack(
        str(args.get("run_path", "")),
        output_dir=str(args.get("output_dir", "") or ""),
    )


def _list_archives(args: dict[str, Any]) -> dict[str, Any]:
    from .archive import list_archives

    return {"archives": list_archives(str(args.get("archive_dir", "")), limit=int(args.get("limit") or 50))}


def _list_batch_reports(args: dict[str, Any]) -> dict[str, Any]:
    from .batch import list_batch_reports

    return {"batch_reports": list_batch_reports(str(args.get("archive_dir", "")), limit=int(args.get("limit") or 20))}


def _pack_archive(args: dict[str, Any]) -> dict[str, Any]:
    from .archive import pack_archive

    return pack_archive(str(args.get("archive_path", "")), str(args.get("output_path", "")))


def _compare_feishu_export_archive(args: dict[str, Any]) -> dict[str, Any]:
    from .archive import compare_feishu_export_for_archive

    return compare_feishu_export_for_archive(
        str(args.get("archive_path", "")),
        timeout_seconds=int(args.get("timeout_seconds") or 60),
    )


def _rebuild_archive(args: dict[str, Any]) -> dict[str, Any]:
    from .archive_rebuilder import RebuildArchiveRequest, rebuild_archive

    return rebuild_archive(
        RebuildArchiveRequest(
            archive_path=str(args.get("archive_path", "")),
            with_visual_check=bool(args.get("with_visual_check", False)),
            visual_dir=str(args.get("visual_dir", "")),
        )
    )


def _fetch_and_rebuild(args: dict[str, Any]) -> dict[str, Any]:
    from .pipeline import DEFAULT_VISUAL_COMMAND, FetchAndRebuildRequest, fetch_and_rebuild

    return fetch_and_rebuild(
        FetchAndRebuildRequest(
            url=str(args.get("url", "")),
            archive_dir=str(args.get("archive_dir", "")),
            timeout_seconds=int(args.get("timeout_seconds") or 30),
            browser_profile=str(args.get("profile", "")),
            browser_user_data_dir=str(args.get("chrome_user_data_dir", "")),
            browser_executable_path=str(args.get("browser_executable_path", "")),
            browser_storage_state=str(args.get("storage_state", "")),
            headless=bool(args.get("headless", False)),
            check_robots=bool(args.get("check_robots", True)),
            extractor=str(args.get("extractor", "auto") or "auto"),
            backend=str(args.get("backend", "auto") or "auto"),
            crawl4ai_wait_for=str(args.get("crawl4ai_wait_for", "") or ""),
            crawl4ai_css_selector=str(args.get("crawl4ai_css_selector", "") or ""),
            crawl4ai_screenshot=bool(args.get("crawl4ai_screenshot", False)),
            crawl4ai_pdf=bool(args.get("crawl4ai_pdf", False)),
            crawl4ai_cache_mode=str(args.get("crawl4ai_cache_mode", "bypass") or "bypass"),
            firecrawl_api_url=str(args.get("firecrawl_api_url", "") or ""),
            firecrawl_api_key_env=str(args.get("firecrawl_api_key_env", "FIRECRAWL_API_KEY") or "FIRECRAWL_API_KEY"),
            firecrawl_formats=[str(item) for item in (args.get("firecrawl_formats") or ["markdown", "html", "links"])],
            firecrawl_only_main_content=bool(args.get("firecrawl_only_main_content", True)),
            firecrawl_wait_for_ms=int(args.get("firecrawl_wait_for_ms") or 0),
            scrapling_mode=str(args.get("scrapling_mode", "static") or "static"),
            scrapling_headless=bool(args.get("scrapling_headless", True)),
            scrapling_network_idle=bool(args.get("scrapling_network_idle", False)),
            scrapling_wait_ms=int(args.get("scrapling_wait_ms") or 0),
            scrapling_wait_selector=str(args.get("scrapling_wait_selector", "") or ""),
            scrapling_real_chrome=bool(args.get("scrapling_real_chrome", False)),
            scrapling_user_data_dir=str(args.get("scrapling_user_data_dir", "") or ""),
            scrapling_python_path=str(args.get("scrapling_python_path", "") or ""),
            scrapling_python_executable=str(args.get("scrapling_python_executable", "") or ""),
            compare_feishu_export=bool(args.get("compare_feishu_export", False)),
            with_visual_check=bool(args.get("with_visual_check", True)),
            visual_command=str(args.get("visual_command", "") or DEFAULT_VISUAL_COMMAND),
            visual_timeout_seconds=int(args.get("visual_timeout_seconds") or 180),
        )
    )


def _rebuild_existing_archive(args: dict[str, Any]) -> dict[str, Any]:
    from .pipeline import DEFAULT_VISUAL_COMMAND, RebuildExistingArchiveRequest, rebuild_existing_archive

    return rebuild_existing_archive(
        RebuildExistingArchiveRequest(
            archive_path=str(args.get("archive_path", "")),
            with_visual_check=bool(args.get("with_visual_check", True)),
            visual_command=str(args.get("visual_command", "") or DEFAULT_VISUAL_COMMAND),
            visual_timeout_seconds=int(args.get("visual_timeout_seconds") or 180),
        )
    )


def _rebuild_archive_batch(args: dict[str, Any]) -> dict[str, Any]:
    from .pipeline import DEFAULT_VISUAL_COMMAND, RebuildArchiveBatchRequest, rebuild_archive_batch

    return rebuild_archive_batch(
        RebuildArchiveBatchRequest(
            archive_dir=str(args.get("archive_dir", "")),
            limit=int(args.get("limit") or 20),
            with_visual_check=bool(args.get("with_visual_check", True)),
            visual_command=str(args.get("visual_command", "") or DEFAULT_VISUAL_COMMAND),
            visual_timeout_seconds=int(args.get("visual_timeout_seconds") or 180),
            continue_on_error=bool(args.get("continue_on_error", True)),
        )
    )


def _convert_attachment(args: dict[str, Any]) -> dict[str, Any]:
    from .attachments import convert_attachment

    return convert_attachment(str(args.get("input_path", "")), str(args.get("output_path", "")))


def _archivebox_add(args: dict[str, Any]) -> dict[str, Any]:
    from .archivebox_backend import ArchiveBoxRequest, run_archivebox_add

    urls = args.get("urls") or []
    if not isinstance(urls, list) or not urls:
        raise ValueError("urls must be a non-empty array")
    extra_args = args.get("extra_args") or []
    if not isinstance(extra_args, list):
        extra_args = []
    return run_archivebox_add(
        ArchiveBoxRequest(
            urls=[str(url) for url in urls],
            archivebox_dir=str(args.get("archivebox_dir", "")),
            archive_dir=str(args.get("archive_dir", "")),
            executable=str(args.get("executable", "archivebox") or "archivebox"),
            timeout_seconds=int(args.get("timeout_seconds") or 120),
            extra_args=[str(item) for item in extra_args],
        )
    ).to_dict()



def _browsertrix_archive(args: dict[str, Any]) -> dict[str, Any]:
    from .browsertrix_backend import BrowsertrixRequest, run_browsertrix

    urls = args.get("urls") or []
    if not isinstance(urls, list) or not urls:
        raise ValueError("urls must be a non-empty array")
    extra_args = args.get("extra_args") or []
    if not isinstance(extra_args, list):
        extra_args = []
    return run_browsertrix(
        BrowsertrixRequest(
            urls=[str(url) for url in urls],
            output_dir=str(args.get("output_dir", "")),
            collection=str(args.get("collection", "wcf-capture") or "wcf-capture"),
            archive_dir=str(args.get("archive_dir", "") or ""),
            docker_executable=str(args.get("docker_executable", "docker") or "docker"),
            image=str(args.get("image", "webrecorder/browsertrix-crawler") or "webrecorder/browsertrix-crawler"),
            timeout_seconds=int(args.get("timeout_seconds") or 900),
            profile_path=str(args.get("profile_path", "") or ""),
            extra_args=[str(item) for item in extra_args],
            copy_artifacts_to_archive=bool(args.get("copy_artifacts_to_archive", True)),
        )
    ).to_dict()


def _readeck_add(args: dict[str, Any]) -> dict[str, Any]:
    from .readeck_backend import ReadeckRequest, run_readeck

    labels = args.get("labels") or []
    if not isinstance(labels, list):
        labels = []
    return run_readeck(
        ReadeckRequest(
            url=str(args.get("url", "")),
            html=str(args.get("html", "") or ""),
            title=str(args.get("title", "") or ""),
            labels=[str(item) for item in labels],
            base_url=str(args.get("base_url", "") or ""),
            token_env=str(args.get("token_env", "READECK_TOKEN") or "READECK_TOKEN"),
            archive_dir=str(args.get("archive_dir", "") or ""),
            timeout_seconds=int(args.get("timeout_seconds") or 30),
            wait_seconds=int(args.get("wait_seconds") or 0),
        )
    ).to_dict()
def _fetch_document_link(args: dict[str, Any]) -> dict[str, Any]:
    result = fetch_document_link(
        str(args["url"]),
        archive_dir=str(args.get("archive_dir", "")),
        timeout_seconds=int(args.get("timeout_seconds") or 30),
        browser_profile=str(args.get("profile", "")),
        browser_user_data_dir=str(args.get("chrome_user_data_dir", "")),
        headless=bool(args.get("headless", False)),
        extractor=str(args.get("extractor", "auto") or "auto"),
        backend=str(args.get("backend", "auto") or "auto"),
        crawl4ai_wait_for=str(args.get("crawl4ai_wait_for", "") or ""),
        crawl4ai_css_selector=str(args.get("crawl4ai_css_selector", "") or ""),
        crawl4ai_screenshot=bool(args.get("crawl4ai_screenshot", False)),
        crawl4ai_pdf=bool(args.get("crawl4ai_pdf", False)),
        crawl4ai_cache_mode=str(args.get("crawl4ai_cache_mode", "bypass") or "bypass"),
        firecrawl_api_url=str(args.get("firecrawl_api_url", "") or ""),
        firecrawl_api_key_env=str(args.get("firecrawl_api_key_env", "FIRECRAWL_API_KEY") or "FIRECRAWL_API_KEY"),
        firecrawl_formats=[str(item) for item in (args.get("firecrawl_formats") or ["markdown", "html", "links"])],
        firecrawl_only_main_content=bool(args.get("firecrawl_only_main_content", True)),
        firecrawl_wait_for_ms=int(args.get("firecrawl_wait_for_ms") or 0),
        scrapling_mode=str(args.get("scrapling_mode", "static") or "static"),
        scrapling_headless=bool(args.get("scrapling_headless", True)),
        scrapling_network_idle=bool(args.get("scrapling_network_idle", False)),
        scrapling_wait_ms=int(args.get("scrapling_wait_ms") or 0),
        scrapling_wait_selector=str(args.get("scrapling_wait_selector", "") or ""),
        scrapling_real_chrome=bool(args.get("scrapling_real_chrome", False)),
        scrapling_user_data_dir=str(args.get("scrapling_user_data_dir", "") or ""),
        scrapling_python_path=str(args.get("scrapling_python_path", "") or ""),
        scrapling_python_executable=str(args.get("scrapling_python_executable", "") or ""),
        compare_feishu_export=bool(args.get("compare_feishu_export", False)),
        wechat_api_base_url=str(args.get("wechat_api_base_url", "") or ""),
    )
    return result.to_dict(include_raw=False)


def _fetch_batch(args: dict[str, Any]) -> dict[str, Any]:
    from .batch import BatchFetchRequest, fetch_batch

    urls = args.get("urls") or []
    if not isinstance(urls, list) or not urls:
        raise ValueError("urls must be a non-empty array")
    return fetch_batch(
        BatchFetchRequest(
            urls=[str(url) for url in urls],
            archive_dir=str(args.get("archive_dir", "")),
            timeout_seconds=int(args.get("timeout_seconds") or 30),
            backend=str(args.get("backend", "auto") or "auto"),
            extractor=str(args.get("extractor", "auto") or "auto"),
            as_documents=bool(args.get("as_documents", True)),
            browser_profile=str(args.get("profile", "")),
            browser_user_data_dir=str(args.get("chrome_user_data_dir", "")),
            headless=bool(args.get("headless", False)),
            compare_feishu_export=bool(args.get("compare_feishu_export", False)),
            include_results=bool(args.get("include_results", False)),
            wechat_api_base_url=str(args.get("wechat_api_base_url", "") or ""),
        )
    )


def _search_wechat_accounts(args: dict[str, Any]) -> dict[str, Any]:
    from .wechat_download_api import search_wechat_accounts

    accounts = search_wechat_accounts(
        str(args.get("query", "")),
        base_url=str(args.get("wechat_api_base_url", "") or ""),
        timeout_seconds=int(args.get("timeout_seconds") or 30),
    )
    return {"status": "ok" if accounts else "not_found", "accounts": accounts, "count": len(accounts)}


def _search_wechat_articles(args: dict[str, Any]) -> dict[str, Any]:
    from .wechat_download_api import search_wechat_articles

    articles = search_wechat_articles(
        str(args.get("fakeid", "")),
        query=str(args.get("query", "") or ""),
        begin=int(args.get("begin") or 0),
        count=int(args.get("count") or 20),
        base_url=str(args.get("wechat_api_base_url", "") or ""),
        timeout_seconds=int(args.get("timeout_seconds") or 30),
    )
    return {"status": "ok" if articles else "not_found", "articles": articles, "count": len(articles)}


def _search_and_archive_wechat(args: dict[str, Any]) -> dict[str, Any]:
    from .wechat_search import WechatSearchArchiveRequest, search_and_archive_wechat

    return search_and_archive_wechat(
        WechatSearchArchiveRequest(
            account_name=str(args.get("account_name", "")),
            archive_dir=str(args.get("archive_dir", "")),
            provider_path=str(args.get("provider_path", "")),
            discovery_provider=str(args.get("discovery_provider", "auto") or "auto"),
            article_query=str(args.get("article_query", "") or ""),
            wechat_api_base_url=str(args.get("wechat_api_base_url", "") or ""),
            account_limit=int(args.get("account_limit") or 5),
            begin=int(args.get("begin") or 0),
            engine=str(args.get("engine", "sogou") or "sogou"),
            pages=int(args.get("pages") or 2),
            limit=int(args.get("limit") or 20),
            delay_seconds=float(args.get("delay_seconds") or 1.0),
            discovery_timeout_seconds=int(args.get("discovery_timeout_seconds") or 300),
            fetch_articles=bool(args.get("fetch_articles", True)),
            timeout_seconds=int(args.get("timeout_seconds") or 30),
            backend=str(args.get("backend", "auto") or "auto"),
            extractor=str(args.get("extractor", "auto") or "auto"),
            browser_profile=str(args.get("profile", "")),
            browser_user_data_dir=str(args.get("chrome_user_data_dir", "")),
            browser_executable_path=str(args.get("browser_executable", "")),
            browser_storage_state=str(args.get("storage_state", "")),
            headless=bool(args.get("headless", False)),
        )
    )


def _rerun_batch_report(args: dict[str, Any]) -> dict[str, Any]:
    from .batch import rerun_batch_report

    return rerun_batch_report(
        batch_path=str(args.get("batch_path", "")),
        rerun_urls_path=str(args.get("rerun_urls_path", "")),
        archive_dir=str(args.get("archive_dir", "")),
        timeout_seconds=int(args.get("timeout_seconds") or 30),
        backend=str(args.get("backend", "auto") or "auto"),
        extractor=str(args.get("extractor", "auto") or "auto"),
        as_documents=bool(args.get("as_documents", True)),
        browser_profile=str(args.get("profile", "")),
        browser_user_data_dir=str(args.get("chrome_user_data_dir", "")),
        headless=bool(args.get("headless", False)),
        compare_feishu_export=bool(args.get("compare_feishu_export", False)),
        include_results=bool(args.get("include_results", False)),
    )


def _review_batch(args: dict[str, Any]) -> dict[str, Any]:
    from .batch import read_batch_review

    return read_batch_review(str(args.get("batch_path", "")))


def _rerun_incomplete_batch(args: dict[str, Any]) -> dict[str, Any]:
    from .batch import read_batch_review, rerun_batch_report

    strategy = str(args.get("strategy", "") or "")
    if strategy == "manual":
        return {
            "status": "manual_review",
            "review": read_batch_review(str(args.get("batch_path", "") or args.get("rerun_urls_path", ""))),
        }
    strategy_backend = {"browser": "browser", "crawl4ai": "crawl4ai", "feishu_export": "feishu_export"}.get(strategy, "")
    rerun_args = {**args, "backend": strategy_backend or str(args.get("backend", "auto") or "auto")}
    return _rerun_batch_report(rerun_args)


def _zsxq(args: dict[str, Any], action: str) -> dict[str, Any]:
    request = ZsxqRequest(
        action=action,
        group_id=str(args.get("group_id", "")),
        topic_id=str(args.get("topic_id", "")),
        query=str(args.get("query", "")),
        limit=int(args.get("limit") or 20),
        end_time=str(args.get("end_time", "")),
        timeout_seconds=int(args.get("timeout_seconds") or 30),
        archive_dir=str(args.get("archive_dir", "")),
    )
    return run_zsxq(request).to_dict(include_raw=False)


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    msg_id = message.get("id")
    try:
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "web-content-fetcher", "version": "0.1.0"},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        if method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "fetch_url":
                data = _fetch(args)
            elif name == "fetch_url_with_archive":
                data = _fetch(args, require_archive=True)
            elif name == "fetch_with_browser":
                browser_args = {
                    **args,
                    "use_browser": True,
                    "download_images": args.get("download_images", True),
                    "screenshot": args.get("screenshot", True),
                    "auto_scroll": args.get("auto_scroll", False),
                    "scroll_steps": args.get("scroll_steps", 8),
                    "timeout_seconds": args.get("timeout_seconds", 30),
                }
                data = _fetch(browser_args)
            elif name == "fetch_document_link":
                data = _fetch_document_link(args)
            elif name == "browser_profile_info":
                data = _browser_profile_info()
            elif name == "bridge_doctor":
                data = _bridge_doctor(args)
            elif name == "run_quality_smoke":
                data = _run_quality_smoke(args)
            elif name == "inspect_quality_run":
                data = _inspect_quality_run(args)
            elif name == "make_rerun_plan":
                data = _make_rerun_plan(args)
            elif name == "make_manual_recovery_pack":
                data = _make_manual_recovery_pack(args)
            elif name == "fetch_batch":
                data = _fetch_batch(args)
            elif name == "search_wechat_accounts":
                data = _search_wechat_accounts(args)
            elif name == "search_wechat_articles":
                data = _search_wechat_articles(args)
            elif name == "search_and_archive_wechat":
                data = _search_and_archive_wechat(args)
            elif name == "rerun_batch_report":
                data = _rerun_batch_report(args)
            elif name == "review_batch":
                data = _review_batch(args)
            elif name == "rerun_incomplete_batch":
                data = _rerun_incomplete_batch(args)
            elif name == "get_archive":
                data = _get_archive(args)
            elif name == "inspect_archive":
                data = _get_archive(args)
            elif name == "list_archives":
                data = _list_archives(args)
            elif name == "list_batch_reports":
                data = _list_batch_reports(args)
            elif name == "pack_archive":
                data = _pack_archive(args)
            elif name == "compare_feishu_export_archive":
                data = _compare_feishu_export_archive(args)
            elif name == "rebuild_archive":
                data = _rebuild_archive(args)
            elif name == "fetch_and_rebuild":
                data = _fetch_and_rebuild(args)
            elif name == "rebuild_existing_archive":
                data = _rebuild_existing_archive(args)
            elif name == "rebuild_archive_batch":
                data = _rebuild_archive_batch(args)
            elif name == "convert_attachment":
                data = _convert_attachment(args)
            elif name == "archivebox_add":
                data = _archivebox_add(args)
            elif name == "browsertrix_archive":
                data = _browsertrix_archive(args)
            elif name == "readeck_add":
                data = _readeck_add(args)
            elif name == "zsxq_auth_status":
                data = _zsxq(args, "auth_status")
            elif name == "zsxq_list_groups":
                data = _zsxq(args, "list_groups")
            elif name == "zsxq_list_topics":
                data = _zsxq(args, "list_topics")
            elif name == "zsxq_search_topics":
                data = _zsxq(args, "search_topics")
            elif name == "zsxq_get_topic":
                data = _zsxq(args, "get_topic")
            elif name == "zsxq_fetch_topic":
                data = _zsxq(args, "fetch_topic_by_query")
            elif name == "extract_selectors":
                data = _fetch(args)
                data = {
                    "requested_url": data.get("requested_url"),
                    "final_url": data.get("final_url"),
                    "status": data.get("status"),
                    "transport": data.get("transport"),
                    "selector_results": data.get("selector_results"),
                    "errors": data.get("errors"),
                }
            else:
                raise ValueError(f"unknown tool: {name}")
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"content": _content(data)}}
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"method not found: {method}"}}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": f"{type(exc).__name__}: {exc}"}}


def main() -> int:
    while True:
        message = _read_message()
        if message is None:
            return 0
        response = handle(message)
        if response is not None:
            _write_message(response)


if __name__ == "__main__":
    raise SystemExit(main())
