import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('WCF_ATTACHMENT_TESTS') == '1', 'Explicit installed-model synthetic acceptance required')
class AttachmentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = Path(__file__).resolve().parent.parent
        self.config = json.loads((self.repo/'config/local-runtime.json').read_text())
        self.source = self.root/'synthetic.txt'
        self.source.write_text('Synthetic offline evidence, not customer data. \u4e2d\u6587\n' * 70,encoding='utf-8')

    def verify(self, source):
        from web_content_fetcher.attachments import convert_attachment
        result=convert_attachment(source)
        self.assertEqual(result['conversion_runtime']['route'],'private')
        self.assertEqual(result['conversion_runtime']['markitdown'],'0.1.9')
        self.assertEqual(result['conversion_runtime']['detector'],'real_installed_local')
        self.assertEqual(result['sha256'],hashlib.sha256(Path(result['output_path']).read_bytes()).hexdigest())
        self.assertEqual(result['bytes'],Path(result['output_path']).stat().st_size)
        return result

    def test_actual_txt(self):
        result=self.verify(self.source)
        self.assertEqual(Path(result['output_path']).read_text(encoding='utf-8'),self.source.read_text(encoding='utf-8'))

    def test_actual_html(self):
        source=self.root/'synthetic.html'
        source.write_text('<html><body><h1>Synthetic heading</h1><p>Local evidence \u4e2d\u6587.</p></body></html>',encoding='utf-8')
        result=self.verify(source)
        self.assertIn('# Synthetic heading',Path(result['output_path']).read_text(encoding='utf-8'))

    def test_actual_failure_fallback(self):
        from web_content_fetcher.attachment_runtime import run_worker
        from web_content_fetcher.attachments import convert_attachment
        def fail(python,config,payload,root,**kw):
            return run_worker(python,config,{},root,**kw)
        with patch('web_content_fetcher.attachment_runtime.run_worker',side_effect=fail):
            result=convert_attachment(self.source)
        self.assertEqual(result['conversion_runtime']['route'],'builtin_fallback')
        self.assertEqual(result['conversion_runtime']['reason'],'RuntimeError')
        self.assertEqual(Path(result['output_path']).read_text(encoding='utf-8'),self.source.read_text(encoding='utf-8'))

    def test_actual_timeout_fallback(self):
        from web_content_fetcher.attachment_runtime import run_worker
        from web_content_fetcher.attachments import convert_attachment
        def timeout(python,config,payload,root,**kw):
            return run_worker(python,{**config,'timeout_seconds':.05},payload,root,**kw)
        with patch('web_content_fetcher.attachment_runtime.run_worker',side_effect=timeout):
            result=convert_attachment(self.source)
        self.assertEqual(result['conversion_runtime']['reason'],'TimeoutError')
        self.assertEqual(result['conversion_runtime']['route'],'builtin_fallback')

    def test_explicit_rollback_routes_none(self):
        from web_content_fetcher.attachment_runtime import convert_in_private_runtime
        with patch.dict(os.environ,{'WCF_USE_SYSTEM_PYTHON':'1'}):
            self.assertIsNone(convert_in_private_runtime(self.source))

    def test_missing_model_stops(self):
        from web_content_fetcher.attachment_runtime import convert_in_private_runtime
        original=Path.is_file
        def is_file(path):
            return False if path.name=='model.onnx' else original(path)
        with patch.object(Path,'is_file',is_file),patch('web_content_fetcher.attachment_runtime.run_worker') as worker:
            with self.assertRaises(FileNotFoundError):convert_in_private_runtime(self.source)
            worker.assert_not_called()
