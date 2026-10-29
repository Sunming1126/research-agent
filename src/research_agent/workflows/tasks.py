from __future__ import annotations

import json
import hashlib
import re
from dataclasses import asdict
from collections import defaultdict

from ..core.models import Chunk, Evidence
from ..core.utils import source_quote_matches
from ..integrations.instrumentation import tool
from ..integrations.llm import ModelError, ModelQuotaError
from ..rag.retrieval import QueryPlan, MAX_SUBQUERIES
from ..rag.retrieval import HybridRetriever, fuse_document_rankings, bounded_document_candidates
from ..rag.evidence_selection import select_evidence, evidence_batches
from ..core.session import checkpoint, current_session




from .reports import ReportWorkflow
from ..evidence import EvidenceError
from ..evidence.reporting import (repair_paragraphs, apply_report_patches, report_requirements, report_evidence_chunks, fallback_evidence_chunks, resolve_quote_source, model_evidence_rows, remove_unsupported_authors, correct_paper_ids, render_claim_ledger_section, deterministic_idea, specific_idea_sections, deterministic_survey, validate_citations)
from ..rag.paper_selection import (compact_retrieval_chunk, explicitly_excluded_paper, requested_paper_count, valid_paper_approval)


class TaskHandlers(ReportWorkflow):
    """路由 A/B/C/E；D 类由独立实验执行器处理。"""
    def __init__(self, llm, team):
        self.llm = llm
        self.team = team
        self.ledger = []
        self.audit = {}
        self.selected_chunks = []
        self.last_draft = ""
        self.rejected_claims = []
        self.retrieval_trace = {}
        self.query_plan_status = "pending"
        self.coverage = {}
        self.evidence_audit = {}
        self.document_expander = None
        self.document_probe = None
        self.probe_trace = {}
        self.report_trace = []

    @staticmethod
    def _task_dict(case):
        method = getattr(case, 'to_dict', None)
        if callable(method):
            return method()
        # 内部工作流也支持原有的轻量题目对象；HTTP入口仍严格校验完整CaseContext。
        return {key: getattr(case, key) for key in ('case_id', 'function', 'title', 'input', 'materials',
                'constraints', 'expected_output', 'output_schema_ref') if hasattr(case, key)}

    @staticmethod
    def _question_text(case):
        values = [getattr(case, 'title', ''), case.input, *getattr(case, 'constraints', []),
                  getattr(case, 'expected_output', '')]
        return '\n'.join(value for value in values if isinstance(value, str) and value.strip())

    def _plan(self, case):
        if case.function in {"Survey", "Idea"} and getattr(self.llm, "supports_batched_extraction", False):
            # 指定材料已确定：把有限在线时间留给原文提取、写作和审查。
            self.query_plan_status = "original_for_report_budget"
            return QueryPlan('\n'.join([case.title, case.input, *case.constraints, case.expected_output]))
        data = self._task_dict(case)
        for attempt, maximum in enumerate((1800, 2800), start=1):
            try:
                value = self.llm.json("query", data, max_tokens=maximum)
                self.query_plan_status = "model" if attempt == 1 else "model_retry"
                return QueryPlan.from_model(value, case.input)
            except ModelQuotaError:
                raise
            except ModelError:
                # 一次截断或网关波动不应直接让整题失败；重试仍失败则用原题。
                continue
        self.query_plan_status = "original_fallback"
        return QueryPlan(self._question_text(case))

    def handle(self, case, retriever):
        if case.function == "Chat":
            evidence = [asdict(c) for c in retriever.per_document(QueryPlan(case.input), 3)] if case.materials else []
            value = self.llm.json("chat", {"task": case.to_dict(), "evidence": evidence}, max_tokens=2200)
            return {"case_id": case.case_id, "answer": value.get("answer"), "clarifying_question": value.get("clarifying_question")}
        if case.function == "Literature_Retrieval" and not retriever.chunks:
            self.retrieval_trace = {"candidates": [], "reason": "empty_authorized_library"}
            return {"case_id": case.case_id, "papers": []}
        plan = self._plan(case)
        if case.function == "Literature_Retrieval":
            return self.retrieval(case, retriever, plan)
        if case.function in {"Survey", "Idea"}:
            return self.report(case, retriever, plan)
        raise ValueError("unsupported handler")

    @tool(name="rank_papers_with_evidence")
    def retrieval(self, case, retriever, plan):
        if self.document_probe is not None:
            probe, self.document_probe = self.document_probe, None
            deeper, self.probe_trace = probe([self._question_text(case), plan.topic, plan.methods,
                                             plan.focus, *plan.variants, *plan.subqueries])
            if deeper:
                merged_chunks = {c.chunk_id: c for c in [*retriever.chunks, *deeper]}
                retriever = HybridRetriever(list(merged_chunks.values()))
        # 模型改写与原问题各保留一条召回通道；跨领域复合题可能被改写窄化。
        rewritten = retriever.top_documents(plan, limit=18, excerpts=3)
        original = retriever.top_documents(QueryPlan(self._question_text(case), exclusions=plan.exclusions,
                                                     variants=plan.variants),
                                           limit=18, excerpts=3)
        merged = {}

        def keep_best(rows):
            for row in rows:
                name, score, _ = row
                if name not in merged or score > merged[name][1]:
                    merged[name] = row

        keep_best(rewritten)
        keep_best(original)
        rankings = [rewritten, original]
        component_hits = []
        component_top = []
        strong_components = []
        for topic in plan.subqueries[:MAX_SUBQUERIES]:
            # 多目标题逐个主题检索，避免短摘要在混合查询中被其他领域词淹没。
            hits = retriever.top_documents(QueryPlan(topic, exclusions=plan.exclusions),
                                           limit=3, excerpts=3)
            component_hits.append({"query": topic, "filenames": [name for name, _, _ in hits]})
            rankings.append(hits)
            if hits:
                component_top.append(hits[0])
            if hits and hits[0][1] >= .75 and (len(hits) == 1 or hits[0][1] - hits[1][1] >= .22):
                strong_components.append((topic, hits[0]))
            keep_best(hits)
        # 文档RRF保持原定义；证据选择使用全部通道，而不是提前按原始分截掉条件片段。
        grouped, fusion_scores = fuse_document_rankings(rankings, excerpts=32)
        if self.document_expander is not None:
            session = current_session()
            if session is None or session.remaining() > 65:
                names = list(dict.fromkeys([name for name, score, _ in component_top if score >= .18]
                                          + [name for name, score, _ in grouped[:4] if score >= .18]))[:8]
                expander, self.document_expander = self.document_expander, None
                deeper = expander(names) if names else []
                expanded = {c.filename for c in deeper}
                if expanded:
                    full = [c for c in retriever.chunks if c.filename not in expanded] + deeper
                    result = self.retrieval(case, HybridRetriever(full), plan)
                    self.retrieval_trace["fulltext_candidates"] = sorted(expanded)
                    return result
        self.retrieval_trace = {"query_plan": asdict(plan),
                                "query_plan_status": self.query_plan_status,
                                "rewritten": [name for name, _, _ in rewritten],
                                "original": [name for name, _, _ in original],
                                "components": component_hits, "fusion": "document_rrf_k60",
                                "fusion_scores": fusion_scores, "deep_page_probe": self.probe_trace}
        requested = requested_paper_count(case.input)
        candidate_limit = min(18, max(12, (requested or 0) * 2))
        eligible = [row for row in grouped if row[0].lower().endswith('.pdf') and row[1] >= .13]
        shortlist = bounded_document_candidates(eligible, [name for name, _, _ in component_top], candidate_limit)
        evidence_queries = [self._question_text(case), plan.topic, plan.methods, plan.focus, *plan.subqueries]
        candidates = [{"filename": name, "score": score,
                       "chunks": [compact_retrieval_chunk(h.chunk, evidence_queries)
                                  for h in select_evidence(group, evidence_queries, limit=3)]}
                      for name, score, group in shortlist]
        self.retrieval_trace["candidate_limit"] = candidate_limit
        self.retrieval_trace["eligible_before_limit"] = len(eligible)
        self.retrieval_trace["candidates"] = [row["filename"] for row in candidates]
        # 保留候选分数，便于逐轮检查阈值与上下文开销；不改变模型实际收到的证据。
        self.retrieval_trace["candidate_scores"] = [
            {"filename": row["filename"], "score": round(row["score"], 4)}
            for row in candidates
        ]
        if not candidates:
            return {"case_id": case.case_id, "papers": []}
        approved = []
        approval_evidence = defaultdict(set)
        partial_error = None
        rejected_approvals = []
        repair_attempts = 0
        explicitly_rejected = set()
        task_brief = self._task_dict(case)
        # 当前批次候选已携带授权片段；重复发送数百个文件名会诱发跨批次误选。
        task_brief.pop("materials", None)
        # 分批交给模型核查，限制上下文长度，同时保留足够的候选论文。
        batches = list(evidence_batches(candidates, task_brief, self.llm))
        self.retrieval_trace['batch_sizes'] = [len(batch) for batch in batches]
        self.retrieval_trace['evidence_policy'] = 'condition_coverage_contiguous_window'
        start = 0
        for batch_index, batch in enumerate(batches):
            try:
                session = current_session()
                if approved and session and session.remaining() < 35:
                    partial_error = {"error_type": "TimeReservedForPublication", "failed_batch": batch_index,
                                     "unprocessed_candidates": len(candidates) - start}
                    break
                value = self.llm.json("retrieve", {"task": task_brief, "candidates": batch}, max_tokens=2800)
                if not isinstance(value, dict) or not isinstance(value.get("papers"), list):
                    raise ModelError("retrieval response requires a papers array")
            except (ModelError, TimeoutError) as exc:
                # 后续批次暂不可用时保留前批次已获明确证据批准的论文，不猜补其余候选。
                if not approved:
                    raise
                partial_error = {"error_type": type(exc).__name__, "failed_batch": batch_index,
                                 "unprocessed_candidates": len(candidates) - start}
                break
            by_name = {row["filename"]: row for row in batch}
            for item in value.get("papers", []):
                if not isinstance(item, dict) or item.get("filename") not in by_name:
                    rejected_approvals.append({"batch": batch_index, "reason": "filename_not_in_batch"})
                    continue
                source = by_name[item["filename"]]
                if item.get("decision") in {"exclude", "uncertain"} or item.get("relevant") is False:
                    explicitly_rejected.add(item["filename"].casefold())
                    continue
                allowed = {c["chunk_id"] for c in source["chunks"]}
                if not valid_paper_approval(item, allowed):
                    rejected_approvals.append({"batch": batch_index, "filename": source["filename"],
                                               "reason": "missing_or_wrong_evidence"})
                    # 高位候选有时只写错片段编号；允许最多两次单篇重审，仍须通过编号核验。
                    remaining = current_session().remaining() if current_session() else 180
                    if source["score"] >= .5 and repair_attempts < 2 and remaining > 75:
                        repair_attempts += 1
                        try:
                            repaired = self.llm.json("retrieve", {"task": task_brief,
                                "candidates": [source], "repair_issues": ["仅使用当前候选提供的 chunk_id；不相关则返回空列表"]},
                                max_tokens=1200)
                            replacement = next((row for row in repaired.get("papers", [])
                                if isinstance(row, dict) and row.get("filename") == source["filename"]
                                and valid_paper_approval(row, allowed)), None)
                            if replacement is not None:
                                approval_evidence[source['filename']].update(replacement['chunk_ids'])
                                approved.append((source["score"], {"filename": source["filename"],
                                                                   "reason": replacement["reason"]}))
                                rejected_approvals[-1]["recovered"] = True
                        except ModelQuotaError:
                            partial_error = {"error_type": "ModelQuotaError", "stage": "evidence_repair",
                                             "failed_batch": batch_index}
                            break
                        except ModelError:
                            rejected_approvals[-1]["recovered"] = False
                    continue
                approved.append((source["score"], {"filename": item["filename"], "reason": item["reason"]}))
                approval_evidence[item['filename']].update(item['chunk_ids'])
            if partial_error:
                break
            start += len(batch)
        unique = {}
        for score, paper in sorted(approved, key=lambda row: -row[0]):
            unique.setdefault(paper["filename"].casefold(), paper)
        rule_added = []
        rechecks = 0
        for topic, (name, score, hits) in strong_components:
            if partial_error:
                break
            if name.casefold() in unique or name.casefold() in explicitly_rejected or not hits or hits[0].chunk.page != 1:
                continue
            if any(phrase.casefold() in hits[0].chunk.heading.casefold()
                   for phrase in plan.exclusions):
                continue
            if rechecks >= 2:
                break
            if current_session() and current_session().remaining() <= 75:
                break
            source = next((row for row in candidates if row["filename"] == name), None)
            if source is None:
                continue
            rechecks += 1
            # 高词法分只能触发再核查，最终纳入仍须有明确语义批准和真实片段。
            try:
                value = self.llm.json("retrieve", {"task": task_brief, "candidates": [source],
                    "repair_issues": ["复核独立目标，不相关或证据不足时明确exclude/uncertain"]}, max_tokens=1200)
                allowed = {c["chunk_id"] for c in source["chunks"]}
                paper = next((p for p in value.get("papers", []) if isinstance(p, dict)
                              and p.get("filename") == name and valid_paper_approval(p, allowed)), None)
                if paper:
                    approval_evidence[name].update(paper['chunk_ids'])
                    unique[name.casefold()] = {"filename": name, "reason": paper["reason"]}
                    rule_added.append(name)
            except ModelQuotaError:
                partial_error = {"error_type": "ModelQuotaError", "stage": "component_recheck"}
                break
            except ModelError:
                continue
        strong_names = {row[0].casefold() for _, row in strong_components}
        count_constrained = (requested is not None and len(plan.subqueries) == requested
                             and len(strong_components) == requested and len(strong_names) == requested)
        if count_constrained:
            # 明确要 N 篇且 N 个独立子题各有唯一强命中时，去掉筛选模型的额外误报。
            unique = {name: paper for name, paper in unique.items() if name in strong_names}
        # 多个独立主题各有明显主峰时，剪去与主峰分数断层的弱关联尾部。
        # 分数只用于约束已获模型批准的论文，不能凭排名直接补写事实。
        peak_names = {name.casefold() for name, _, _ in component_top}
        peak_scores = [merged[name][1] for name in merged if name.casefold() in peak_names]
        other_scores = [score for name, score, _ in grouped if name.casefold() not in peak_names]
        tail_gap = (len(plan.subqueries) >= 2 and len(component_top) == len(plan.subqueries)
                    and len(peak_names) == len(component_top) and len(peak_scores) == len(peak_names)
                    and min(peak_scores) >= .75
                    and (not other_scores or min(peak_scores) - max(other_scores) >= .15))
        tail_filtered = []
        if tail_gap:
            tail_filtered = [paper["filename"] for name, paper in unique.items() if name not in peak_names]
            unique = {name: paper for name, paper in unique.items() if name in peak_names}
        self.retrieval_trace["rule_added"] = rule_added
        self.retrieval_trace["rejected_approvals"] = rejected_approvals
        self.retrieval_trace["count_constrained"] = count_constrained
        self.retrieval_trace["tail_gap"] = tail_gap
        self.retrieval_trace["tail_filtered"] = tail_filtered
        self.retrieval_trace["approved"] = [row["filename"] for row in unique.values()]
        # 题目明确要求 N 篇时，最终名单不得因模型额外批准而超过 N 篇。
        limit = min(requested, 10) if requested is not None else 10
        self.retrieval_trace["requested_limit"] = limit
        accepted, excluded = [], []
        for paper in unique.values():
            if explicitly_excluded_paper(paper, plan.exclusions):
                excluded.append(paper["filename"])
            else:
                accepted.append(paper)
        self.retrieval_trace["explicitly_excluded"] = excluded
        if partial_error:
            self.retrieval_trace["partial_error"] = partial_error
        fused_order = {name: index for index, (name, _, _) in enumerate(grouped)}
        accepted.sort(key=lambda paper: fused_order.get(paper['filename'], len(grouped)))
        accepted = accepted[:limit]
        ids = {identifier for paper in accepted for identifier in approval_evidence[paper['filename']]}
        # Keep the original top_documents protocol: lightweight retrievers need
        # not expose an additional public .chunks collection. These are exactly
        # the source objects from which the sent candidate excerpts were made.
        sources = {h.chunk.chunk_id: h.chunk for _, _, group in grouped for h in group}
        self.selected_chunks = [sources[identifier] for identifier in sorted(ids)]
        self.retrieval_trace['accepted_evidence'] = [
            {'filename': c.filename, 'chunk_id': c.chunk_id, 'page': c.page,
             'document_sha256': c.document_sha256, 'chunk_sha256': c.sha256} for c in self.selected_chunks]
        return {"case_id": case.case_id, "papers": accepted}
