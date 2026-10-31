"""长文审查上下文与后续预算；核验数据留在本题，不重复发送整份台账。"""
from __future__ import annotations

import re


def report_task(case):
    """保留官方八字段题目，写作、审查和修订不能丢失题面要求。"""
    return {name: getattr(case, name, [] if name in {'materials', 'constraints'} else '')
            for name in ('case_id', 'function', 'title', 'input', 'materials',
                         'constraints', 'expected_output', 'output_schema_ref')}


def compact_claims(rows):
    """文件名在单独映射中传递，事实行不重复核验元数据。"""
    keys = ("evidence_id", "claim", "ref", "page", "role", "verification")
    return [{key: row[key] for key in keys if key in row} for row in rows]


def cited_evidence(markdown, rows):
    """按编号及物理页定位事实；没有页码时保留该论文全部候选。"""
    pairs = {(int(ref), int(page) if page else None)
             for ref, page in re.findall(r"\[(\d{1,2})(?:,\s*p\.\s*(\d+))?\]", markdown)}
    ids = set(re.findall(r"ev-\d+", markdown))
    return [e for e in rows if e["evidence_id"] in ids or (e["ref"], e["page"]) in pairs
            or (e["ref"], None) in pairs]


def revision_view(markdown, patches):
    """未改正文已首审：复审发送变更附近原文及完整标题结构，明确标记为节选。"""
    paragraphs = markdown.split("\n\n")
    clean = lambda text: re.sub(r"\[(?:E:[^\]]+|\d{1,2}(?:,\s*p\.\s*\d+)?)\]", "", text).strip()
    changed = [clean(p["replacement"]) for p in patches if clean(p["replacement"])]
    indices = {i for i, p in enumerate(paragraphs) if any(text in clean(p) for text in changed)}
    if not indices:
        # 删除操作没有替换文本，交付全篇；实际请求前仍会按真实大小检查预算。
        return markdown, "complete_document"
    selected = {j for i in indices for j in (i - 1, i, i + 1) if 0 <= j < len(paragraphs)}
    outline = "\n".join(line for line in markdown.splitlines() if re.match(r"^#{1,6}\s", line))
    context = [p if i in indices or len(p) <= 900 else p[:600] + "\n（未改段落节选）\n" + p[-200:]
               for i, p in enumerate(paragraphs) if i in selected]
    return "标题结构：\n" + outline + "\n\n变更附近原文：\n" + "\n\n".join(context), "changed_paragraphs_and_neighbors"


def audit_request(case, markdown, rows, ledger, *, used=(), patches=(), feedback=(), full_review=False):
    """首轮审查全篇；复审保留全篇一次，事实证据限定为变更和原问题涉及项。"""
    from ..evidence.reporting import report_requirements

    changed = "\n\n".join(p["replacement"] for p in patches)
    partial = bool(patches) and not full_review
    context, view = revision_view(markdown, patches) if partial else (None, "complete_document")
    if partial:
        # 审查能看到相邻原文，相邻段落的引用也必须提供证据；不能只给替换句证据。
        relevant = "\n".join([changed, context or '', *feedback, *(p["original"] for p in patches)])
        selected = cited_evidence(relevant, rows) or rows
    else:
        # used 必须包含正文真正引用的证据；同时按正文引用回查，避免模型漏报。
        selected_ids = set(used) | {e["evidence_id"] for e in cited_evidence(markdown, rows)}
        selected = [e for e in rows if e["evidence_id"] in selected_ids] or rows
    known = {e["evidence_id"] for e in selected}
    result = {
        "task": report_task(case),
        "markdown": changed if partial else markdown,
        "full_document": context, "document_view": view,
        "previous_issues": list(feedback) if partial else [], "applied_patches": list(patches) if partial else [],
        "scope": "核查变更、原问题以及全篇要求" if partial else "整篇审查；预检查修订不能代替首次事实审查",
        "coverage_requirements": report_requirements(case.function),
        # 事实与原文合并成一份，避免 claims/source_quotes 重复发送编号及上下文。
        "source_quotes": [{**row, "quote": next(e.quote for e in ledger if e.evidence_id == row["evidence_id"])}
                          for row in compact_claims(selected) if row["evidence_id"] in known],
        "papers": {str(e["ref"]): e["filename"] for e in selected},
    }

    if case.function == 'Survey':
        # Direct source excerpts formerly appeared twice (claim == quote),
        # inflating audit/repair reservations until valid corrections could not run.
        for row in result['source_quotes']:
            if row.get('role') == 'source_context' and row.get('claim') == row.get('quote'):
                row.pop('claim')
    return result


def repair_context(markdown, paragraphs, feedback, rows, *, case=None):
    """复审需要的证据依据原句和问题定位，不能只看替换段落的关键词。"""
    selected = cited_evidence("\n".join([*paragraphs, *feedback]), rows) or rows
    result = {"paragraphs": paragraphs, "issues": list(feedback), "claims": compact_claims(selected),
              "papers": {str(e["ref"]): e["filename"] for e in selected}}
    if case is not None:
        result['task'] = report_task(case)
    return result


def repair_tail_budget(llm, case, markdown, rows, ledger):
    """预估一次局部修订；实际问题出现后再按真实范围核算，不假定重写长文。"""
    if not hasattr(llm, "input_tokens"):
        return 3200
    paragraphs = sorted((p for p in markdown.split("\n\n") if len(p) <= 1000
                         and 1 <= len({e["ref"] for e in cited_evidence(p, rows)}) <= 2), key=len, reverse=True)[:1]
    if not paragraphs:
        # 草稿未生成或只有跨论文长段时，用一个具体证据模拟局部修订。
        paragraphs = ["待修订事实及条件。" * 25 + f" [{e['ref']}, p.{e['page']}]" for e in rows[:1]]
        markdown += "\n\n" + "\n\n".join(paragraphs)
    issues = ["待修订问题：" + "证据条件或实验配置不完整。" * 18]
    repair = repair_context(markdown, paragraphs, issues, rows, case=case)
    patches = [{"original": p, "replacement": p} for p in paragraphs]
    followup = audit_request(case, markdown, rows, ledger, patches=patches, feedback=issues)
    return llm.input_tokens("repair", repair) + 1400 + llm.input_tokens("audit", followup) + 1000
