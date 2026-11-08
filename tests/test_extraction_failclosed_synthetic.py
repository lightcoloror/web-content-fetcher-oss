"""Synthetic Stage 2 rework checks: extraction refusal must not fall back or downgrade engines.

STATIC REWORK: prepared but not executed until the Owner approves a permitted
fixture strategy. Mocks are explicit; no runtime/native/network/model work.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from web_content_fetcher import extraction_runtime, extractors
from web_content_fetcher.extraction_runtime import (
    ExtractionRuntimeError,
    ExtractionTimeoutError,
    RuntimeRefused,
    RuntimeValidationError,
)


class FakeLocalResult:
    def __init__(self, text='LOCAL'):
        self.text = text
        self.engine = 'trafilatura'
        self.metadata = {}
        self.quality = {}
        self.errors = []


class ExtractionFailClosedTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)
        patcher = mock.patch.object(extractors, '_extract_trafilatura_local')
        self.local = patcher.start()
        self.local.return_value = FakeLocalResult()
        self.addCleanup(patcher.stop)

    def call(self):
        return extractors._extract_trafilatura('<p>x</p>', url='https://example.test', title='T')

    def test_no_route_result_uses_local_default(self):
        with mock.patch.object(extraction_runtime, 'route', return_value=None):
            result = self.call()
        self.local.assert_called_once()
        self.assertIs(result, self.local.return_value)
        self.assertNotIn('extraction_runtime', result.metadata)

    def test_routed_result_is_used_without_local(self):
        routed = {'text': 'REMOTE', 'engine': 'trafilatura', 'metadata': {'k': 1}, 'quality': {}, 'errors': []}
        with mock.patch.object(extraction_runtime, 'route', return_value=routed):
            result = self.call()
        self.local.assert_not_called()
        self.assertEqual(result.text, 'REMOTE')
        self.assertEqual(result.metadata, {'k': 1})

    def test_permission_refusal_propagates_with_no_fallback(self):
        with mock.patch.object(extraction_runtime, 'route', side_effect=PermissionError('synthetic denial')):
            with self.assertRaises(RuntimeRefused):
                self.call()
        self.local.assert_not_called()

    def test_validation_refusal_propagates_with_no_fallback(self):
        with mock.patch.object(extraction_runtime, 'route', side_effect=RuntimeValidationError('mutated receipt')):
            with self.assertRaises(RuntimeValidationError):
                self.call()
        self.local.assert_not_called()

    def test_unexpected_route_errors_are_refusals_not_fallbacks(self):
        for error in (KeyError('synthetic key'), TypeError('synthetic type'), AttributeError('synthetic attribute'),
                      RuntimeError('untyped runtime error'), TimeoutError('untyped timeout')):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(extraction_runtime, 'route', side_effect=error):
                    with self.assertRaises(RuntimeRefused):
                        self.call()
                self.local.assert_not_called()

    def test_malformed_routed_payload_is_refusal_not_fallback(self):
        with mock.patch.object(extraction_runtime, 'route', return_value={'unexpected': 'payload'}):
            with self.assertRaises(RuntimeRefused):
                self.call()
        self.local.assert_not_called()

    def test_operational_runtimeerror_keeps_explicit_fallback(self):
        with mock.patch.object(extraction_runtime, 'route', side_effect=ExtractionRuntimeError('extraction_runtime_exit:1')):
            result = self.call()
        self.local.assert_called_once()
        self.assertEqual(result.metadata['extraction_runtime'], {'route': 'local_fallback', 'reason': 'RuntimeError'})
        self.assertIn('extraction_runtime:RuntimeError', result.errors)

    def test_operational_timeout_keeps_explicit_fallback(self):
        with mock.patch.object(extraction_runtime, 'route', side_effect=ExtractionTimeoutError('extraction_runtime_timeout')):
            result = self.call()
        self.local.assert_called_once()
        self.assertEqual(result.metadata['extraction_runtime']['reason'], 'TimeoutError')
        self.assertIn('extraction_runtime:TimeoutError', result.errors)

    def test_extract_content_does_not_downgrade_engines_on_refusal(self):
        with mock.patch.object(extractors, '_extract_site_specific', return_value=None), \
             mock.patch.object(extractors, '_run_engine', side_effect=RuntimeRefused('policy refusal')) as engine:
            with self.assertRaises(RuntimeRefused):
                extractors.extract_content('<p>x</p>', url='https://example.test', extractor='auto')
        self.assertEqual(engine.call_count, 1)

    def test_extract_content_still_continues_on_operational_engine_failure(self):
        second = FakeLocalResult('SECOND')
        with mock.patch.object(extractors, '_extract_site_specific', return_value=None), \
             mock.patch.object(extractors, '_run_engine', side_effect=[RuntimeError('synthetic engine failure'), second]), \
             mock.patch.object(extractors, '_is_useful', return_value=True), \
             mock.patch.object(extractors, 'quality_for', return_value={}), \
             mock.patch.object(extractors, '_try_readability_coverage', return_value=None), \
             mock.patch.object(extractors, '_try_defuddle_sidecar', return_value=None):
            result = extractors.extract_content('<p>x</p>', url='https://example.test', extractor='auto')
        self.assertIs(result, second)

    def test_explicit_rollback_skips_route(self):
        root = self.tmp / 'repo'
        (root / 'config').mkdir(parents=True)
        (root / 'web_content_fetcher').mkdir()
        (root / 'config' / 'local-runtime.json').write_text('{"enabled": true, "mode": "extraction"}', encoding='utf-8')
        module_file = str(root / 'web_content_fetcher' / 'extraction_runtime.py')
        with mock.patch.object(extraction_runtime, '__file__', module_file), \
             mock.patch.object(extraction_runtime, 'run_worker', side_effect=AssertionError('must not spawn')), \
             mock.patch.dict(os.environ, {'WCF_USE_SYSTEM_PYTHON': '1'}):
            result = self.call()
        self.local.assert_called_once()
        self.assertNotIn('extraction_runtime', result.metadata)


if __name__ == '__main__':
    unittest.main()
