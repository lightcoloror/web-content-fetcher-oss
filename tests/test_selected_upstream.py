"""Opt-in synthetic upstream regression; never starts a detector model."""
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(os.environ.get('WCF_SELECTED_UPSTREAM_TESTS') == '1',
                     'Explicit selected-upstream environment required')
class SelectedUpstreamTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'WCF_USE_SYSTEM_PYTHON': '1'})
        env.start()
        self.addCleanup(env.stop)

    def test_trafilatura_body_and_source(self):
        from web_content_fetcher.extractors import _extract_trafilatura
        body = ('A synthetic source explains evidence retention and preserves the original '
                'article wording while separating observations from interpretations. ')
        html = ('<html><head><title>Synthetic source</title></head><body><article>'
                '<h1>Synthetic source</h1><p>' + body + '</p><p>'
                'The second section retains a distinct closing observation for review.'
                '</p></article></body></html>')
        result = _extract_trafilatura(html, url='https://example.test/evidence', title='Synthetic source')
        self.assertIn(body.strip(), result.text)
        self.assertEqual(result.text.count(body.strip()), 1)
        self.assertIn('distinct closing observation', result.text)
        self.assertEqual(result.engine, 'trafilatura')
        self.assertEqual(result.metadata.get('source'), 'https://example.test/evidence')

    def test_trafilatura_inline_code_does_not_duplicate_tail(self):
        from web_content_fetcher.extractors import _extract_trafilatura
        tail = 'This uniquely marked continuation must occur exactly once in the body.'
        html = '<article><h1>Code evidence</h1><p>Use <code>retain()</code> ' + tail + '</p><p>' + ('Evidence is preserved with source attribution. ' * 8) + '</p></article>'
        result = _extract_trafilatura(html, url='https://example.test/code', title='Code evidence')
        self.assertEqual(result.text.count(tail), 1)
        self.assertIn('retain()', result.text)

    def converter(self):
        from markitdown import MarkItDown
        from markitdown.converters import PlainTextConverter
        with patch('markitdown._markitdown.magika.Magika') as detector:
            converter = MarkItDown(enable_builtins=False, enable_plugins=False)
            detector.assert_called_once()
        converter.register_converter(PlainTextConverter())
        return converter

    def test_nonseekable_stream_reads_all_chunks(self):
        from markitdown import StreamInfo
        class Stream(io.BytesIO):
            def seekable(self):
                return False
            def read(self, size=-1):
                return super().read(min(size, 17) if size >= 0 else 17)
        text = 'synthetic chunk preservation ' * 400 + '\u4e2d\u6587 end'
        converter = self.converter()
        info = StreamInfo(extension='.txt', mimetype='text/plain', charset='utf-8')
        with patch.object(converter, '_get_stream_info_guesses', return_value=[info]):
            result = converter.convert_stream(Stream(text.encode()), stream_info=info)
        self.assertEqual(result.text_content, text)

    def test_utf8_sample_completes_boundary_character(self):
        from markitdown._markitdown import _read_charset_sample
        raw = b'a' * 65535 + '\u4e2d'.encode() + b'end'
        sample = _read_charset_sample(io.BytesIO(raw))
        self.assertEqual(sample.decode(), 'a' * 65535 + '\u4e2d')

    def test_response_stream_uses_64k_chunks(self):
        from markitdown import StreamInfo
        converter = self.converter()
        info = StreamInfo(extension='.txt', mimetype='text/plain', charset='utf-8')
        response = Mock()
        response.url = 'https://example.test/synthetic.txt'
        response.headers = {'Content-Type': 'text/plain; charset=utf-8'}
        response.iter_content.return_value = [b'first ', b'last']
        with patch.object(converter, '_get_stream_info_guesses', return_value=[info]):
            result = converter.convert_response(response)
        self.assertEqual(result.text_content, 'first last')
        response.iter_content.assert_called_once_with(chunk_size=65536)

    def test_late_nonascii_plain_text_preserved(self):
        from markitdown import StreamInfo
        from markitdown.converters import PlainTextConverter
        text = '{"content":"' + 'a' * 12000 + '\u4e2d\u6587\u03b1"}'
        result = PlainTextConverter().convert(io.BytesIO(text.encode()), StreamInfo(extension='.json'))
        self.assertEqual(result.text_content, text)

    def test_wcf_attachment_digest_and_utf8(self):
        from markitdown import StreamInfo
        from web_content_fetcher.attachments import convert_attachment
        converter = self.converter()
        info = StreamInfo(extension='.txt', mimetype='text/plain', charset='utf-8')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'synthetic.txt'
            source.write_text('Synthetic evidence \u4e2d\u6587\nSource: example.test', encoding='utf-8')
            with patch('markitdown.MarkItDown', return_value=converter), patch.object(converter, '_get_stream_info_guesses', return_value=[info]):
                result = convert_attachment(source)
            output = Path(result['output_path'])
            self.assertEqual(output.read_text(encoding='utf-8'), source.read_text(encoding='utf-8'))
            self.assertEqual(result['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(result['input_path'], str(source))
            self.assertEqual(result['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
