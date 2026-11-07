import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from web_content_fetcher.completeness import inspect_capture
from web_content_fetcher import public_bodies as bodies


class SavedCompletenessRepairTests(unittest.TestCase):
    def inspect(self, html, markdown, **kwargs):
        return inspect_capture(html, markdown, page_url='https://example.test/story', **kwargs)

    def test_semantic_body_excludes_navigation_and_author_bio(self):
        raw = '<main><nav><a href="/">Home</a></nav><section class="post__content"><p>Actual <a href="/a"><strong>linked</strong> text</a>.</p><article class="author-bio"><p>Author metadata</p><a href="/author">About the author</a></article></section><aside><article>Recommended article</article></aside></main>'
        report = self.inspect(raw, 'Actual [**linked** text](/a).')
        self.assertEqual(report['text_status'], 'complete_against_saved_dom')
        self.assertEqual(report['body_scope']['selection'], 'explicit_body_marker')
        self.assertEqual([n['text'] for n in report['dom_links']['nodes']], ['linked text'])
        self.assertTrue(report['body_scope']['excluded_nodes'])
        node = report['dom_links']['nodes'][0]
        self.assertEqual(node['source_line'], 1)
        self.assertEqual(raw[node['source_column']:node['source_column'] + 2], '<a')

    def test_inline_emphasis_links_and_entities(self):
        raw = '<article><p>Read <a href="/a">API <em>access</em></a> &amp; <strong>scope</strong>.</p></article>'
        report = self.inspect(raw, 'Read [API *access*](/a) & **scope**.')
        self.assertFalse(report['text_blocks']['missing'])
        self.assertFalse(report['dom_links']['missing'])

    def test_duplicate_labels_require_each_occurrence(self):
        raw = '<article><p>First <a href="/a">details</a>.</p><p>Second <a href="/b">details</a>.</p></article>'
        report = self.inspect(raw, 'First [details](/a).')
        self.assertEqual(len(report['dom_links']['missing']), 1)
        self.assertEqual(report['dom_links']['missing'][0]['url'], 'https://example.test/b')
        self.assertEqual(len(report['text_blocks']['missing']), 1)

    def test_actual_missing_paragraph_and_duplicate_paragraph_are_partial(self):
        for raw in ('<article><p>Kept paragraph.</p><p>Actually omitted information.</p></article>',
                    '<article><p>Kept paragraph.</p><p>Kept paragraph.</p></article>'):
            with self.subTest(raw=raw):
                report = self.inspect(raw, 'Kept paragraph.')
                self.assertEqual(report['text_status'], 'partial')
                self.assertEqual(len(report['text_blocks']['missing']), 1)

    def test_gfm_tables_are_compared_as_cells_not_pipes(self):
        raw = '<article><table><tr><th>Provider</th><th>Type</th></tr><tr><td>Example</td><td>sample_key</td></tr></table></article>'
        md = '| Provider | Type |\n| --- | --- |\n| Example | `sample_key` |'
        report = self.inspect(raw, md)
        self.assertFalse(report['text_blocks']['missing'])
        missing = self.inspect(raw, md.replace('sample_key', 'different_key'))
        self.assertEqual(missing['text_status'], 'partial')

    def test_text_complete_is_not_image_or_whole_page_complete(self):
        report = self.inspect('<article><p>Retained text.</p><img src="/a.png" data-original="/original.png"></article>', 'Retained text.')
        self.assertEqual(report['text_status'], 'complete_against_saved_dom')
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['body_images']['display_available'], 0)
        self.assertEqual(report['body_images']['original_downloaded'], 0)
        self.assertEqual(report['body_scope']['whole_page_completeness'], 'not_verified')

    def test_truncation_and_missing_scope_remain_unknown(self):
        for raw, kwargs in [('<article><p>Text</p></article>', {'payload': {'truncated': True}}),
                            ('<div>Text</div>', {})]:
            self.assertEqual(self.inspect(raw, 'Text', **kwargs)['text_status'], 'unknown')

    def test_multiple_articles_without_explicit_body_are_not_silently_reduced(self):
        report = self.inspect('<main><article><p>One.</p></article><article><p>Two.</p></article></main>', 'One.')
        self.assertEqual(report['body_scope']['selection'], 'main_conservative_fallback')
        self.assertEqual(report['text_status'], 'partial')

    def test_processing_revision_appends_version_and_keeps_collection_time(self):
        with tempfile.TemporaryDirectory() as directory, patch('socket.socket.connect', side_effect=AssertionError('offline')):
            root = Path(directory)
            html = '<main><p>' + 'Synthetic archived evidence preserves its source and collection time. ' * 15 + '</p></main>'
            source = root / 'source.html'
            source.write_text(html, encoding='utf-8')
            entry = {'id': 'one', 'topic_id': 'sample', 'url': 'https://example.test/a', 'format': 'html',
                'path': source.name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'rights_scope': 'synthetic', 'collected_at': '2026-01-01T00:00:00+00:00', 'published_at': None}
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'schema': 'wcf-body-manifest.v1', 'entries': [entry]}))
            store = root / 'store'
            bodies.import_manifest(manifest, store, root)
            original = bodies._state(store)['records']
            changed = {**bodies.processing_identity(), 'sha256': 'synthetic-next-processing-version'}
            with patch.object(bodies, 'processing_identity', return_value=changed):
                bodies.import_manifest(manifest, store, root)
                frozen = (store / 'index.json').read_bytes()
                bodies.import_manifest(manifest, store, root)
                self.assertEqual(frozen, (store / 'index.json').read_bytes())
            records = bodies._state(store)['records']
            self.assertEqual(len(records), 2)
            self.assertEqual(len(list((store / 'bodies').glob('*.md'))), 1)
            for key, value in original.items():
                self.assertEqual(records[key], value)
            self.assertTrue(all(r['collected_at'] == entry['collected_at'] for r in records.values()))


if __name__ == '__main__':
    unittest.main()
