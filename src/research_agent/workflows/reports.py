from __future__ import annotations
import re
import json
from dataclasses import asdict
from collections import defaultdict
from ..core.models import Evidence
from ..core.utils import source_quote_matches
from ..integrations.instrumentation import tool
from ..integrations.llm import ModelError
from ..evidence import EvidenceError
from ..core.session import checkpoint, current_session
from ..evidence.ledger import FACETS, ROLES, coverage_matrix, select_claims, verify_claims
from ..evidence.reporting import render_evidence_citations
from ..rag.retrieval import QueryPlan
from ..evidence.reporting import (repair_paragraphs, apply_report_patches, report_requirements, report_evidence_chunks, fallback_evidence_chunks, resolve_quote_source, model_evidence_rows, remove_unsupported_authors, correct_paper_ids, render_claim_ledger_section, deterministic_idea, specific_idea_sections, deterministic_survey, validate_citations)


class ReportWorkflow:
    """C/E 证据提取、报告生成、审查与修订。"""
    def _model_step(self, stage, data, maximum, *, reserve=0, minimum=500):
        checkpoint("report_" + stage)
        if hasattr(self.llm, "output_capacity"):
            maximum = self.llm.output_capacity(stage, data, reserve=reserve, maximum=maximum)
            if maximum < minimum:
                raise ModelError("insufficient reserved budget for " + stage)
            with self.llm.reserve(reserve):
                return self.llm.json(stage, data, max_tokens=maximum)
        return self.llm.json(stage, data, max_tokens=maximum)

    @tool(name="build_claim_ledger")
    def extract(self, case, chunks):
        # 原文摘录必须逐字出现在授权片段中，才允许写入事实台账。
        claims = []
        sources = {c.chunk_id: c for c in chunks}
        grouped = defaultdict(list)
        for chunk in chunks:
            grouped[chunk.filename].append(chunk)
        for position, (filename, group) in enumerate(grouped.items()):
            # 均匀分配片段上下文，避免一个长章节挤掉实验、表格和局限。
            per_chunk = max(180, 4600 // max(1, len(group)))
            data = {"task": case.input, "filename": filename,
                    "chunks": [{"chunk_id": c.chunk_id, "page": c.page,
                                "heading": c.heading, "text": c.text[:per_chunk]} for c in group]}
            reserve = 13000 + max(0, len(grouped) - position - 1) * 1700
            value = self._model_step("extract", data, 2000, reserve=reserve, minimum=900)
            returned = value.get("claims", [])
            if not isinstance(returned, list):
                raise EvidenceError("extract claims must be an array")
            for item in returned:
                if not isinstance(item, dict):
                    self.rejected_claims.append({"filename": filename, "reason": "invalid_claim_object"})
                    continue
                chunk = sources.get(item.get("chunk_id"))
                quote, claim = item.get("quote"), item.get("claim")
                source = resolve_quote_source(chunk, quote, group)
                if source is None:
                    # 模型偶尔会改写摘录。丢弃该条，禁止未核验内容进入证据台账。
                    self.rejected_claims.append({"filename": filename, "chunk_id": item.get("chunk_id"),
                                                 "quote": str(quote)[:180],
                                                 "source_preview": chunk.text[:300] if chunk else None})
                    continue
                if not isinstance(claim, str) or not claim.strip():
                    raise EvidenceError("empty claim")
                # 原论文自己的 [14] 等文献编号不属于本题参考文献；保留其“文内所引”语义。
                claim = re.sub(r"\[\d{1,2}\]", "文内所引", claim)
                if source.chunk_id != chunk.chunk_id and all(c.chunk_id != source.chunk_id for c in self.selected_chunks):
                    self.selected_chunks.append(source)
                facet = item.get("facet") if item.get("facet") in FACETS else "unspecified"
                role = item.get("role") if item.get("role") in ROLES else "unspecified"
                claims.append(Evidence(f"ev-{len(self.ledger)+len(claims)+1}", claim,
                                       source.filename, source.page, source.chunk_id, quote, facet=facet, role=role))
        return claims


    @tool(name="compose_and_check_report")
    def report(self, case, retriever, plan):
        # 先提取可核验事实，再生成正文；模型草稿最多尝试两次。
        chunks = report_evidence_chunks(retriever, plan)
        if not chunks:
            raise EvidenceError("no readable authorized PDFs for report")
        self.selected_chunks = chunks
        self.ledger = self.extract(case, chunks)
        if getattr(self.llm, "supports_evidence_verification", False):
            self._recover_evidence_gaps(case, retriever, plan)
        # 用全部授权论文检查覆盖，不能只看首轮召回到的论文。
        expected = {c.filename for c in retriever.chunks if c.filename.lower().endswith(".pdf")}
        missing = expected - {e.filename for e in self.ledger}
        if missing:
            # 首轮漏掉指定论文时，仅回到该论文首页和前几页补取，禁止借用其他论文。
            supplemental = [chunk for name in sorted(missing)
                            for chunk in fallback_evidence_chunks(retriever.chunks, name)]
            if supplemental:
                self.selected_chunks.extend(supplemental)
                self.ledger.extend(self.extract(case, supplemental))
        self.ledger = select_claims(self.ledger)
        if getattr(self.llm, "supports_evidence_verification", False):
            checkpoint("evidence_semantic_verification")
            self.ledger, self.evidence_audit = verify_claims(self.llm, self.ledger, self.selected_chunks)
        self.coverage = coverage_matrix(self.ledger)
        filenames = sorted({e.filename for e in self.ledger})
        if not filenames or (case.function == "Survey" and len(filenames) < 5):
            raise EvidenceError("insufficient supported papers for report (Survey requires at least five)")
        refs = {name: i+1 for i, name in enumerate(filenames)}
        evidence = [{**asdict(e), "ref": refs[e.filename]} for e in self.ledger]
        model_evidence = model_evidence_rows(evidence)
        # 正文只展示每篇少量核心事实，完整台账留在证据文件中。
        anchor_ledger = select_claims(self.ledger, maximum_per_paper=2)
        anchor, anchor_ids = render_claim_ledger_section(anchor_ledger, refs) if case.function == "Survey" else ("", [])
        feedback, history, previous_used, repair_targets = [], [], [], []
        for attempt in range(2):
            patches = []
            audit_evidence = model_evidence
            try:
                if attempt and self.last_draft:
                    # 首稿已整篇审查过；只修正出错段，不重复生成和审查整篇事实。
                    paragraphs = repair_targets or repair_paragraphs(self.last_draft, feedback)
                    refs_in_patch = {int(n) for n in re.findall(r"\[(\d{1,2})(?:,\s*p\.\s*\d+)?\]", "\n".join(paragraphs))}
                    audit_evidence = [e for e in model_evidence if e["ref"] in refs_in_patch] or model_evidence
                    repaired = self._model_step("repair", {"paragraphs": paragraphs, "issues": feedback,
                        "claims": audit_evidence}, 2200, reserve=4200)
                    patches = repaired.get("patches", [])
                    revised = apply_report_patches(self.last_draft, patches)
                    value = {"markdown": revised, "used_evidence_ids": previous_used}
                else:
                    data = {"task": case.to_dict(), "claims": model_evidence,
                            "coverage_requirements": report_requirements(case.function), "repair_issues": feedback,
                            "evidence_coverage": self.coverage}
                    # 哈希、偏移等核验元数据留在磁盘；为审查和一次修订保留实际模型预算。
                    remaining = getattr(self.llm, "budget", 40000) - getattr(self.llm, "total_tokens", 0)
                    requested = min(4200, remaining - len(json.dumps(data, ensure_ascii=False)) // 2 - 2000)
                    if requested < 1600:
                        raise ModelError("insufficient model budget reserved for report")
                    value = self._model_step("report", data, min(3600, requested), reserve=7500, minimum=1600)
            except (ModelError, EvidenceError) as exc:
                history.append({"attempt": attempt + 1, "stage": "report", "issues": [str(exc)]})
                feedback = feedback + [str(exc)]
                break
            markdown = value.get("markdown", "")
            used = value.get("used_evidence_ids", [])
            if not isinstance(markdown, str) or not isinstance(used, list):
                raise EvidenceError("invalid report response")
            self.report_trace.append({"attempt": attempt + 1, "raw_markdown": markdown,
                                      "used_evidence_ids": list(used), "patches": patches})
            try:
                markdown, bound_ids = render_evidence_citations(markdown, evidence)
            except EvidenceError as exc:
                self.last_draft = markdown
                previous_used = list(used)
                feedback, repair_targets = [str(exc)], getattr(exc, "passages", [])
                history.append({"attempt": attempt + 1, "stage": "citation_binding", "issues": list(feedback)})
                continue
            if bound_ids:
                used = list(dict.fromkeys((previous_used if patches else []) + bound_ids))
            markdown = markdown.split("## 参考文献")[0].strip()
            # 只修正与唯一授权 arXiv 编号相差一位的排版笔误。
            markdown = correct_paper_ids(markdown, filenames)
            if anchor and not markdown.startswith(anchor):
                # 先列出可回查的逐篇事实，避免生成长文时遗失关键证据。
                markdown = anchor + "\n\n" + markdown
                used = list(dict.fromkeys(anchor_ids + used))
            # 作者姓名若不在已核验事实中，不把模型猜测写成论文归因。
            markdown = remove_unsupported_authors(markdown, evidence)
            self.last_draft = markdown
            previous_used = list(used)
            try:
                validate_citations(markdown, evidence, used, minimum=5 if case.function == "Survey" else 1)
                audit_text = "\n\n".join(p["replacement"] for p in patches) if patches else markdown
                evidence_ids = {e["evidence_id"] for e in audit_evidence}
                audit = self._model_step("audit", {"task": case.to_dict(), "markdown": audit_text,
                                                "full_document": markdown if patches else None,
                                                "previous_issues": feedback if patches else [],
                                                "applied_patches": patches,
                                                "scope": "核查变更及之前问题是否均解决；未改段落已在首轮整篇审查" if patches else "整篇审查",
                                                "coverage_requirements": report_requirements(case.function),
                                                "source_quotes": [{"evidence_id": e.evidence_id, "quote": e.quote}
                                                                  for e in self.ledger if e.evidence_id in evidence_ids],
                                                "claims": audit_evidence}, 1800, reserve=3200 if not patches else 0)
                if (type(audit.get("ok")) is not bool or not isinstance(audit.get("issues"), list)
                        or any(not isinstance(issue, str) for issue in audit["issues"])):
                    raise EvidenceError("invalid semantic audit response")
                history.append({"attempt": attempt + 1, "stage": "audit", "audit": dict(audit)})
                audit["history"] = list(history)
                self.audit = audit
                if audit.get("ok") is not True:
                    raise EvidenceError("; ".join(str(x) for x in audit.get("issues", ["audit rejected"])))
                selected_refs = {int(x) for x in re.findall(r"\[(\d{1,2})(?:,\s*p\.\s*\d+)?\]", markdown)}
                markdown += "\n\n## 参考文献\n\n" + "\n".join(f"[{refs[name]}] {name}" for name in filenames if refs[name] in selected_refs)
                kind = "综述" if case.function == "Survey" else "灵感Idea报告"
                return {"case_id": case.case_id, "path": f"data/expert/{case.case_id}-{kind}-{self.team}.md", "markdown": markdown}
            except (EvidenceError, ModelError) as exc:
                feedback = [str(exc)]
                repair_targets = getattr(exc, "passages", [])
                history.append({"attempt": attempt + 1, "stage": "validation", "issues": list(feedback)})
        if case.function == "Survey":
            # 自由长文多次审查失败时，只拼接已经进入证据台账的事实。
            markdown, used = deterministic_survey(case.input, self.ledger)
            body = markdown.split("## 参考文献", 1)[0]
            validate_citations(body, evidence, used, minimum=5)
            self.audit = {"ok": None, "mode": "deterministic_fallback", "local_validation": True,
                          "model_issues": feedback, "history": history}
            return {"case_id": case.case_id,
                    "path": f"data/expert/{case.case_id}-综述-{self.team}.md", "markdown": markdown}
        # E 类同样只使用已核验的证据生成保底方案，避免修复循环耗尽模型预算。
        markdown, used = deterministic_idea(case.input, self.ledger)
        body = markdown.split("## 参考文献", 1)[0]
        validate_citations(body, evidence, used)
        self.audit = {"ok": None, "mode": "deterministic_fallback", "local_validation": True,
                      "model_issues": feedback, "history": history}
        return {"case_id": case.case_id,
                "path": f"data/expert/{case.case_id}-灵感Idea报告-{self.team}.md", "markdown": markdown}

    def _recover_evidence_gaps(self, case, retriever, plan):
        """仅补检缺失维度；最多两篇、每篇两段，保持长文后续预算。"""
        if not hasattr(retriever, "search"):
            return
        session = current_session()
        if session and session.remaining() < 65:
            return
        if getattr(self.llm, "remaining_tokens", 40000) < 23000:
            return
        queries = {"method": "method mechanism approach 方法 机制",
                   "result": "experiment results evaluation 实验 结果",
                   "limitation": "limitations assumptions failure 局限 假设"}
        gaps = coverage_matrix(self.ledger)["gaps"]
        seen = {c.chunk_id for c in self.selected_chunks}
        supplemental, documents = [], set()
        for gap in gaps:
            name, facet = gap["filename"], gap["facet"]
            if facet not in queries or (name not in documents and len(documents) >= 2):
                continue
            if sum(c.filename == name for c in supplemental) >= 2:
                continue
            hits = retriever.search(QueryPlan(queries[facet] + " " + plan.topic), filename=name, top_k=12)
            selected = next((h.chunk for h in hits if h.chunk.chunk_id not in seen
                             and h.chunk.kind != "references" and len(h.chunk.text.strip()) >= 80), None)
            if selected:
                supplemental.append(selected)
                seen.add(selected.chunk_id)
                documents.add(name)
        if supplemental:
            self.selected_chunks.extend(supplemental)
            try:
                self.ledger.extend(self.extract(case, supplemental))
            except ModelError as exc:
                self.rejected_claims.append({"reason": "gap_recovery_budget", "message": str(exc)})
