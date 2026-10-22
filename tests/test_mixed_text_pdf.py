"""混合文本与扫描页的论文应保留可读页，而不是整篇报错。"""

import unittest
from pathlib import Path

from research_agent.core.models import MaterialRef
from research_agent.rag.materials import DocumentParser


class MixedTextPdfTests(unittest.TestCase):
    def test_scanned_page_does_not_hide_text_pages(self):
        root = Path(__file__).resolve().parents[1]
        path = root / "paper_library" / "pdfs-300" / "2511.18883.pdf"
        if not path.is_file():
            self.skipTest("300-paper local benchmark not installed")
        pages = DocumentParser().pages(MaterialRef(path, path.name, "pdf", "local-test"))
        self.assertTrue(any(page == 1 and len(text) > 100 for page, text, _ in pages))
        self.assertTrue(any(page == 35 and not text.strip() for page, text, _ in pages))


if __name__ == "__main__":
    unittest.main()
