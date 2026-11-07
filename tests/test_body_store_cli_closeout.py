import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from web_content_fetcher import public_bodies
from web_content_fetcher.cli import main


class BodyStoreCLICloseoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / 'store'
        self.source = self.root / 'source.md'
        self.source.write_text('# Synthetic reference\n\n' + 'Public example evidence. ' * 30, encoding='utf-8')
        self.entry = {'id': 'sample', 'topic_id': 'fixture', 'url': 'https://example.test/article',
                      'format': 'markdown', 'path': self.source.name, 'sha256': self.sha(self.source),
                      'rights_scope': 'synthetic', 'content_completeness': 'full',
                      'collected_at': '2026-01-01T00:00:00+00:00'}
        self.manifest = self.root / 'manifest.json'
        self.manifest.write_text(json.dumps({'schema': 'wcf-body-manifest.v1', 'entries': [self.entry]}), encoding='utf-8')
        for target in ('socket.socket.connect', 'socket.getaddrinfo',
                       'web_content_fetcher.public_bodies.direct_fetch',
                       'web_content_fetcher.core.fetch_content'):
            mock = patch(target, side_effect=AssertionError('offline_only')).start()
            self.addCleanup(patch.stopall)
            self.addCleanup(mock.assert_not_called)
        self.call_import()

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def call(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(['body-store', *args]), 0)
        return json.loads(output.getvalue())

    def call_import(self):
        return self.call('import', '--manifest', str(self.manifest), '--input-root', str(self.root), '--store', str(self.store))

    def snapshot(self, root):
        return {p.relative_to(root).as_posix(): self.sha(p) for p in root.rglob('*') if p.is_file()}

    def test_cli_resume_preserves_all_artifacts_without_extraction(self):
        before = self.snapshot(self.store)
        with patch.object(public_bodies, 'structure_content', side_effect=AssertionError('reextraction')) as extract:
            result = self.call_import()
        extract.assert_not_called()
        self.assertEqual(result['references'], 1)
        self.assertEqual(before, self.snapshot(self.store))

    def test_cli_input_drift_is_rejected_without_mutating_existing_store(self):
        before = self.snapshot(self.store)
        self.source.write_text('Deliberately altered isolated test input.', encoding='utf-8')
        with patch.object(public_bodies, 'structure_content', side_effect=AssertionError('reextraction')) as extract:
            with self.assertRaisesRegex(ValueError, 'previous_import_drift:sample'):
                self.call_import()
        extract.assert_not_called()
        self.assertEqual(before, self.snapshot(self.store))

    def test_cli_export_is_immutable_and_preserves_scope(self):
        export = self.root / 'export'
        args = ('export', '--store', str(self.store), '--topic-id', 'fixture', '--output', str(export))
        self.call(*args)
        before = self.snapshot(export)
        with patch.object(public_bodies, 'structure_content', side_effect=AssertionError('reextraction')) as extract:
            self.call(*args)
        extract.assert_not_called()
        self.assertEqual(before, self.snapshot(export))
        item = json.loads((export / 'pid-items.json').read_text(encoding='utf-8'))[0]
        self.assertEqual(item['collected_at'], self.entry['collected_at'])
        self.assertEqual(item['content_completeness_scope'], 'saved_body_text_not_images_or_whole_page')
        self.assertEqual(item['completeness_dimensions']['whole_page'], 'not_verified')
        self.assertFalse(item['fact_verified'])
        (export / 'pid-items.json').write_text('[]', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'artifact_drift:pid-items.json'):
            self.call(*args)


if __name__ == '__main__':
    unittest.main()
