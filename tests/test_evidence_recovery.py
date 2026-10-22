"""指定论文初次无事实时，从其本身的摘要与正文恢复证据。"""

import unittest
from types import SimpleNamespace

from research_agent.contracts.validation import validate_result
from research_agent.core.models import Chunk
from research_agent.workflows.tasks import (fallback_evidence_chunks, requested_paper_count,
                                            resolve_quote_source, validate_citations, TaskHandlers)


class EvidenceRecoveryTests(unittest.TestCase):
    def test_fallback_is_limited_to_missing_authorized_paper(self):
        chunks = [Chunk("title", "A.pdf", 1, "Title", "Short title", "text", "t", 0),
                  Chunk("abstract", "A.pdf", 1, "Abstract", "A mechanistic biological model. " * 12,
                        "text", "a", 100),
                  Chunk("body", "A.pdf", 2, "Method", "The proposed framework handles signal hierarchy. " * 10,
                        "text", "b", 1000),
                  Chunk("other", "B.pdf", 1, "Abstract", "Unrelated paper. " * 20,
                        "text", "o", 0)]
        result = fallback_evidence_chunks(chunks, "A.pdf", maximum=3)
        self.assertEqual({row.chunk_id for row in result}, {"abstract", "body"})

    def test_short_pdf_lines_remain_available_for_evidence(self):
        chunks = [Chunk(str(index), "A.pdf", 2, "Abstract", "Tritium adsorption varies by material. " * 3,
                        "text", str(index), index * 100) for index in range(10)]
        result = fallback_evidence_chunks(chunks, "A.pdf")
        self.assertEqual(len(result), 10)

    def test_total_paper_count_overrides_per_topic_count(self):
        self.assertEqual(requested_paper_count("每个方向各找一篇，合计三篇论文"), 3)
        self.assertEqual(requested_paper_count("找五篇相关论文"), 5)

    def test_crystal_orientation_is_not_a_citation(self):
        evidence = [{"evidence_id": "ev-1", "ref": 1, "page": 3}]
        markdown = "沿[111]晶向测量的结果已由原文核验 [1, p.3]。" * 40
        validate_citations(markdown, evidence, ["ev-1"])
        result = {"case_id": "E-01", "path": "data/expert/E-01-灵感Idea报告-team.md",
                  "markdown": markdown + "\n\n## 参考文献\n\n[1] A.pdf"}
        validate_result(SimpleNamespace(case_id="E-01", function="Idea"), result,
                        team="team", filenames=["A.pdf"])

    def test_cross_line_quote_stays_on_same_pdf_page(self):
        first = Chunk("a", "A.pdf", 2, "Abstract", "The measured coherent signal was strongly",
                      "text", "a", 0)
        second = Chunk("b", "A.pdf", 2, "Abstract", "enhanced after applying uniaxial stress.",
                       "text", "b", 43)
        quote = "coherent signal was strongly enhanced after applying uniaxial stress"
        merged = resolve_quote_source(first, quote, [first, second])
        self.assertIsNotNone(merged)
        self.assertEqual(merged.page, 2)
        self.assertEqual(merged.filename, "A.pdf")
        self.assertIsNone(resolve_quote_source(first, quote, [first,
            Chunk("c", "A.pdf", 3, "Results", second.text, "text", "c", 43)]))

    def test_source_internal_reference_is_not_output_citation(self):
        class FakeLLM:
            def json(self, *args, **kwargs):
                return {"claims": [{"chunk_id": "a", "quote": "the method described in [14] was used",
                                    "claim": "研究采用[14]的方法。"}]}

        chunk = Chunk("a", "A.pdf", 1, "Abstract", "the method described in [14] was used",
                      "text", "a", 0)
        claims = TaskHandlers(FakeLLM(), "team").extract(SimpleNamespace(input="比较方法"), [chunk])
        self.assertEqual(claims[0].claim, "研究采用文内所引的方法。")


if __name__ == "__main__":
    unittest.main()
