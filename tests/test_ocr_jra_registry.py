import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocr_jra_registry import MAX_PDF_BYTES, download_pdf, extract_pdf_text, ocr_pdf_pages


class FakeResponse:
    def __init__(self, data, headers=None):
        self.data = data
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.data[:size]


class RegistryOcrToolTests(unittest.TestCase):
    def test_download_rejects_non_https_url(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'Only HTTPS URLs'):
                download_pdf('http://www.jra.go.jp/sample.pdf', Path(temp) / 'sample.pdf')

    def test_download_rejects_untrusted_host(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'Only HTTPS URLs'):
                download_pdf('https://example.test/sample.pdf', Path(temp) / 'sample.pdf')

    @patch('ocr_jra_registry.urllib.request.urlopen')
    def test_download_accepts_pdf_signature(self, mocked_open):
        mocked_open.return_value = FakeResponse(b'%PDF-1.7 sample', {'Content-Type': 'application/pdf'})
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'sample.pdf'
            download_pdf('https://www.jra.go.jp/sample.pdf', target)
            self.assertTrue(target.read_bytes().startswith(b'%PDF-'))

    @patch('ocr_jra_registry.urllib.request.urlopen')
    def test_download_rejects_non_pdf_response(self, mocked_open):
        mocked_open.return_value = FakeResponse(b'<html>denied</html>', {'Content-Type': 'text/html'})
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'not a PDF'):
                download_pdf('https://www.jra.go.jp/sample.pdf', Path(temp) / 'sample.pdf')

    @patch('ocr_jra_registry.urllib.request.urlopen')
    def test_download_rejects_excessive_content_length(self, mocked_open):
        mocked_open.return_value = FakeResponse(b'%PDF-1.7', {'Content-Length': str(MAX_PDF_BYTES + 1)})
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'Content-Length'):
                download_pdf('https://www.jra.go.jp/sample.pdf', Path(temp) / 'sample.pdf')

    @patch('ocr_jra_registry.subprocess.run')
    def test_extract_pdf_text_uses_layout_and_requested_pages(self, mocked_run):
        mocked_run.return_value.stdout = '馬名          登録番号'
        result = extract_pdf_text(Path('sample.pdf'), 2, 3)
        self.assertEqual(result, '馬名          登録番号')
        self.assertEqual(mocked_run.call_args.args[0], [
            'pdftotext', '-f', '2', '-l', '3', '-layout', 'sample.pdf', '-'
        ])

    def test_extract_pdf_text_rejects_more_than_five_pages(self):
        with self.assertRaisesRegex(ValueError, 'five pages'):
            extract_pdf_text(Path('unused.pdf'), 1, 6)

    @patch('ocr_jra_registry.subprocess.run')
    def test_extract_pdf_text_rejects_excessive_output(self, mocked_run):
        mocked_run.return_value.stdout = 'x' * (2 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(ValueError, '2 MiB'):
            extract_pdf_text(Path('sample.pdf'), 1, 1)

    def test_ocr_rejects_more_than_five_pages(self):
        with self.assertRaisesRegex(ValueError, 'five pages'):
            ocr_pdf_pages(Path('unused.pdf'), 1, 6)

    def test_ocr_rejects_invalid_page_range(self):
        with self.assertRaisesRegex(ValueError, 'positive and ordered'):
            ocr_pdf_pages(Path('unused.pdf'), 0, 1)


if __name__ == '__main__':
    unittest.main()
