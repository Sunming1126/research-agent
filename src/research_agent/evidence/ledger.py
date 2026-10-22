"""事实支持检查与覆盖矩阵；来源可定位和解释被支持分别记录。"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from ..integrations.llm import ModelError


FACETS = {"method", "data", "metric", "result", "limitation", "unspecified"}
ROLES = {"own_work", "related_work", "unknown", "unspecified"}


def coverage_matrix(ledger):
    papers = defaultdict(lambda: defaultdict(list))
    for evidence in ledger:
        papers[evidence.filename][evidence.facet].append(evidence.evidence_id)
    return {"papers": {name: dict(rows) for name, rows in papers.items()},
            "gaps": [{"filename": name, "facet": facet} for name, rows in papers.items()
                     for facet in ("method", "data", "metric", "result", "limitation") if facet not in rows],
            "scope": "gaps mean missing verified evidence, not that the paper omitted the topic"}


def select_claims(ledger, maximum_per_paper=5):
    """每篇先覆盖不同维度，再填充其余事实，控制重复声明占用的预算。"""
    grouped = defaultdict(list)
    for evidence in ledger:
        grouped[evidence.filename].append(evidence)
    selected = []
    for rows in grouped.values():
        chosen, seen = [], set()
        for evidence in rows:
            if evidence.facet not in seen:
                chosen.append(evidence)
                seen.add(evidence.facet)
        chosen = chosen[:maximum_per_paper]
        ids = {e.evidence_id for e in chosen}
        for evidence in rows:
            if len(chosen) < maximum_per_paper and evidence.evidence_id not in ids:
                chosen.append(evidence)
                ids.add(evidence.evidence_id)
        selected.extend(chosen)
    return selected


def verify_claims(llm, ledger, chunks):
    """语义核验只审当前原文；不确定解释退回原文摘录，不能冒充已核验结论。"""
    sources = {c.chunk_id: c for c in chunks}
    rows = []
    for evidence in ledger:
        chunk = sources.get(evidence.chunk_id)
        text = chunk.text if chunk else evidence.quote
        offset = max(0, text.find(evidence.quote))
        rows.append({"evidence_id": evidence.evidence_id, "claim": evidence.claim,
                     "quote": evidence.quote, "context": text[max(0, offset-160):offset+len(evidence.quote)+240],
                     "heading": chunk.heading if chunk else "", "role": evidence.role, "facet": evidence.facet})
    data = {"claims": rows}
    maximum = llm.output_capacity("verify_claims", data, reserve=10500, maximum=2400)
    decisions, failure = {}, None
    try:
        if maximum < 900:
            raise ModelError("evidence verification budget reserved for final report")
        with llm.reserve(10500):
            result = llm.json("verify_claims", data, max_tokens=maximum)
        returned = result.get("claims")
        if not isinstance(returned, list):
            raise ModelError("invalid claim verification array")
        known = {e.evidence_id for e in ledger}
        for row in returned:
            if (not isinstance(row, dict) or row.get("evidence_id") not in known
                    or row.get("evidence_id") in decisions or row.get("verdict") not in {"supported", "unsupported", "uncertain"}):
                raise ModelError("invalid claim verification decision")
            decisions[row["evidence_id"]] = row
        if set(decisions) != known:
            raise ModelError("claim verification omitted evidence IDs")
    except ModelError as exc:
        failure = str(exc)
        decisions = {}
    checked, audit = [], []
    for evidence in ledger:
        decision = decisions.get(evidence.evidence_id, {})
        supported = decision.get("verdict") == "supported"
        role = decision.get("role") if decision.get("role") in ROLES else evidence.role
        facet = decision.get("facet") if decision.get("facet") in FACETS else evidence.facet
        if supported:
            checked.append(replace(evidence, role=role, facet=facet, verification="semantic_supported"))
        else:
            # 原文连续摘录已在提取时校验；去掉不确定的模型解释，保留可回查原文。
            checked.append(replace(evidence, claim="原文摘录：" + evidence.quote,
                                   role="unknown", facet="unspecified", verification="verbatim_only"))
        audit.append({"evidence_id": evidence.evidence_id, "verdict": decision.get("verdict", "not_verified"),
                      "reason": str(decision.get("reason", failure or ""))[:240]})
    return checked, {"decisions": audit, "error": failure,
                     "supported": sum(e.verification == "semantic_supported" for e in checked),
                     "verbatim_only": sum(e.verification == "verbatim_only" for e in checked)}
