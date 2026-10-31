from __future__ import annotations
import re
import json
import time
from contextlib import contextmanager
from dataclasses import asdict
from collections import defaultdict
from ..core.models import Evidence
from ..contracts.validation import validate_report_limits, report_citations
from ..core.utils import source_quote_matches
from ..integrations.instrumentation import tool
from ..integrations.llm import ModelError
from ..evidence import EvidenceError
from ..core.session import checkpoint, current_session
from ..evidence.ledger import FACETS, ROLES, coverage_matrix, select_claims, verify_claims
from ..evidence.reporting import render_evidence_citations
from ..rag.retrieval import QueryPlan
from .report_context import audit_request, compact_claims, repair_context, repair_tail_budget, report_task
from ..evidence.reporting import focused_excerpt
from ..evidence.reporting import survey_core_facts
from ..rag.report_selection import own_method_score
from ..evidence.reporting import (repair_paragraphs, apply_report_patches, report_requirements, report_evidence_chunks, fallback_evidence_chunks, resolve_quote_source, model_evidence_rows, remove_unsupported_authors, correct_paper_ids, render_claim_ledger_section, specific_idea_sections, deterministic_survey, validate_citations)


class ReportWorkflow:
    """C/E 证据提取、报告生成、审查与修订。"""
    @contextmanager
    def _stage_time(self, stage):
        previous = getattr(self.llm, 'deadline', None)
        previous_request_cap = getattr(self.llm, 'REQUEST_TIMEOUT_CAP', None)
        previous_thinking = getattr(self.llm, 'thinking_override', None)
        previous_retries = getattr(self.llm, 'RETRY_ATTEMPTS', None)
        limits = {'extract_batch': (55, 70), 'extract': (35, 70),
                  'verify_claims': (22, 55), 'report': (50, 25),
                  'repair': (22, 15), 'audit': (20, 5)}
        function = getattr(self, '_report_function', None)
        if function == 'Idea':
            # E必须留下模型正文及事实复核时间，不能用程序模板补成成功报告。
            limits = {'extract_batch': (40, 90), 'extract': (30, 90),
                      'verify_claims': (20, 70), 'report': (85, 35),
                      'repair': (25, 10), 'audit': (20, 5)}
        direct = getattr(self, '_source_report', False)
        if direct:
            # 首次在线调用就写正文，避免55秒提取超时后没有任何答卷。
            limits.update(report=(135, 25), repair=(32, 10), audit=(22, 8))
            if function == 'Idea':
                # E的八项审查比单纯引文审查长；首稿后留出完整修订窗口。
                limits.update(extract_batch=(32, 110), report=(75, 45), repair=(38, 12), audit=(32, 8))
                if stage == 'report' and self.last_draft:
                    limits['report'] = (60, 12)
        session = current_session()
        if previous is not None and session and function in {'Survey', 'Idea'}:
            cap, reserve = limits.get(stage, (40, 10))
            self.llm.deadline = min(previous, time.monotonic() + cap, session.deadline - reserve)
            if (direct or (function == 'Idea' and stage == 'report')) and previous_request_cap is not None:
                # 否则传输层60秒会提前取消长写作，再用剩余25秒重发整份请求。
                self.llm.REQUEST_TIMEOUT_CAP = cap
        if direct:
            self.llm.thinking_override = 'disabled'
            if previous_retries is not None:
                self.llm.RETRY_ATTEMPTS = 1
        try:
            yield
        finally:
            if previous is not None:
                self.llm.deadline = previous
            if previous_request_cap is not None:
                self.llm.REQUEST_TIMEOUT_CAP = previous_request_cap
            self.llm.thinking_override = previous_thinking
            if previous_retries is not None:
                self.llm.RETRY_ATTEMPTS = previous_retries

    def _model_step(self, stage, data, maximum, *, reserve=0, minimum=500):
        checkpoint("report_" + stage)
        with self._stage_time(stage):
            return self._budgeted_model_step(stage, data, maximum, reserve=reserve, minimum=minimum)

    def _budgeted_model_step(self, stage, data, maximum, *, reserve=0, minimum=500):
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
        batch_values = None
        if getattr(self.llm, "supports_batched_extraction", False):
            # 官方网关实测逐篇调用无法给180秒内的写作/审查留足时间。
            # 合并有限原文上下文，引用仍逐篇逐片段作连续原文核验。
            batch_values = {}
            entries = list(grouped.items())
            for start in range(0, len(entries), 5):
                batch = entries[start:start + 5]
                documents = []
                for filename, group in batch:
                    selected = sorted(group, key=lambda c: (-(own_method_score(c) >= 2), c.page or 0, c.start))[:7]
                    documents.append({"filename": filename, "chunks": [
                        {"chunk_id": c.chunk_id, "page": c.page, "heading": c.heading,
                         "text": focused_excerpt(c, case.input, 500)} for c in selected]})
                value = self._model_step("extract_batch", {"task": case.input, "papers": documents},
                                         3200 if len(batch) > 2 else 2000, reserve=10000, minimum=900)
                returned = value.get("papers")
                allowed = {name for name, _ in batch}
                if not isinstance(returned, list):
                    raise EvidenceError("batch extraction requires papers array")
                for row in returned:
                    if not isinstance(row, dict) or row.get("filename") not in allowed or row["filename"] in batch_values:
                        raise EvidenceError("invalid/duplicate batch extraction paper")
                    batch_values[row["filename"]] = row
                if not allowed <= batch_values.keys():
                    raise EvidenceError("batch extraction omitted authorized paper")
        for position, (filename, group) in enumerate(grouped.items()):
            # 均匀分配片段上下文，避免一个长章节挤掉实验、表格和局限。
            # Survey 使用完整短片段，避免对已经切好的正文再次裁切，丢掉后半句定义与条件。
            # Idea 保留紧凑证据上下文；长表格仍按连续窗口限制大小。
            weights = [2 if c.page != 1 and own_method_score(c) >= 3 else 1 for c in group]
            budgets = ([min(900, len(c.text)) for c in group] if getattr(case, "function", None) == "Survey" else
                       [max(180, int(3800 * weight / sum(weights))) for weight in weights])
            data = {"task": case.input, "filename": filename,
                    "chunks": [{"chunk_id": c.chunk_id, "page": c.page,
                                "heading": c.heading, "text": focused_excerpt(c, case.input, limit)} for c, limit in zip(group, budgets)]}
            reserve = 17000 + max(0, len(grouped) - position - 1) * 1700
            value = batch_values[filename] if batch_values is not None else self._model_step("extract", data, 2000, reserve=reserve, minimum=900)
            # 由当前论文片段提出补检问题，不依赖固定论文或评分事实清单。
            queries = value.get("missing_queries", [])
            if isinstance(queries, list):
                pending = getattr(self, "evidence_queries", [])
                for query in queries[:2]:
                    if isinstance(query, str) and 3 <= len(query.strip()) <= 180:
                        pair = (filename, query.strip())
                        if pair not in pending:
                            pending.append(pair)
                self.evidence_queries = pending
            returned = value.get("claims", [])
            if not isinstance(returned, list):
                raise EvidenceError("extract claims must be an array")
            for item in returned:
                if not isinstance(item, dict):
                    self.rejected_claims.append({"filename": filename, "reason": "invalid_claim_object"})
                    continue
                chunk = sources.get(item.get("chunk_id"))
                if chunk is not None and chunk.filename != filename:
                    chunk = None
                quote, claim = item.get("quote"), item.get("claim")
                if chunk is None and item.get('chunk_id') not in sources and isinstance(quote, str):
                    # 未知编号只允许用本篇唯一的真实连续摘录恢复，不跨论文搬运证据。
                    matches = [c for c in group if source_quote_matches(quote, c.text)]
                    if len(matches) == 1:
                        chunk = matches[0]
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
                claim = re.sub(r"\s+", " ", claim).strip()
                if (chunk is None or source.chunk_id != chunk.chunk_id) and all(c.chunk_id != source.chunk_id for c in self.selected_chunks):
                    self.selected_chunks.append(source)
                facet = item.get("facet") if item.get("facet") in FACETS else "unspecified"
                role = item.get("role") if item.get("role") in ROLES else "unspecified"
                claims.append(Evidence(f"ev-{len(self.ledger)+len(claims)+1}", claim,
                                       source.filename, source.page, source.chunk_id, quote, facet=facet, role=role))
        return claims


    @tool(name="compose_and_check_report")
    def report(self, case, retriever, plan):
        # 先提取可核验事实，再生成正文；模型草稿最多尝试两次。
        self._report_function = case.function
        chunks = report_evidence_chunks(retriever, plan)
        if not chunks:
            raise EvidenceError("no readable authorized PDFs for report")
        self.selected_chunks = chunks
        if getattr(self.llm, 'supports_source_reports', False):
            from .source_reports import source_ledger
            self._source_report = True
            if case.function == 'Survey':
                from .survey_sources import survey_ledger
                self.ledger, self.selected_chunks = survey_ledger(case, retriever, plan, chunks)
            elif case.function == 'Idea':
                from .idea_sources import idea_ledger
                self.ledger, self.selected_chunks = idea_ledger(case, retriever, chunks)
            else:
                self.ledger = source_ledger(case, chunks, retriever.chunks)
            self.coverage = coverage_matrix(self.ledger)
            self.evidence_audit = {'mode': 'authorized_source_context',
                                   'semantic_claims_verified': False}
            return self.compose_report(case)
        self.ledger = self.extract(case, chunks)
        compact = getattr(self.llm, "supports_batched_extraction", False)
        if getattr(self.llm, "supports_evidence_verification", False) and not compact:
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
        self.ledger = select_claims(self.ledger, maximum_per_paper=3 if compact else (6 if case.function == "Survey" else 4), question=case.input)
        if getattr(self.llm, "supports_evidence_verification", False):
            checkpoint("evidence_semantic_verification")
            with self._stage_time('verify_claims'):
                self.ledger, self.evidence_audit = verify_claims(self.llm, self.ledger, self.selected_chunks)
        self.coverage = coverage_matrix(self.ledger)
        self.coverage["targeted_queries"] = [{"filename": name, "query": query}
                                               for name, query in getattr(self, "evidence_queries", [])]
        return self.compose_report(case)

    @tool(name="write_evidence_backed_report")
    def compose_report(self, case):
        """写作阶段只消费本题已核验台账，可单独定向验证。"""
        self._report_function = case.function
        if case.function == 'Idea' and getattr(self, '_source_report', False):
            from .idea_workflow import compose_idea
            return compose_idea(self, case)
        filenames = sorted({e.filename for e in self.ledger})
        if not filenames or (case.function == "Survey" and len(filenames) < 5):
            raise EvidenceError("insufficient supported papers for report (Survey requires at least five)")
        refs = {name: i+1 for i, name in enumerate(filenames)}
        evidence = [{**asdict(e), "ref": refs[e.filename]} for e in self.ledger]
        model_evidence = model_evidence_rows(evidence)
        direct = getattr(self, '_source_report', False)
        idea_plan = None
        if direct and case.function == 'Idea' and getattr(self.llm, 'supports_source_reports', False):
            from .idea_plan import prepare_plan
            idea_plan = prepare_plan(self, case, model_evidence)
        core = survey_core_facts(self.ledger) if case.function == "Survey" and not direct else []
        anchor, anchor_ids = render_claim_ledger_section(core, refs) if core else ("", [])
        feedback, history, previous_used, repair_targets = [], [], [], []
        deliverable = None
        deliverable_attempt = None
        # E可先修一次格式，再修一次语义；两次语义审查仍不通过就结束。
        # 全流程继续受本题时间和Token预算约束，不能靠重复重试超时。
        for attempt in range(3 if case.function == 'Idea' else 2):
            if sum(item.get('stage') == 'audit' for item in history) >= 2:
                break
            patches = []
            audit_evidence = model_evidence
            try:
                global_limit = any("exceeds" in issue and ("characters" in issue or "citation instances" in issue)
                                   for issue in feedback)
                if direct and any('citation instances' in issue for issue in feedback):
                    global_limit = False
                if attempt and self.last_draft and not global_limit:
                    # 首稿已整篇审查过；只修正出错段，不重复生成和审查整篇事实。
                    paragraphs = repair_targets or repair_paragraphs(self.last_draft, feedback)
                    repair_data = repair_context(self.last_draft, paragraphs, feedback, model_evidence, case=case)
                    if direct and case.function == 'Idea':
                        from .idea_repair import indexed_paragraphs
                        repair_data['paragraphs'] = indexed_paragraphs(paragraphs)
                        if idea_plan is not None:
                            repair_data['design_plan'] = idea_plan
                    # 按预计替换范围与当前全文计算复审成本，不能再用固定 4200。
                    planned_patches = [{"original": p, "replacement": p} for p in paragraphs]
                    next_audit = audit_request(case, self.last_draft, model_evidence, self.ledger,
                                              patches=planned_patches, feedback=feedback,
                                              full_review=not any(h.get("stage") == "audit" for h in history))
                    reserve = self.llm.input_tokens("audit", next_audit) + 1500 if hasattr(self.llm, "input_tokens") else 4200
                    if direct and case.function == 'Survey' and hasattr(self.llm, 'input_tokens'):
                        # Keep the real repair affordable. An optional, pessimistic
                        # next-audit estimate must not prevent correcting known errors.
                        affordable = max(0, self.llm.remaining_tokens - self.llm.input_tokens('repair', repair_data) - 1400)
                        reserve = min(reserve, affordable)
                    if case.function == 'Idea' and hasattr(self.llm, 'input_tokens'):
                        affordable = max(0, self.llm.remaining_tokens - self.llm.input_tokens('repair', repair_data) - 1400)
                        reserve = min(reserve, affordable)
                    repaired = self._model_step("repair", repair_data, 1400, reserve=reserve)
                    if self.report_trace:
                        self.report_trace[-1].setdefault('repair_responses', []).append(repaired)
                    patches = repaired.get("patches", [])
                    if direct and case.function == "Idea":
                        from .idea_repair import resolve_patches
                        patches = resolve_patches(patches, paragraphs)
                    revised = apply_report_patches(self.last_draft, patches)
                    value = {"markdown": revised, "used_evidence_ids": previous_used}
                else:
                    data = {"task": report_task(case),
                            "claims": compact_claims(model_evidence), "papers": refs,
                            "facts_appended_by_program": anchor_ids,
                            "output_limits": ({"model_body_characters": max(800, 4500 - len(anchor) - 4),
                                               "model_citation_instances": max(0, 40 - len(report_citations(anchor))),
                                               "total_body_characters": 4500, "total_citation_instances": 40}
                                              if case.function == "Survey" else {"model_body_characters": 6000}),
                            "coverage_requirements": report_requirements(case.function), "repair_issues": feedback,
                            "evidence_coverage": {name: list(facets) for name, facets in self.coverage.get("papers", {}).items()}}
                    if case.function == 'Idea' or direct:
                        data['output_limits']['target_body_characters'] = 2200
                        data['response_fields'] = {'markdown': '完整报告正文字符串，不能省略或嵌套',
                                                   'used_evidence_ids': '实际使用的证据ID数组'}
                    if idea_plan is not None:
                        data['design_plan'] = idea_plan
                    if case.function == 'Idea':
                        from ..integrations.idea_prompt import REQUIREMENTS
                        data['coverage_requirements'] = REQUIREMENTS
                        data['output_limits']['target_body_characters'] = 2800
                    if direct and case.function == 'Survey':
                        data['output_limits'].update(target_body_characters=2200, model_citation_instances=30)
                    # 哈希、偏移等核验元数据留在磁盘；为审查和一次修订保留实际模型预算。
                    remaining = getattr(self.llm, "budget", 40000) - getattr(self.llm, "total_tokens", 0)
                    requested = min(4200, remaining - len(json.dumps(data, ensure_ascii=False)) // 2 - 2000)
                    if requested < 1600:
                        raise ModelError("insufficient model budget reserved for report")
                    # 先用预计长度的正文计算一次审查和修订闭环的成本。
                    preview = (anchor + "\n\n" if anchor else "") + "待核验正文。" * 260
                    initial_audit = audit_request(case, preview, model_evidence, self.ledger)
                    reserve = (self.llm.input_tokens("audit", initial_audit) + 1000
                               + repair_tail_budget(self.llm, case, preview, model_evidence, self.ledger)
                               if hasattr(self.llm, "input_tokens") else 7500)
                    if direct:
                        # 原文上下文只发一次；可选自审不能挤掉完整正文预算。
                        reserve = 3500
                    if hasattr(self.llm, "input_tokens"):
                        # 当前写作和首次审查必须能执行；尚未发生的修订预估不能占满输出空间。
                        available = max(0, self.llm.remaining_tokens - self.llm.input_tokens("report", data)
                                        - (3000 if case.function == "Survey" else 4200))
                        reserve = min(reserve, available)
                    value = self._model_step("report", data, min((3600 if direct else 3000) if case.function == "Survey" else 4200, requested),
                                             reserve=reserve, minimum=1600)
            except (ModelError, EvidenceError, TimeoutError) as exc:
                history.append({"attempt": attempt + 1, "stage": "report", "issues": [str(exc)]})
                feedback = feedback + [str(exc)]
                session = current_session()
                # 首次正文JSON截断/格式错误可在原有两次写作上限内重新生成。
                # 网络超时、额度拒绝不重试；不得用占位正文伪装成功。
                retry_format = isinstance(exc, ModelError) and any(message in str(exc) for message in
                    ('model returned invalid JSON', 'model response incomplete'))
                if (direct and case.function == 'Survey' and attempt == 0 and deliverable is None
                        and retry_format and (session is None or session.remaining() >= 45)):
                    self.last_draft = ''
                    continue
                break
            markdown = value.get("markdown", "")
            used = value.get("used_evidence_ids", [])
            self.report_trace.append({"attempt": attempt + 1, "raw_markdown": markdown, "raw_response": value,
                                      "used_evidence_ids": used, "patches": patches,
                                      "source_task": report_task(case)})
            if (not isinstance(markdown, str) or not markdown.strip() or not isinstance(used, list)
                    or any(not isinstance(identifier, str) for identifier in used)):
                feedback = ['invalid report response: markdown must be a nonempty string; '
                            'used_evidence_ids must be an array of evidence ID strings']
                history.append({"attempt": attempt + 1, "stage": "response_schema", "issues": list(feedback)})
                self.last_draft = ''
                continue
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
            if case.function == "Survey":
                if anchor and not attempt and not markdown.startswith(anchor):
                    markdown = anchor + "\n\n" + markdown
                    used = list(dict.fromkeys(anchor_ids + used))
                # 只补正文尚未引用的论文；不重复整份事实摘要扩大复审输入。
                cited_refs = {int(n) for n in re.findall(r"\[(\d{1,2})(?:,\s*p\.\s*\d+)?\]", markdown)}
                missing_rows = [e for e in self.ledger if refs[e.filename] not in cited_refs]
                if missing_rows and not direct:
                    anchor, anchor_ids = render_claim_ledger_section(select_claims(missing_rows, maximum_per_paper=2), refs)
                    markdown = anchor + "\n\n" + markdown
                    used = list(dict.fromkeys(anchor_ids + used))
            # 作者姓名若不在已核验事实中，不把模型猜测写成论文归因。
            markdown = remove_unsupported_authors(markdown, evidence)
            self.last_draft = markdown
            previous_used = list(used)
            try:
                try:
                    validate_report_limits(markdown + "\n\n", case.function)
                except ValueError as exc:
                    passages = []
                    if direct and 'citation instances' in str(exc):
                        passages = sorted(markdown.split('\n\n'), key=lambda p: len(report_citations(p)), reverse=True)[:2]
                    raise EvidenceError(str(exc), passages=passages) from exc
                numeric_ambiguities = validate_citations(markdown, evidence, used,
                                                        minimum=5 if case.function == "Survey" else 1, function=case.function,
                                                        strict_facts=not direct)
                # 语义自审属于质量控制。先记录确已通过硬检查的完整模型稿，
                # 不能把自审疑虑或局部修订失败误报成没有可交付的答卷。
                if case.function == 'Idea' or direct:
                    deliverable = markdown
                    deliverable_attempt = attempt + 1
                session = current_session()
                if direct and session and session.remaining() < 30:
                    feedback = ['semantic review skipped: preserving submission deadline']
                    break
                audit_data = audit_request(case, markdown, model_evidence, self.ledger,
                                           used=used, patches=patches, feedback=feedback,
                                           full_review=bool(patches) and not any(h.get("stage") == "audit" for h in history))
                audit_data["numeric_claims_to_classify"] = numeric_ambiguities
                reserve = repair_tail_budget(self.llm, case, markdown, model_evidence, self.ledger) if not patches else 0
                if not patches and hasattr(self.llm, "input_tokens"):
                    # 优先完成当前真实审查。后续修订尚未发生，不能让其最坏估算挡住首审。
                    # 修订请求发出前仍会按实际问题、替换范围重新核算完整复审成本。
                    affordable = max(0, self.llm.remaining_tokens - self.llm.input_tokens("audit", audit_data) - 1000)
                    reserve = min(reserve, affordable)
                audit = self._model_step("audit", audit_data, 1500 if case.function == "Idea" else 1000, reserve=reserve)
                if (type(audit.get("ok")) is not bool or not isinstance(audit.get("issues"), list)
                        or any(not isinstance(issue, str) for issue in audit["issues"])):
                    raise EvidenceError("invalid semantic audit response")
                history.append({"attempt": attempt + 1, "stage": "audit", "audit": dict(audit)})
                if direct and case.function == 'Idea':
                    from ..integrations.idea_prompt import audit_gaps
                    gaps = audit_gaps(audit, markdown)
                    if gaps:
                        audit = {**audit, 'reported_ok': audit['ok'], 'ok': False,
                                 'issues': list(audit['issues']) + gaps}
                if audit["ok"] and audit["issues"]:
                    # 保留模型原始结论，但不能把仍有问题的正文当成通过。
                    audit = {**audit, "reported_ok": True, "ok": False}
                audit["history"] = list(history)
                self.audit = audit
                if audit.get("ok") is not True:
                    issues = audit["issues"] or ["semantic audit rejected without details"]
                    raise EvidenceError("; ".join(issues), passages=repair_paragraphs(markdown, issues))
                return self._report_result(case, markdown, filenames, refs)
            except (ModelError, TimeoutError) as exc:
                # 额度或网络问题不是正文错误，禁止把它当成修订意见再调用写作模型。
                feedback = [str(exc)]
                history.append({"attempt": attempt + 1, "stage": "audit_unavailable", "issues": list(feedback)})
                break
            except EvidenceError as exc:
                feedback = [str(exc)]
                repair_targets = getattr(exc, "passages", [])
                history.append({"attempt": attempt + 1, "stage": "validation", "issues": list(feedback)})
        if case.function == "Survey" and not direct:
            # 自由长文多次审查失败时，只拼接已经进入证据台账的事实。
            markdown, used = deterministic_survey(case.input, self.ledger)
            body = markdown.split("## 参考文献", 1)[0]
            validate_report_limits(markdown, case.function)
            validate_citations(body, evidence, used, minimum=5)
            self.audit = {"ok": None, "mode": "deterministic_fallback", "local_validation": True,
                          "model_issues": feedback, "history": history}
            return {"case_id": case.case_id,
                    "path": f"data/expert/{case.case_id}-综述-{self.team}.md", "markdown": markdown}
        if deliverable is not None:
            # 不改正文、不伪造审查通过；供平台实际判内容分，全部疑虑留存证据档案。
            self.audit = {"ok": False, "mode": "model_report_needs_review", "local_validation": True,
                          "delivered_attempt": deliverable_attempt, "model_issues": feedback, "history": history}
            return self._report_result(case, deliverable, filenames, refs)
        # c1-output-limits-v1 禁止用程序模板替代未生成或格式不合格的 E 报告。
        self.audit = {"ok": False, "mode": "model_report_failed", "model_issues": feedback, "history": history}
        raise EvidenceError(case.function + " model report failed validation: " + "; ".join(feedback))

    def _report_result(self, case, markdown, filenames, refs):
        selected_refs = {int(x) for x in report_citations(markdown)}
        markdown += "\n\n## 参考文献\n\n" + "\n".join(
            f"[{refs[name]}] {name}" for name in filenames if refs[name] in selected_refs)
        kind = "综述" if case.function == "Survey" else "灵感Idea报告"
        return {"case_id": case.case_id, "path": f"data/expert/{case.case_id}-{kind}-{self.team}.md", "markdown": markdown}

    def _recover_evidence_gaps(self, case, retriever, plan):
        """优先补检模型从本题提出的具体问题，再补通用维度；有预算才执行。"""
        if not hasattr(retriever, "search"):
            return
        session = current_session()
        # 官方网关实测：逐篇提取后还需核验、写作和审查。补检不能占用这段时间，
        # 否则即使模型已写完，也会在超限修订阶段超过平台的180秒上限。
        if session and session.remaining() < 130:
            return
        if getattr(self.llm, "remaining_tokens", 40000) < (30000 if case.function == "Survey" else 25000):
            return
        queries = {"method": "method mechanism approach 方法 机制",
                   "result": "experiment results evaluation 实验 结果",
                   "limitation": "limitations assumptions failure 局限 假设"}
        gaps = coverage_matrix(self.ledger)["gaps"]
        seen = {c.chunk_id for c in self.selected_chunks}
        supplemental, documents = [], set()
        requests = list(getattr(self, "evidence_queries", []))
        requests += [(gap["filename"], queries[gap["facet"]] + " " + plan.topic)
                     for gap in gaps if gap["facet"] in queries]
        for name, query in requests:
            if name not in documents and len(documents) >= 3:
                continue
            if sum(c.filename == name for c in supplemental) >= 2:
                continue
            hits = retriever.search(QueryPlan(query), filename=name, top_k=12)
            selected = next((h.chunk for h in hits if h.chunk.chunk_id not in seen
                             and h.chunk.kind != "references" and len(h.chunk.text.strip()) >= 80), None)
            if selected:
                supplemental.append(selected)
                seen.add(selected.chunk_id)
                documents.add(name)
            if len(supplemental) >= (4 if case.function == "Survey" else 2):
                break
        if supplemental:
            self.selected_chunks.extend(supplemental)
            try:
                self.ledger.extend(self.extract(case, supplemental))
            except ModelError as exc:
                self.rejected_claims.append({"reason": "gap_recovery_budget", "message": str(exc)})
