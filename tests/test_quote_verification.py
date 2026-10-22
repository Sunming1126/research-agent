"""PDF 排版差异不应抛弃真实引文，也不能放行改写。"""

import unittest

from research_agent.core.utils import source_quote_matches


class QuoteVerificationTests(unittest.TestCase):
    def test_pdf_line_breaks_ligatures_and_hyphens_are_normalized(self):
        source = "We obtain state-of-the-\nart results on the Natural Questions and TriviaQA open benchmarks. The ﬁnal score improves."
        self.assertTrue(source_quote_matches(
            "We obtain state-of-the-art results on the Natural Questions and TriviaQA open benchmarks.", source))
        self.assertTrue(source_quote_matches("The final score improves.", source))

    def test_paraphrase_or_spliced_fragments_are_rejected(self):
        source = "We train a dense retriever. We then evaluate it on questions."
        self.assertFalse(source_quote_matches("We train a sparse retriever.", source))
        self.assertFalse(source_quote_matches("We train a dense retriever. We evaluate it on questions.", source))


if __name__ == "__main__":
    unittest.main()
