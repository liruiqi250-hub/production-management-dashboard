"""验证便携PDF组件优先级，不读取用户浏览器配置。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.pdf_export import find_pdf_browser


class PDFBrowserTests(unittest.TestCase):
    def test_packaged_component_precedes_system_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundled = root / "vendor/chromium/chrome-headless-shell.exe"
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"synthetic")
            edge = root / "edge.exe"
            edge.write_bytes(b"synthetic")
            with patch("app.pdf_export.EDGE_PATH", edge):
                self.assertEqual(find_pdf_browser(root), bundled)

    def test_source_checkout_can_use_existing_system_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            edge = root / "edge.exe"
            edge.write_bytes(b"synthetic")
            with patch("app.pdf_export.EDGE_PATH", edge), patch.dict("os.environ", {}, clear=True):
                self.assertEqual(find_pdf_browser(root), edge)
                edge.unlink()
                self.assertIsNone(find_pdf_browser(root))


if __name__ == "__main__":
    unittest.main()
