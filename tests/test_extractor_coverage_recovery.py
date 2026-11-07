import unittest
from unittest.mock import patch

from web_content_fetcher.extractors import ExtractionResult, extract_content, html_fragment_to_markdown


class ExtractorCoverageRecoveryTests(unittest.TestCase):
    def test_article_heading_ids_do_not_trigger_noise_heuristics(self):
        first = 'Article evidence explains a substantial engineering problem and its practical consequences. ' * 6
        second = 'Article evidence explains the proposed solution and the limits of the observed results. ' * 6
        raw = '<article><p>' + first + '</p><h2 id="local-toolchain">Local toolchain</h2><h2 id="community-practice">Community practice</h2><p>' + second + '</p></article>'
        with patch('web_content_fetcher.extractors._extract_trafilatura', return_value=ExtractionResult(first+'\n\n'+second, 'trafilatura')), \
             patch('web_content_fetcher.extractors.defuddle_available', return_value=False):
            result = extract_content(raw, url='https://example.test/story')
        self.assertIn('Local toolchain', result.text)
        self.assertIn('Community practice', result.text)

    def test_literal_numbered_paragraph_preserves_its_number(self):
        from web_content_fetcher.completeness import _markdown_text
        result = html_fragment_to_markdown('<p><strong>2. Source-bound findings</strong></p>')
        self.assertIn('2. Source-bound findings', _markdown_text(result))

    def test_coverage_candidate_preserves_short_article_sections(self):
        first = 'A substantial article introduction explains the source material, its scope, and the evidence. ' * 6
        second = 'A substantial article conclusion explains the observations, their limits, and next steps. ' * 6
        raw = '<article><p>' + first + '</p><header><h2>At a glance</h2></header><section><h4>Severity</h4><ul><li>Low</li></ul></section><p>' + second + '</p></article>'
        with patch('web_content_fetcher.extractors._extract_trafilatura', return_value=ExtractionResult(first+'\n\n'+second, 'trafilatura')), \
             patch('web_content_fetcher.extractors.defuddle_available', return_value=False):
            result = extract_content(raw, url='https://example.test/story')
        self.assertIn('At a glance', result.text)
        self.assertIn('Severity', result.text)
        self.assertIn('Low', result.text)

    def test_nested_code_is_not_dropped(self):
        raw = '<article><h2>Task steps</h2><pre><code>first()\nif x &lt; 2:\n    next()</code></pre></article>'
        result = html_fragment_to_markdown(raw)
        self.assertIn('first()\nif x < 2:\n    next()', result)
        self.assertIn('```', result)

    def test_inline_anchor_preserves_source_order(self):
        result = html_fragment_to_markdown('<p>Read <a href="/x">API <em>access</em> first</a>.</p>', base_url='https://example.test')
        self.assertIn('[API access first](https://example.test/x)', result)

    def fixture(self):
        first = 'This saved article has a substantial opening paragraph with source-bound evidence. ' * 4
        second = 'The following paragraph explains how to preserve original content during extraction. ' * 4
        raw = '<article><p>' + first + '</p><h2>Restore this meaningful heading</h2><p>' + second + '</p><pre><code>step_one()\nstep_two()</code></pre></article>'
        primary = first + '\n\n' + second + '\n\n```\nstep_one()\nstep_two()\n```'
        return raw, primary

    def test_auto_recovers_heading_while_preserving_existing_blocks(self):
        raw, primary = self.fixture()
        candidate = html_fragment_to_markdown(raw)
        with patch('web_content_fetcher.extractors._extract_trafilatura', return_value=ExtractionResult(primary, 'trafilatura')), \
             patch('web_content_fetcher.extractors._extract_readability', return_value=ExtractionResult(candidate, 'readability')), \
             patch('web_content_fetcher.extractors.defuddle_available', return_value=False):
            result = extract_content(raw, url='https://example.test/story')
        self.assertEqual(result.engine, 'readability')
        self.assertIn('## Restore this meaningful heading', result.text)
        self.assertIn('step_one()\nstep_two()', result.text)

    def test_candidate_losing_existing_code_is_rejected(self):
        raw, primary = self.fixture()
        candidate = html_fragment_to_markdown(raw).split('```')[0]
        with patch('web_content_fetcher.extractors._extract_trafilatura', return_value=ExtractionResult(primary, 'trafilatura')), \
             patch('web_content_fetcher.extractors._extract_readability', return_value=ExtractionResult(candidate, 'readability')), \
             patch('web_content_fetcher.extractors.defuddle_available', return_value=False):
            result = extract_content(raw, url='https://example.test/story')
        self.assertEqual(result.engine, 'trafilatura')

    def test_explicit_extractor_does_not_trigger_coverage_fallback(self):
        raw, primary = self.fixture()
        with patch('web_content_fetcher.extractors._extract_trafilatura', return_value=ExtractionResult(primary, 'trafilatura')), \
             patch('web_content_fetcher.extractors._extract_readability') as fallback:
            result = extract_content(raw, extractor='trafilatura')
        fallback.assert_not_called()
        self.assertEqual(result.text, primary)


if __name__ == '__main__':
    unittest.main()
