"""限页候选索引与全文证据索引必须独立缓存。"""

import tempfile
import unittest
from pathlib import Path

from research_agent.core.config import Settings
from research_agent.rag.materials import DocumentIndex


class LimitedIndexCacheTests(unittest.TestCase):
    def test_limited_and_full_index_never_share_cache(self):
        import fitz

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            papers = root / "papers"
            papers.mkdir()
            source = papers / "sample.pdf"
            with fitz.open() as document:
                for number in range(1, 4):
                    page = document.new_page()
                    page.insert_text((40, 40), f"Page {number} evidence about model behavior and experiments.")
                document.save(source)
            settings = Settings(workspace=root, output=root / "output", material_roots=[papers])
            cache = root / "output" / "cache"
            limited = DocumentIndex(settings, cache).build(["sample.pdf"], page_limit=2)
            full = DocumentIndex(settings, cache).build(["sample.pdf"])
            limited_again = DocumentIndex(settings, cache).build(["sample.pdf"], page_limit=2)
            self.assertEqual({chunk.page for chunk in limited}, {1, 2})
            self.assertEqual({chunk.page for chunk in full}, {1, 2, 3})
            self.assertEqual([chunk.sha256 for chunk in limited], [chunk.sha256 for chunk in limited_again])
            self.assertEqual(len(list(cache.glob("*.json"))), 2)


if __name__ == "__main__":
    unittest.main()
