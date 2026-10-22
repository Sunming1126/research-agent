"""复杂问题的证据解释、预算预留和句级数字约束。"""
from dataclasses import asdict
from unittest.mock import patch

import pytest

from research_agent.core.models import Chunk, Evidence
from research_agent.evidence import EvidenceError
from research_agent.evidence.ledger import coverage_matrix, select_claims, verify_claims
from research_agent.evidence.reporting import validate_citations, render_evidence_citations
from research_agent.integrations.llm import GatewayLLM, ModelError


def test_unsupported_interpretation_returns_to_exact_source_quote():
    quote = "The observational design does not establish a causal relationship."
    evidence = Evidence("ev-1", "该实验确立了因果关系。", "new.pdf", 2, "c", quote)
    chunk = Chunk("c", "new.pdf", 2, "Limitations", quote, "text", "h")
    llm = GatewayLLM()
    response = {"claims": [{"evidence_id": "ev-1", "verdict": "unsupported", "facet": "limitation",
                            "role": "own_work", "reason": "原文否定因果解释"}]}
    with patch.object(llm, "json", return_value=response):
        checked, audit = verify_claims(llm, [evidence], [chunk])
    assert checked[0].claim.endswith(quote)
    assert checked[0].verification == "verbatim_only"
    assert audit["supported"] == 0 and audit["verbatim_only"] == 1


def test_verifier_missing_ids_cannot_certify_unchecked_claims():
    evidence = Evidence("ev-1", "模型具有局限。", "new.pdf", 2, "c", "The model has known limitations.")
    llm = GatewayLLM()
    with patch.object(llm, "json", return_value={"claims": []}):
        checked, audit = verify_claims(llm, [evidence], [])
    assert checked[0].verification == "verbatim_only" and audit["error"]


def test_selection_preserves_papers_and_different_facets():
    ledger = [Evidence(f"a{i}", "方法", "a.pdf", 1, "c", "exact quotation", facet="method") for i in range(8)]
    ledger += [Evidence("result", "结果", "a.pdf", 3, "c", "exact quotation", facet="result"),
               Evidence("b", "边界", "b.pdf", 4, "c", "exact quotation", facet="limitation")]
    selected = select_claims(ledger, maximum_per_paper=3)
    assert {e.filename for e in selected} == {"a.pdf", "b.pdf"}
    assert "result" in {e.evidence_id for e in selected}
    assert len(selected) == 4
    assert coverage_matrix(selected)["papers"]["a.pdf"]["result"] == ["result"]


def test_proposal_word_in_same_paragraph_does_not_excuse_factual_number():
    evidence = [{"evidence_id": "e", "ref": 1, "page": 2}]
    body = "证据边界需要逐项核实。" * 90 + " [1, p.2]\n\n建议设计对照实验。论文实测提升95%。"
    with pytest.raises(EvidenceError, match="numeric"):
        validate_citations(body, evidence, ["e"])


def test_reserved_budget_blocks_early_request_and_restores_afterwards():
    llm = GatewayLLM(token_budget=1000)
    llm.base_url, llm.api_key, llm.model = "https://example.invalid", "test", "test"
    with llm.reserve(990):
        with pytest.raises(ModelError, match="budget"):
            llm.json("chat", {}, max_tokens=10)
    assert llm.token_reserve == 0 and llm.calls == 0


def test_internal_evidence_tags_generate_physical_pages_and_used_ids():
    evidence = [{"evidence_id": "ev-1", "ref": 4, "page": 26},
                {"evidence_id": "ev-2", "ref": 2, "page": 3}]
    rendered, used = render_evidence_citations("事实[E:ev-1]，比较[E:ev-1, ev-2]。", evidence)
    assert rendered == "事实[4, p.26]，比较[4, p.26][2, p.3]。"
    assert used == ["ev-1", "ev-2"]
    with pytest.raises(EvidenceError, match="unknown"):
        render_evidence_citations("事实[E:ev-999]", evidence)


def test_citation_diagnostics_identify_exact_passage_for_repair():
    evidence = [{"evidence_id": "ev-1", "ref": 1, "page": 26}]
    bad = "论文报告实验结果。[1, p.19]"
    draft = "限定证据范围，比较方法和实验条件。" * 70 + "\n\n" + bad
    with pytest.raises(EvidenceError) as error:
        validate_citations(draft, evidence, ["ev-1"])
    assert error.value.passages == [bad]
    assert "26" in str(error.value)


def test_verbatim_excerpt_does_not_leak_paper_internal_reference_number():
    quote = "The benchmark uses an established indoor scene dataset [30]."
    evidence = Evidence("ev-1", "不充分解释。", "new.pdf", 2, "c", quote)
    llm = GatewayLLM()
    with patch.object(llm, "json", return_value={"claims": []}):
        checked, _ = verify_claims(llm, [evidence], [])
    assert "[30]" not in checked[0].claim and checked[0].quote == quote


def test_explicit_proposal_heading_allows_designed_numeric_parameters():
    body = "比较证据及其适用条件。" * 90 + " [1, p.2]\n\n**预算（建议/待验证设计，非实测）**：拟议掩码比例25%，重复5次，报告95%置信区间。"
    evidence = [{"evidence_id": "e", "ref": 1, "page": 2}]
    validate_citations(body, evidence, ["e"])
    body += "论文报告准确率达到99%。"
    with pytest.raises(EvidenceError, match="numeric"):
        validate_citations(body, evidence, ["e"])
