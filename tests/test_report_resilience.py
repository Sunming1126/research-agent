"""报告失败保留证据；E 类不能再用程序拼接模板伪装成功。"""

import unittest
from types import SimpleNamespace

from research_agent.core.models import Chunk
from research_agent.integrations.llm import ModelError
from research_agent.rag.retrieval import QueryPlan
from research_agent.workflows.tasks import TaskHandlers
from research_agent.evidence import EvidenceError


class ReportResilienceTests(unittest.TestCase):
    def test_idea_model_budget_failure_keeps_verified_evidence(self):
        quote = "We present a model of promoter dynamics."
        chunk = Chunk("c1", "A.pdf", 1, "Abstract", quote + " The experiment compares trajectories.", "text", "h")

        class Retriever:
            chunks = [chunk]

            def per_document(self, plan, maximum):
                return self.chunks

        class LLM:
            def json(self, stage, data, **kwargs):
                if stage == "extract":
                    return {"claims": [{"claim": "论文提出启动子动力学模型。", "chunk_id": "c1", "quote": quote}]}
                raise ModelError("budget exhausted")

        case = SimpleNamespace(case_id="E-99", function="Idea", input="研究启动子反馈的模拟观察",
                               to_dict=lambda: {"case_id": "E-99"})
        handler = TaskHandlers(LLM(), "demo")
        with self.assertRaisesRegex(EvidenceError, "Idea model report failed"):
            handler.report(case, Retriever(), QueryPlan("promoter"))
        self.assertEqual(handler.ledger[0].claim, "论文提出启动子动力学模型。")
        self.assertEqual(handler.audit["mode"], "model_report_failed")

    def test_survey_recovers_one_missing_paper_before_fallback(self):
        quote = "We propose a mechanistic framework for this system."
        chunks = [Chunk(f"c{i}", f"P{i}.pdf", 1, "Abstract", quote + " Additional detail. " * 18,
                        "text", f"h{i}") for i in range(5)]

        class Retriever:
            def __init__(self):
                self.chunks = chunks

            def per_document(self, plan, maximum):
                return self.chunks

        class LLM:
            calls_for_last = 0

            def json(self, stage, data, **kwargs):
                if stage == "extract":
                    filename = data["filename"]
                    if filename == "P4.pdf":
                        self.calls_for_last += 1
                        if self.calls_for_last == 1:
                            return {"claims": []}
                    index = int(filename[1])
                    return {"claims": [{"claim": f"论文 {index} 提出可检验的机制。",
                                        "chunk_id": f"c{index}", "quote": quote}]}
                raise ModelError("budget exhausted")

        case = SimpleNamespace(case_id="C-99", function="Survey", input="比较五篇论文",
                               to_dict=lambda: {"case_id": "C-99"})
        result = TaskHandlers(LLM(), "demo").report(case, Retriever(), QueryPlan("framework"))
        self.assertIn("[5, p.1]", result["markdown"])
        self.assertEqual(result["markdown"].count(".pdf"), 10)


if __name__ == "__main__":
    unittest.main()
