"""Synthetic Stage 2 rework checks: attachment adoption, hard stops, fallback and refusal.

STATIC REWORK: prepared but not executed until the Owner approves a permitted
fixture strategy. Mocks are explicit; no runtime/native/network/model work.
"""
import hashlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from web_content_fetcher import attachment_runtime, attachments
from web_content_fetcher.extraction_runtime import (
    ExtractionRuntimeError,
    ExtractionTimeoutError,
    RuntimeRefused,
    RuntimeValidationError,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FakeMarkItDown:
    def __init__(self, *args, **kwargs):
        pass

    def convert(self, source):
        return types.SimpleNamespace(text_content='PUBLIC MARKITDOWN TEXT')


def fake_markitdown():
    module = types.ModuleType('markitdown')
    module.MarkItDown = FakeMarkItDown
    return module


class AttachmentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)
        self.root = self.tmp / 'repo'
        (self.root / 'config').mkdir(parents=True)
        (self.root / 'web_content_fetcher').mkdir()
        self.python = self.root / 'interp' / 'python.exe'
        self.python.parent.mkdir()
        self.python.write_bytes(b'synthetic interpreter, never executed')
        self.supervisor = self.root / 'windows_job.py'
        self.supervisor.write_text('class WindowsJob:\n    pass\n', encoding='utf-8')
        self.model = self.root / 'model'
        self.model.mkdir()
        (self.model / 'model.onnx').write_bytes(b'synthetic model bytes, never loaded')
        (self.model / 'config.min.json').write_text('{}', encoding='utf-8')
        self.receipt = self.root / 'validation.json'
        receipt = {
            'python': str(self.python.resolve()),
            'pip_check_exit_code': 0,
            'entrypoint_tests_passed': True,
            'fingerprints': {
                str(self.python.resolve()): _sha(self.python),
                str(self.supervisor.resolve()): _sha(self.supervisor),
            },
        }
        self.receipt.write_text(json.dumps(receipt), encoding='utf-8')
        self.config = {
            'enabled': True,
            'mode': 'extraction',
            'python': str(self.python.resolve()),
            'validation_receipt': str(self.receipt.resolve()),
            'validation_sha256': _sha(self.receipt),
            'process_supervisor': str(self.supervisor.resolve()),
            'attachment_model_directory': str(self.model),
            'attachments_enabled': True,
            'attachment_timeout_seconds': 15,
        }
        self.config_path = self.root / 'config' / 'local-runtime.json'
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        self.source = self.tmp / 'synthetic.txt'
        self.source.write_text('Synthetic offline evidence, not customer data. 中文\n' * 3, encoding='utf-8')
        self.html_source = self.tmp / 'synthetic.html'
        self.html_source.write_text('<html><body><h1>Synthetic</h1><p>Local evidence.</p></body></html>', encoding='utf-8')
        self.module_file = str(self.root / 'web_content_fetcher' / 'attachment_runtime.py')

    def private(self):
        return mock.patch.object(attachment_runtime, '__file__', self.module_file)

    def public_convert(self, source):
        return attachments.convert_attachment(source)

    def test_private_txt_success_record(self):
        private = {'text': 'PRIVATE TEXT', 'conversion_runtime': {'route': 'private', 'detector': 'real_installed_local'}}
        out = self.tmp / 'out.md'
        with self.private(), mock.patch.object(attachment_runtime, 'run_worker', return_value=private) as worker:
            record = attachments.convert_attachment(self.source, out)
        worker.assert_called_once()
        self.assertEqual(out.read_text(encoding='utf-8'), 'PRIVATE TEXT')
        self.assertEqual(record['conversion_runtime'], private['conversion_runtime'])
        self.assertEqual(record['sha256'], hashlib.sha256(out.read_bytes()).hexdigest())
        self.assertEqual(record['bytes'], out.stat().st_size)
        self.assertEqual(record['status'], 'ok')

    def test_private_html_success_record(self):
        private = {'text': '# Synthetic heading', 'conversion_runtime': {'route': 'private'}}
        out = self.tmp / 'out.html.md'
        with self.private(), mock.patch.object(attachment_runtime, 'run_worker', return_value=private) as worker:
            record = attachments.convert_attachment(self.html_source, out)
        worker.assert_called_once()
        self.assertEqual(out.read_text(encoding='utf-8'), '# Synthetic heading')
        self.assertEqual(record['conversion_runtime']['route'], 'private')

    def test_unsupported_suffix_returns_none_and_uses_public_path(self):
        pdf = self.tmp / 'synthetic.pdf'
        pdf.write_bytes(b'%PDF-1.4 synthetic')
        self.assertIsNone(attachment_runtime.convert_in_private_runtime(pdf))
        out = self.tmp / 'synthetic.pdf.md'
        with mock.patch.dict(sys.modules, {'markitdown': fake_markitdown()}):
            record = attachments.convert_attachment(pdf, out)
        self.assertNotIn('conversion_runtime', record)
        self.assertEqual(record['text_chars'], len('PUBLIC MARKITDOWN TEXT'))
        self.assertEqual(out.read_text(encoding='utf-8'), 'PUBLIC MARKITDOWN TEXT')

    def test_public_record_keys_unchanged(self):
        pdf = self.tmp / 'keys.pdf'
        pdf.write_bytes(b'%PDF-1.4 synthetic')
        with mock.patch.dict(sys.modules, {'markitdown': fake_markitdown()}):
            record = attachments.convert_attachment(pdf, self.tmp / 'keys.pdf.md')
        self.assertEqual(set(record), {'input_path', 'output_path', 'bytes', 'sha256', 'text_chars', 'status'})

    def test_public_path_missing_markitdown_raises_original_error(self):
        pdf = self.tmp / 'missing.pdf'
        pdf.write_bytes(b'%PDF-1.4 synthetic')
        with mock.patch.dict(sys.modules, {'markitdown': None}):
            with self.assertRaisesRegex(RuntimeError, 'markitdown is not installed'):
                self.public_convert(pdf)

    def test_rollback_returns_none_without_reading_config(self):
        with self.private(), mock.patch.dict(os.environ, {'WCF_USE_SYSTEM_PYTHON': '1'}), mock.patch.object(attachment_runtime.Path, 'read_text', side_effect=AssertionError('config must not be read')):
            self.assertIsNone(attachment_runtime.convert_in_private_runtime(self.source))
        with self.private(), mock.patch.dict(os.environ, {'WCF_ATTACHMENT_WORKER': '1'}):
            self.assertIsNone(attachment_runtime.convert_in_private_runtime(self.source))

    def test_missing_model_stops_before_worker(self):
        (self.model / 'model.onnx').unlink()
        with self.private(), mock.patch.object(attachment_runtime, 'run_worker', side_effect=AssertionError('must not spawn')) as worker:
            with self.assertRaises(FileNotFoundError):
                attachment_runtime.convert_in_private_runtime(self.source)
        worker.assert_not_called()

    def test_disabled_attachments_returns_none(self):
        self.config['attachments_enabled'] = False
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        with self.private(), mock.patch.object(attachment_runtime, 'run_worker', side_effect=AssertionError('must not spawn')):
            self.assertIsNone(attachment_runtime.convert_in_private_runtime(self.source))

    def test_oversize_attachment_stops_before_worker(self):
        big = self.tmp / 'big.txt'
        with open(big, 'wb') as handle:
            handle.truncate(8 * 1024 * 1024 + 1)
        with self.private(), mock.patch.object(attachment_runtime, 'run_worker', side_effect=AssertionError('must not spawn')) as worker:
            with self.assertRaises(ValueError):
                attachment_runtime.convert_in_private_runtime(big)
        worker.assert_not_called()

    def test_operational_failure_falls_back_for_txt(self):
        out = self.tmp / 'fallback.md'
        with self.private(), mock.patch.object(attachment_runtime, 'run_worker', side_effect=ExtractionRuntimeError('synthetic worker failure')):
            record = attachments.convert_attachment(self.source, out)
        self.assertEqual(record['conversion_runtime']['route'], 'builtin_fallback')
        self.assertEqual(record['conversion_runtime']['reason'], 'RuntimeError')
        self.assertFalse(record['conversion_runtime']['model_executed_in_fallback'])
        self.assertEqual(out.read_text(encoding='utf-8'), self.source.read_text(encoding='utf-8'))

    def test_timeout_falls_back_for_html(self):
        out = self.tmp / 'fallback.md'
        with self.private(), mock.patch.object(attachment_runtime, 'run_worker', side_effect=ExtractionTimeoutError('synthetic timeout')), mock.patch('web_content_fetcher.extractors.html_fragment_to_markdown', return_value='MD FALLBACK'):
            record = attachments.convert_attachment(self.html_source, out)
        self.assertEqual(record['conversion_runtime']['reason'], 'TimeoutError')
        self.assertEqual(record['conversion_runtime']['route'], 'builtin_fallback')
        self.assertEqual(out.read_text(encoding='utf-8'), 'MD FALLBACK')

    def test_untyped_runtimeerror_propagates_with_no_fallback(self):
        out = self.tmp / 'untyped.md'
        with self.private(), mock.patch.object(attachment_runtime, 'run_worker', side_effect=RuntimeError('untyped worker error')), mock.patch.dict(sys.modules, {'markitdown': fake_markitdown()}):
            with self.assertRaises(RuntimeError):
                attachments.convert_attachment(self.source, out)
        self.assertFalse(out.exists())

    def test_untyped_timeout_propagates_with_no_fallback(self):
        out = self.tmp / 'untyped-timeout.md'
        with self.private(), mock.patch.object(attachment_runtime, 'run_worker', side_effect=TimeoutError('untyped timeout')):
            with self.assertRaises(TimeoutError):
                attachments.convert_attachment(self.html_source, out)
        self.assertFalse(out.exists())

    def test_malformed_config_refuses_without_fallback(self):
        self.config_path.write_text('{not json', encoding='utf-8')
        out = self.tmp / 'malformed-config.md'
        with self.private(), mock.patch.dict(sys.modules, {'markitdown': fake_markitdown()}):
            with self.assertRaises(RuntimeValidationError):
                attachments.convert_attachment(self.source, out)
        self.assertFalse(out.exists())

    def test_nonobject_config_refuses_without_fallback(self):
        self.config_path.write_text('[]', encoding='utf-8')
        out = self.tmp / 'nonobject-config.md'
        with self.private():
            with self.assertRaises(RuntimeValidationError):
                attachments.convert_attachment(self.source, out)
        self.assertFalse(out.exists())

    def test_nonobject_receipt_refuses_without_fallback(self):
        self.receipt.write_text('[]', encoding='utf-8')
        self.config['validation_sha256'] = _sha(self.receipt)
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        out = self.tmp / 'nonobject-receipt.md'
        with self.private():
            with self.assertRaises(RuntimeValidationError):
                attachments.convert_attachment(self.source, out)
        self.assertFalse(out.exists())

    def test_missing_model_directory_key_refuses_without_fallback(self):
        self.config.pop('attachment_model_directory', None)
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        out = self.tmp / 'missing-model-key.md'
        with self.private():
            with self.assertRaises(RuntimeValidationError):
                attachments.convert_attachment(self.source, out)
        self.assertFalse(out.exists())

    def test_validation_refusal_propagates_with_no_fallback(self):
        out = self.tmp / 'refused.md'
        with self.private(), mock.patch.object(attachment_runtime, 'run_worker', side_effect=RuntimeValidationError('mutated receipt')):
            with self.assertRaises(RuntimeValidationError):
                attachments.convert_attachment(self.source, out)
        self.assertFalse(out.exists())

    def test_permission_refusal_propagates_with_no_fallback(self):
        out = self.tmp / 'denied.md'
        with self.private(), mock.patch.object(attachment_runtime, 'run_worker', side_effect=PermissionError('synthetic denial')):
            with self.assertRaises(PermissionError):
                attachments.convert_attachment(self.source, out)
        self.assertFalse(out.exists())

    def test_private_error_precedes_public_markitdown(self):
        with self.private(), mock.patch.object(attachment_runtime, 'run_worker', side_effect=RuntimeRefused('policy refusal')), mock.patch.dict(sys.modules, {'markitdown': fake_markitdown()}):
            with self.assertRaises(RuntimeRefused):
                attachments.convert_attachment(self.source, self.tmp / 'no.md')

    def test_cleanup_refusal_propagates_with_no_fallback(self):
        out = self.tmp / 'cleanup-refused.md'
        with self.private(), mock.patch.object(attachment_runtime, 'run_worker', side_effect=RuntimeRefused('Extraction cleanup failed: close:OSError')):
            with self.assertRaises(RuntimeRefused):
                attachments.convert_attachment(self.source, out)
        self.assertFalse(out.exists())

    def test_unknown_oserror_propagates_with_no_fallback(self):
        out = self.tmp / 'oserror.md'
        with self.private(), mock.patch.object(attachment_runtime, 'run_worker', side_effect=OSError('synthetic unknown child failure')), mock.patch.dict(sys.modules, {'markitdown': fake_markitdown()}):
            with self.assertRaises(OSError):
                attachments.convert_attachment(self.source, out)
        self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
