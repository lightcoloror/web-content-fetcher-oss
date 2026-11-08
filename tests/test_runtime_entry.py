import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from web_content_fetcher import runtime_entry as entry


class RuntimeEntryTests(unittest.TestCase):
    def test_explicit_rollback_does_not_read_config(self):
        with patch.dict(os.environ, {'WCF_USE_SYSTEM_PYTHON': '1'}), patch.object(Path, 'is_file', side_effect=AssertionError):
            self.assertIsNone(entry.maybe_reexec('web_content_fetcher.cli', []))

    def test_disabled_config_preserves_entrypoint(self):
        with patch.dict(os.environ, {'WCF_USE_SYSTEM_PYTHON': '0'}), patch.object(Path, 'is_file', return_value=True), patch.object(Path, 'read_text', return_value='{"enabled": false}'):
            self.assertIsNone(entry.maybe_reexec('web_content_fetcher.cli', []))

    def test_invalid_module_rejected(self):
        with self.assertRaises(ValueError):
            entry.maybe_reexec('untrusted.module')

    def check_runtime(self, pip_exit):
        with tempfile.TemporaryDirectory() as tmp:
            python = Path(tmp)/'python.exe'; python.write_bytes(b'synthetic, never executed')
            receipt = Path(tmp)/'validation.json'
            receipt.write_text(json.dumps({'python':str(python),'pip_check_exit_code':pip_exit,'entrypoint_tests_passed':True,'fingerprints':{str(python):hashlib.sha256(python.read_bytes()).hexdigest()}}))
            config=json.dumps({'enabled':True,'python':str(python),'validation_receipt':str(receipt),'validation_sha256':hashlib.sha256(receipt.read_bytes()).hexdigest()})
            with patch.dict(os.environ, {'WCF_USE_SYSTEM_PYTHON':'0','WCF_RUNTIME_REENTRY':'0'}), patch.object(Path,'is_file',return_value=True), patch.object(Path,'read_text',return_value=config), patch.object(entry.subprocess,'call',return_value=17) as call:
                if pip_exit:
                    with self.assertRaises(RuntimeError):entry.maybe_reexec('web_content_fetcher.cli',['--help'])
                    call.assert_not_called()
                else:
                    self.assertEqual(entry.maybe_reexec('web_content_fetcher.cli',['--help']),17)
                    self.assertEqual(call.call_args.args[0],[str(python),'-m','web_content_fetcher.cli','--help'])

    def test_failed_dependency_check_cannot_activate(self):
        self.check_runtime(1)

    def test_validated_runtime_passes_arguments_and_exit(self):
        self.check_runtime(0)


if __name__ == '__main__':
    unittest.main()
