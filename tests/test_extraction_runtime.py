"""Opt-in actual offline subprocess routing; no browser or model execution."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, AsyncMock


@unittest.skipUnless(os.environ.get('WCF_ROUTING_TESTS') == '1', 'Actual local runtime test requires opt-in')
class ExtractionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent
        self.config = json.loads((self.root/'config/local-runtime.json').read_text())
        self.html = '<article><h1>Synthetic route</h1><p>' + ('This synthetic source retains original evidence and source attribution. ' * 5) + '</p></article>'

    def test_actual_success(self):
        from web_content_fetcher.extractors import _extract_trafilatura
        result = _extract_trafilatura(self.html, url='https://example.test/source', title='Synthetic route')
        route = result.metadata['extraction_runtime']
        self.assertEqual(route['route'], 'private')
        self.assertEqual(route['trafilatura'], '2.3.1')
        self.assertEqual(Path(route['python']).resolve(), Path(self.config['python']).resolve())
        self.assertIn('original evidence', result.text)

    def test_actual_failure_and_fallback(self):
        from web_content_fetcher.extraction_runtime import run_worker
        from web_content_fetcher.extractors import _extract_trafilatura
        def fail(*args):
            return run_worker(Path(self.config['python']), self.config, {}, self.root)
        with patch('web_content_fetcher.extraction_runtime.route', side_effect=fail):
            result = _extract_trafilatura(self.html, url='https://example.test/source', title='Synthetic route')
        self.assertEqual(result.metadata['extraction_runtime']['route'], 'local_fallback')
        self.assertEqual(result.metadata['extraction_runtime']['reason'], 'RuntimeError')
        self.assertIn('original evidence', result.text)

    def test_actual_timeout_and_fallback(self):
        from web_content_fetcher.extraction_runtime import run_worker
        from web_content_fetcher.extractors import _extract_trafilatura
        config = {**self.config, 'timeout_seconds': .05}
        def timeout(*args):
            return run_worker(Path(config['python']), config, {'html': self.html, 'url':'', 'title':''}, self.root)
        with patch('web_content_fetcher.extraction_runtime.route', side_effect=timeout):
            result = _extract_trafilatura(self.html, url='https://example.test/source', title='Synthetic route')
        self.assertEqual(result.metadata['extraction_runtime']['reason'], 'TimeoutError')
        self.assertIn('original evidence', result.text)

    def test_explicit_rollback(self):
        from web_content_fetcher.extractors import _extract_trafilatura
        with patch.dict(os.environ, {'WCF_USE_SYSTEM_PYTHON':'1'}):
            result = _extract_trafilatura(self.html, url='', title='')
        self.assertNotIn('extraction_runtime', result.metadata)

    def test_entrypoint_stays_in_caller(self):
        from web_content_fetcher.runtime_entry import maybe_reexec
        for module in ['web_content_fetcher.cli','web_content_fetcher.ui','web_content_fetcher.mcp_server']:
            self.assertIsNone(maybe_reexec(module, []))

    def test_legacy_crawl_backend_retained(self):
        from web_content_fetcher.core import FetchRequest, crawl4ai_fetch
        import importlib.metadata as md
        self.assertNotEqual(Path(sys.executable).resolve(), Path(self.config['python']).resolve())
        self.assertTrue(md.version('playwright'))
        self.assertTrue(md.version('crawl4ai'))
        def run_fixture(coroutine):
            try:
                coroutine.send(None)
            except StopIteration as completed:
                return completed.value
            raise AssertionError('Synthetic fixture unexpectedly suspended')
        with patch('web_content_fetcher.core.run_crawl4ai', new=AsyncMock(return_value={'success':True,'html':self.html})) as backend, patch('web_content_fetcher.core.asyncio.run', side_effect=run_fixture):
            result = crawl4ai_fetch(FetchRequest(url='https://example.test/source'))
        backend.assert_awaited_once()
        self.assertEqual(result[3], 'crawl4ai')


if __name__ == '__main__':
    unittest.main()
