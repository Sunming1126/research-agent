"""长文修订失败时保留首个审查原因，历史记录必须能序列化。"""
import json
from types import SimpleNamespace

from research_agent.core.models import Chunk
from research_agent.integrations.llm import ModelError
from research_agent.rag.retrieval import QueryPlan
from research_agent.workflows.tasks import TaskHandlers


def test_audit_history_preserves_initial_rejection_before_budget_fallback():
    quote = "This system predicts promoter dynamics using a mechanistic model."
    chunk = Chunk("c", "biology.pdf", 1, "Method", quote, "text", "h")

    class Retriever:
        chunks = [chunk]

        def per_document(self, plan, maximum):
            return self.chunks

    class LLM:
        budget, total_tokens = 40000, 0

        def json(self, stage, data, **kwargs):
            if stage == "extract":
                # 模型请求不需要文件哈希、偏移等本地核验字段。
                assert set(data["chunks"][0]) == {"chunk_id", "page", "heading", "text"}
                return {"claims": [{"claim": "论文提出启动子动力学模型。", "chunk_id": "c", "quote": quote}]}
            if stage == "report":
                return {"markdown": ("建议待验证设计，先比较单模块与组合模块的效果。" * 60) + " [1, p.1]",
                        "used_evidence_ids": ["ev-1"]}
            if stage == "audit":
                assert data["source_quotes"][0]["quote"] == quote
                self.total_tokens = 39500
                return {"ok": False, "issues": ["缺少具体实验协议"]}
            if stage == "repair":
                raise ModelError("insufficient model budget for patch repair")
            raise AssertionError(stage)

    case = SimpleNamespace(case_id="E-99", function="Idea", input="设计可证伪的研究",
                           to_dict=lambda: {"case_id": "E-99"})
    handler = TaskHandlers(LLM(), "test")
    handler.report(case, Retriever(), QueryPlan("promoter"))
    serialized = json.dumps(handler.audit, ensure_ascii=False)
    assert "缺少具体实验协议" in serialized
    assert "insufficient model budget" in serialized
