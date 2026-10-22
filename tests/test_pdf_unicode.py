"""异常 PDF 字形不能使合法的文档索引中断。"""

import unittest

from research_agent.rag.materials import normalize_extracted_text


class PDFUnicodeTests(unittest.TestCase):
    def test_unpaired_surrogate_is_replaced_before_hashing_and_json(self):
        text = "数学公式 A" + chr(0xD835) + "B 的上下文"
        safe = normalize_extracted_text(text)
        self.assertEqual(safe, "数学公式 A\ufffdB 的上下文")
        safe.encode("utf-8")


if __name__ == "__main__":
    unittest.main()
