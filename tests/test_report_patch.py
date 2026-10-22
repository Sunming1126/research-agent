"""局部修订必须绑定当前段落，并对歧义修改采取拒绝策略。"""
import pytest
import json
from types import SimpleNamespace

from research_agent.core.models import Chunk
from research_agent.rag.retrieval import QueryPlan

from research_agent.workflows.tasks import EvidenceError, apply_report_patches, repair_paragraphs
from research_agent.workflows.tasks import TaskHandlers


def test_patch_changes_only_current_exact_passage():
    original = "本篇使用固定方式进行融合，这是过度概括。[1, p.2]"
    kept = "其他已通过审查的事实保持原样。[2, p.3]"
    draft = original + "\n\n" + kept
    replacement = "给定证据描述两种融合方式，尚不能合并归类。[1, p.2]"
    assert repair_paragraphs(draft, [f"正文称“{original}”存在归类错误"]) == [original]
    revised = apply_report_patches(draft, [{"original": original, "replacement": replacement}])
    assert revised == replacement + "\n\n" + kept


def test_patch_rejects_unknown_or_ambiguous_original():
    original = "这是已经出现两次的原句。"
    with pytest.raises(EvidenceError):
        apply_report_patches(original * 2, [{"original": original, "replacement": "新文本"}])
    with pytest.raises(EvidenceError):
        apply_report_patches(original, [{"original": "这是不存在于当前答案的原句。", "replacement": "新文本"}])


def test_second_audit_reviews_only_exact_changes_and_keeps_valid_report():
    quote = "The system supports two independently validated operating modes."
    chunk = Chunk("c", "unseen.pdf", 1, "Method", quote, "text", "h")
    original = "该系统总是只能使用一种运行方式。[1, p.1]"
    corrected = "该系统支持两种经过独立验证的运行方式。[1, p.1]"

    class Retriever:
        chunks = [chunk]

        def per_document(self, plan, maximum):
            return self.chunks

    class LLM:
        audits = 0

        def json(self, stage, data, **kwargs):
            if stage == "extract":
                return {"claims": [{"chunk_id": "c", "quote": quote, "claim": "系统支持两种经过独立验证的运行方式。"}]}
            if stage == "report":
                return {"markdown": ("建议待验证研究方案，比较两种机制并固定预算与数据划分。" * 45) + "\n\n" + original,
                        "used_evidence_ids": ["ev-1"]}
            if stage == "repair":
                assert data["paragraphs"] == [original]
                return {"patches": [{"original": original, "replacement": corrected}]}
            if stage == "audit":
                self.audits += 1
                if self.audits == 1:
                    return {"ok": False, "issues": [f"正文称“{original}”与原文矛盾"]}
                assert data["markdown"] == corrected
                assert corrected in data["full_document"]
                assert "固定预算与数据划分" in data["full_document"]
                assert data["previous_issues"] and data["applied_patches"]
                return {"ok": True, "issues": []}
            raise AssertionError(stage)

    handler = TaskHandlers(LLM(), "test")
    case = SimpleNamespace(case_id="E-99", function="Idea", input="比较机制",
                           to_dict=lambda: {"case_id": "E-99"})
    result = handler.report(case, Retriever(), QueryPlan("system"))
    assert corrected in result["markdown"] and original not in result["markdown"]
    assert handler.audit["ok"] is True
    assert len(handler.audit["history"]) == 3
    json.dumps(handler.audit)
