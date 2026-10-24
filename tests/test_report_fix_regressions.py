"""覆盖真实失分路径：局部复审、遗漏变体、部分支持与实验条件。"""
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch
import json

from research_agent.core.models import Chunk, Evidence
from research_agent.evidence.ledger import select_claims, verify_claims
from research_agent.evidence.reporting import focused_excerpt, report_requirements
from research_agent.evidence.reporting import validate_citations
from research_agent.evidence import EvidenceError
import pytest
from research_agent.integrations.llm import GatewayLLM, ModelError, ModelQuotaError
from research_agent.workflows.report_context import audit_request, repair_tail_budget
from research_agent.workflows.tasks import TaskHandlers
from research_agent.rag.retrieval import HybridRetriever, QueryPlan
from research_agent.evidence.reporting import report_evidence_chunks
from research_agent.evidence.reporting import survey_core_facts


def test_patch_audit_retains_local_conditions_without_repeating_unrelated_sections():
    before = "控制组与处理组固定相同的数据划分和预算。"
    old = "该方法使用一种候选状态进行计算。[1, p.2]"
    new = "该方法分别计算两种候选状态。[1, p.2]"
    unrelated = "其他方向的完整长段落无需重复发送。" * 300
    markdown = "## 实验条件\n\n" + before + "\n\n" + new + "\n\n## 其他方向\n\n" + unrelated
    evidence = Evidence("ev-1", "两种候选状态", "unseen.pdf", 2, "c", "The model evaluates two alternative states.")
    rows = [{**asdict(evidence), "ref": 1}]
    data = audit_request(SimpleNamespace(function="Survey", input="比较状态"), markdown, rows, [evidence],
                         patches=[{"original": old, "replacement": new}], feedback=["一种状态的说法错误"])
    assert new in data["full_document"] and before in data["full_document"]
    assert unrelated not in data["full_document"] and "## 其他方向" in data["full_document"]
    assert data["source_quotes"][0]["claim"] == evidence.claim
    assert data["source_quotes"][0]["quote"] == evidence.quote
    assert data["document_view"] == "changed_paragraphs_and_neighbors"


def test_partial_support_cannot_certify_full_claim_even_if_verdict_is_inconsistent():
    evidence = Evidence("ev-1", "该模型在全部条件下都有效。", "unseen.pdf", 2, "c", "The result holds under the tested conditions.")
    gateway = GatewayLLM()
    with patch.object(gateway, "json", return_value={"claims": [
            {"evidence_id": "ev-1", "verdict": "supported", "reason": "只构成部分支持"}]}):
        ledger, _ = verify_claims(gateway, [evidence], [])
    assert ledger[0].verification == "verbatim_only"


def test_late_definition_is_kept_as_a_continuous_source_window():
    text = "General background for the study. " * 30 + "We introduce two variants of the catalytic state transition mechanism."
    chunk = Chunk("c", "unseen.pdf", 8, "Mechanism", text, "text", "h")
    excerpt = focused_excerpt(chunk, "catalytic state transition variants", 200)
    assert "two variants" in excerpt and excerpt in text


def test_new_targeted_method_fact_is_not_always_displaced_by_old_method_rows():
    ledger = [Evidence(str(i), "generic model", "a.pdf", 1, "c", "generic model for a system", facet="method") for i in range(5)]
    ledger += [Evidence("new", "alternate catalytic states", "a.pdf", 3, "d", "The catalytic mechanism has alternate states.", facet="method")]
    selected = select_claims(ledger, maximum_per_paper=2, question="catalytic states")
    assert "new" in {e.evidence_id for e in selected}


def test_repair_reservation_depends_on_the_actual_document_size():
    gateway = GatewayLLM()
    evidence = Evidence("ev-1", "有条件结果", "a.pdf", 1, "c", "The result holds under tested conditions.")
    rows = [{**asdict(evidence), "ref": 1}]
    case = SimpleNamespace(function="Survey", input="比较条件")
    short = "有条件的结论。[1, p.1]"
    long = "有条件的结论。" * 350 + "[1, p.1]"
    assert repair_tail_budget(gateway, case, long, rows, [evidence]) > repair_tail_budget(gateway, case, short, rows, [evidence])


def test_idea_requires_shared_concrete_experiment_conditions():
    requirements = " ".join(report_requirements("Idea"))
    assert "随机种子" in requirements and "候选配置" in requirements
    assert "对照与消融共用" in requirements and "软件版本" in requirements
    assert "复现瓶颈" in requirements and "处理对策" in requirements


def test_hypothetical_repair_cost_does_not_block_an_affordable_first_audit():
    gateway = GatewayLLM()
    gateway.base_url, gateway.api_key, gateway.model = "https://example.invalid", "test", "test"
    gateway.total_tokens = 20000
    handler = TaskHandlers(gateway, "test")
    handler.ledger = [Evidence(f"ev-{i+1}", "该模型在固定条件下进行状态比较。" * 4,
                               f"p{i//6}.pdf", 1, f"c{i}", "The state comparison is conditioned on fixed observations. " * 3)
                      for i in range(30)]
    body = "\n\n".join("## 比较维度 " + str(i) + "\n\n" + ("给定证据的条件限制应在比较过程中保留。" * 18) + f" [{i+1}, p.1]"
                         for i in range(5))
    case = SimpleNamespace(case_id="C-99", function="Survey", input="比较五篇状态模型")
    responses = [{"markdown": body, "used_evidence_ids": [e.evidence_id for e in handler.ledger]},
                 {"ok": True, "issues": []}]
    def request(system, prompt, requested, remaining):
        value = responses.pop(0)
        return {"usage": {"total_tokens": 6000 if "markdown" in value else 3000},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}
    with patch.object(gateway, "_request_once", request):
        result = handler.compose_report(case)
    assert handler.audit.get("ok") is True and not responses
    assert result["case_id"] == "C-99" and gateway.total_tokens <= gateway.budget


def test_unavailable_audit_is_not_sent_as_a_text_repair_instruction():
    stages = []
    class LLM:
        def json(self, stage, data, **kwargs):
            stages.append(stage)
            if stage == "report":
                return {"markdown": "建议待验证设计，固定实验条件与控制变量。" * 60 + " [1, p.1]",
                        "used_evidence_ids": ["ev-1"]}
            raise ModelError("insufficient reserved budget for audit")
    handler = TaskHandlers(LLM(), "test")
    handler.ledger = [Evidence("ev-1", "当前材料提供了一项有条件的结果。", "p.pdf", 1, "c", "The result is conditional.")]
    with pytest.raises(EvidenceError, match="Idea model report failed"):
        handler.compose_report(SimpleNamespace(case_id="E-99", function="Idea", input="设计实验"))
    assert stages == ["report", "audit"]
    assert handler.audit["mode"] == "model_report_failed"


def test_proposed_hypothesis_keeps_confidence_interval_in_the_same_paragraph_scope():
    evidence = [{"evidence_id": "ev-1", "ref": 1, "page": 1}]
    background = "已核验证据及其适用边界需要逐项比较。" * 60 + " [1, p.1]\n\n"
    proposal = "**建议/待验证假设H1**：组合模块改善主要指标。**可证伪判据**：EM差值95%置信区间上限低于最小效应时拒绝有意义的改善假设。"
    validate_citations(background + proposal, evidence, ["ev-1"])
    with pytest.raises(EvidenceError, match="numeric"):
        validate_citations(background + proposal + "论文报告准确率达到99%。", evidence, ["ev-1"])


def test_proposed_steps_and_table_do_not_need_fake_paper_citations():
    evidence = [{"evidence_id": "ev-1", "ref": 1, "page": 1}]
    body = "已核验证据及其范围应逐项核查。" * 80 + " [1, p.1]\n\n"
    body += "原文提供了样本线索[1, p.1]。拟议获取步骤：先筛选可授权样本。划分以细胞分组，训练60%、验证20%、测试20%。"
    body += "\n\n## 实验配置\n\n| 项目 | 拟议设置 |\n|---|---|\n| 重复 | 报告95%区间 |"
    validate_citations(body, evidence, ["ev-1"])
    with pytest.raises(EvidenceError, match="numeric"):
        validate_citations(body + "\n| 文献结果 | 论文报告准确率达到99%。 |", evidence, ["ev-1"])


def test_other_papers_reader_cannot_be_renamed_to_a_different_authorized_method():
    evidence = [{"evidence_id": "a", "filename": "2601.12345-ALPHA.pdf", "ref": 1, "page": 2,
                 "claim": "本篇阅读器处理更多样本。", "quote": "Our reader processes more observations."},
                {"evidence_id": "b", "filename": "2601.12346-BETA.pdf", "ref": 2, "page": 3,
                 "claim": "BETA使用分组建模。", "quote": "BETA uses grouped models."}]
    body = "比较论文事实及其明确条件。" * 90 + " [1, p.2][2, p.3]\n\n"
    with pytest.raises(EvidenceError, match="named method"):
        validate_citations(body + "BETA的阅读器处理更多样本。[1, p.2]", evidence, ["a", "b"])
    validate_citations(body + "建议BETA采用新增的噪声模块，原机制作为基线。[1, p.2]", evidence, ["a", "b"])
    evidence[0]["quote"] = "Our reader processes more observations than BETA."
    validate_citations(body + "ALPHA报告其阅读器与BETA对比。[1, p.2]", evidence, ["a", "b"])


def test_core_mechanism_is_selected_before_high_frequency_related_work():
    abstract = Chunk("a", "unseen.pdf", 1, "Abstract", "Catalytic state transitions are studied. " * 15, "text", "a")
    related = [Chunk(f"r{i}", "unseen.pdf", i+2, "Related work", "Related work on catalytic state transitions. " * 20,
                     "text", f"h{i}") for i in range(6)]
    method = Chunk("own", "unseen.pdf", 8, "Model", "We propose two variants of our model. The catalytic transition is defined as a conditional state update. " * 7,
                   "text", "own")
    selected = report_evidence_chunks(HybridRetriever([abstract, *related, method]), QueryPlan("catalytic state transitions"))
    assert "own" in {c.chunk_id for c in selected}
    assert len(selected) <= 8


def test_ambiguous_idea_numbers_require_semantic_classification_instead_of_false_repair():
    evidence = [{"evidence_id": "ev-1", "ref": 1, "page": 1}]
    body = "比较来源与适用条件。" * 120 + " [1, p.1]\n\n训练集80%，测试集20%，按细胞分组。"
    uncertain = validate_citations(body, evidence, ["ev-1"], function="Idea")
    assert uncertain == ["训练集80%，测试集20%，按细胞分组。"]
    with pytest.raises(EvidenceError, match="numeric"):
        validate_citations(body, evidence, ["ev-1"], function="Survey")
    with pytest.raises(EvidenceError, match="numeric"):
        validate_citations(body + "论文报告准确率99%。", evidence, ["ev-1"], function="Idea")


def test_survey_digest_keeps_multiple_method_components_and_an_experiment():
    ledger = [Evidence("ev-1", "采用双状态模型。", "unseen.pdf", 1, "c", "dual states", facet="method", role="own_work"),
              Evidence("ev-2", "第一种变体独立更新。", "unseen.pdf", 2, "d", "independent updates", facet="method", role="own_work"),
              Evidence("ev-3", "第二种变体联合更新。", "unseen.pdf", 3, "e", "joint updates", facet="method", role="own_work"),
              Evidence("ev-4", "相比基线误差降低。", "unseen.pdf", 4, "f", "lower error than baseline", facet="result", role="own_work"),
              Evidence("ev-5", "其他论文采用图模型。", "unseen.pdf", 5, "g", "related study", facet="method", role="related_work")]
    assert {e.evidence_id for e in survey_core_facts(ledger)} == {"ev-1", "ev-2", "ev-3", "ev-4"}


def test_survey_does_not_cut_a_short_chunk_again_before_extraction():
    text = "Background about the study. " * 22 + "The second variant updates states independently."
    chunk = Chunk("c", "unseen.pdf", 3, "Model", text, "text", "h")
    class LLM:
        def json(self, stage, data, **kwargs):
            assert data["chunks"][0]["text"] == text
            return {"claims": []}
    TaskHandlers(LLM(), "test").extract(SimpleNamespace(input="比较两个变体", function="Survey"), [chunk])


def test_precheck_repair_still_requires_the_first_full_semantic_audit():
    bad, corrected = "本篇机制的定义可回查。[1, p.9]", "本篇机制的定义可回查。[1, p.1]"
    body = "建议待验证实验应固定划分和控制变量。" * 60 + " [2, p.2]\n\n" + bad
    class LLM:
        def json(self, stage, data, **kwargs):
            if stage == "report":
                return {"markdown": body, "used_evidence_ids": ["ev-1", "ev-2"]}
            if stage == "repair":
                return {"patches": [{"original": bad, "replacement": corrected}]}
            assert stage == "audit"
            assert data["document_view"] == "complete_document" and not data["applied_patches"]
            assert "固定划分和控制变量" in data["markdown"] and corrected in data["markdown"]
            assert {e["evidence_id"] for e in data["source_quotes"]} == {"ev-1", "ev-2"}
            return {"ok": True, "issues": []}
    handler = TaskHandlers(LLM(), "test")
    handler.ledger = [Evidence("ev-1", "本篇机制的定义可回查。", "a.pdf", 1, "c", "The mechanism is defined."),
                      Evidence("ev-2", "另一篇讨论约束。", "b.pdf", 2, "d", "The constraints are explicit.")]
    result = handler.compose_report(SimpleNamespace(case_id="E-99", function="Idea", input="比较约束"))
    assert corrected in result["markdown"] and handler.audit["ok"]


def test_explicit_account_quota_rejection_is_not_retried_or_charged_as_unknown_inference():
    gateway = GatewayLLM()
    gateway.base_url, gateway.api_key, gateway.model = "https://example.invalid", "test", "test"
    with patch.object(gateway, "_request_once", side_effect=ModelQuotaError("quota exhausted")):
        with pytest.raises(ModelQuotaError):
            TaskHandlers(gateway, "test")._plan(SimpleNamespace(function="Survey", input="比较机制", constraints=[]))
    assert gateway.calls == 1 and gateway.total_tokens == 0
    assert gateway.usage[0]["usage_source"] == "gateway_rejected"


def test_stdlib_transport_distinguishes_account_quota_from_transient_429():
    import io
    import urllib.error
    gateway = GatewayLLM()
    gateway.base_url = "https://example.invalid"
    error = urllib.error.HTTPError("https://example.invalid", 429, "quota", {},
                                  io.BytesIO(b'{"error":{"code":"AccountQuotaExceeded"}}'))
    with patch("urllib.request.urlopen", side_effect=error), pytest.raises(ModelQuotaError):
        gateway._request_stdlib({"messages": []}, 20)


def test_sdk_transport_distinguishes_account_quota_from_transient_429():
    from unittest.mock import MagicMock
    gateway = GatewayLLM()
    error = type("RateLimitError", (Exception,), {})()
    error.body = {"code": "AccountQuotaExceeded"}
    client = MagicMock()
    client.__enter__.return_value = client
    client.chat.completions.create.side_effect = error
    with patch.dict("sys.modules", {"openai": SimpleNamespace(OpenAI=lambda **kwargs: client)}), pytest.raises(ModelQuotaError):
        gateway._request_once("system", "prompt", 5, 20)
