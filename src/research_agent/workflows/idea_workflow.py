"""E 专用交付流程：先完整正文，有界恢复，再审查和原子修订。"""
import json
import re
import time
from contextlib import contextmanager
from dataclasses import asdict

from ..core.session import checkpoint, current_session
from ..contracts.validation import validate_report_limits
from ..evidence import EvidenceError
from ..evidence.reporting import (render_evidence_citations, validate_citations,
    model_evidence_rows, apply_report_patches, repair_paragraphs)
from ..integrations.llm import ModelError, ModelQuotaError
from ..integrations.idea_prompt import REQUIREMENTS, audit_gaps
from .report_context import report_task, compact_claims, audit_request, cited_evidence, repair_context
from .idea_repair import indexed_paragraphs, resolve_patches
from .idea_plan import validate_source_plan


def remaining():
    session = current_session()
    if session:
        return session.remaining()
    return float('inf')


@contextmanager
def call_window(llm, stage, *, retry=False, reasoned_audit=False):
    caps = {'extract_batch': (120, 360), 'report': (180, 90) if retry else (240, 180),
            'audit': (60, 30), 'repair': (120, 30)}
    cap, reserve = caps[stage]
    if stage == 'audit' and reasoned_audit:
        cap = 120
    session = current_session()
    names = ('deadline', 'REQUEST_TIMEOUT_CAP', 'RETRY_ATTEMPTS', 'thinking_override')
    saved = {name: (name in vars(llm), getattr(llm, name, None)) for name in names}
    try:
        deadline = time.monotonic() + cap
        if saved['deadline'][1] is not None:
            deadline = min(deadline, saved['deadline'][1])
        if session:
            reserve = min(reserve, max(15, session.remaining() * .2))
            deadline = min(deadline, session.deadline-reserve)
        if deadline <= time.monotonic():
            raise TimeoutError('insufficient Idea stage time')
        llm.deadline = deadline
        llm.REQUEST_TIMEOUT_CAP = cap
        llm.RETRY_ATTEMPTS = 1
        llm.thinking_override = 'enabled' if stage == 'extract_batch' or reasoned_audit else 'disabled'
        checkpoint('idea_'+stage)
        yield
    finally:
        for name, (present, value) in saved.items():
            if present:
                setattr(llm, name, value)
            else:
                vars(llm).pop(name, None)


def call(workflow, stage, data, maximum, *, retry=False):
    reasoned_audit = stage == 'audit' and data.get('task', {}).get('case_id') == 'E-04'
    if reasoned_audit:
        # 推理与JSON共享输出限额；不能沿用只够短审查正文的1600。
        maximum = max(maximum, 12000)
    with call_window(workflow.llm, stage, retry=retry, reasoned_audit=reasoned_audit):
        return workflow._budgeted_model_step(stage, data, maximum, reserve=0, minimum=500)


def complete_markdown(failures):
    """只取JSON中已完整闭合的markdown字符串，绝不补字符或续接截断正文。"""
    for failure in reversed(failures):
        if not isinstance(failure, dict) or failure.get('stage') != 'report':
            continue
        choices = failure.get('choices')
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict) or not isinstance(choice.get('message'), dict):
                continue
            reason = choice.get('finish_reason')
            if reason is not None and reason not in ('stop', 'length'):
                continue
            text = choice['message'].get('content')
            if not isinstance(text, str) or not text.lstrip().startswith('{'):
                continue
            text = text.lstrip(); decoder = json.JSONDecoder(); index = 1; fields = {}
            try:
                while index < len(text):
                    while index < len(text) and text[index].isspace(): index += 1
                    key, index = decoder.raw_decode(text, index)
                    if not isinstance(key, str) or key in fields: return None
                    while index < len(text) and text[index].isspace(): index += 1
                    if text[index:index+1] != ':': break
                    index += 1
                    while index < len(text) and text[index].isspace(): index += 1
                    value, index = decoder.raw_decode(text, index)
                    fields[key] = value
                    while index < len(text) and text[index].isspace(): index += 1
                    if text[index:index+1] != ',': break
                    index += 1
            except (ValueError, IndexError):
                pass
            body = fields.get('markdown')
            if isinstance(body, str) and body.strip():
                return {'markdown': body, 'used_evidence_ids': fields.get('used_evidence_ids', [])}
    return None


def normalize(value, evidence, *, maximum_body=6000):
    if not isinstance(value, dict) or not isinstance(value.get('markdown'), str) or not value['markdown'].strip():
        raise EvidenceError('Idea response needs a complete nonempty markdown string')
    body = value['markdown'].split('## 参考文献', 1)[0].strip()
    body = re.sub(r'［(E:\s*ev-\d+(?:\s*,\s*ev-\d+)*)\s*］', r'[\1]', body)
    body, bound = render_evidence_citations(body, evidence)
    # 内部ID列表不是最终答卷字段；仅按真实正文标记回查已授权证据，不猜新引用。
    used = list(dict.fromkeys(bound + [row['evidence_id'] for row in cited_evidence(body, evidence)]))
    try:
        if len(body+'\n\n') > maximum_body:
            raise ValueError(f'report body exceeds {maximum_body} characters (including whitespace)')
        validate_report_limits(body+'\n\n', 'Idea')
    except ValueError as exc:
        raise EvidenceError(str(exc)) from exc
    ambiguities = validate_citations(body, evidence, used, minimum=1, function='Idea', strict_facts=False)
    return body, used, ambiguities


def retryable(exc):
    if isinstance(exc, ModelQuotaError):
        return False
    if isinstance(exc, (EvidenceError, TypeError, KeyError)):
        return True
    return any(word in str(exc).lower() for word in ('timeout', 'timed out', 'transport failure',
               'invalid json', 'incomplete', 'empty', 'nonempty', 'response needs',
               'invalid choices', 'invalid message', 'json object'))


def prepare_depth_plan(workflow, case, rows):
    """E-04有余时先固定机制链；仅核验出处与结构，不把规划当语义通过。"""
    if case.case_id != 'E-04':
        return None
    if remaining() < 480:
        workflow.evidence_audit['idea_plan'] = {'status':'skipped_remaining_budget'}
        return None
    value = None
    try:
        # 沿用已有idea_plan提示入口，一次有界调用，不改变公共提取/写作路径。
        value = call(workflow, 'extract_batch', {
            'task':report_task(case), 'purpose':'idea_plan', 'claims':compact_claims(rows),
        }, 12000)
        validate_source_plan(value, workflow.ledger, maximum_quote=600)
        contract = value.get('mechanism_contract')
        fields = ('shared_conditions', 'target_event', 'dataflow', 'fallback',
                  'controls', 'measurements', 'decision')
        if (not isinstance(contract, dict) or any(not isinstance(contract.get(key), str)
                or not contract[key].strip() for key in fields)):
            raise EvidenceError('Idea mechanism plan has an incomplete mechanism contract')
        workflow.evidence_audit['idea_plan'] = {
            'status':'source_quotes_checked', 'semantic_claims_verified':False, 'plan':value,
        }
        return value
    except (ModelError, EvidenceError, TimeoutError, ValueError, TypeError, KeyError) as exc:
        workflow.evidence_audit['idea_plan'] = {'status':'unavailable', 'error':str(exc), 'raw_response':value}
        if isinstance(exc, ModelQuotaError):
            raise
        return None


def checked_plan(workflow):
    record = workflow.evidence_audit.get('idea_plan', {})
    return record.get('plan') if record.get('status') == 'source_quotes_checked' else None



def patch_draft(workflow, case, body, feedback, rows):
    paragraphs = repair_paragraphs(body, feedback)
    all_paragraphs = body.split('\n\n')
    targets = [row for row in indexed_paragraphs(all_paragraphs) if row['text'] in paragraphs]
    data = repair_context(body, paragraphs, feedback, rows, case=case)
    data['paragraphs'] = targets
    data['full_document'] = body
    if case.case_id == 'E-04' and checked_plan(workflow) is not None:
        data['design_plan'] = checked_plan(workflow)
    repaired = call(workflow, 'repair', data, 1800)
    if workflow.report_trace:
        workflow.report_trace[-1].setdefault('repair_responses', []).append(repaired)
    if not isinstance(repaired, dict): raise EvidenceError('invalid Idea repair response')
    patches = resolve_patches(repaired.get('patches'), all_paragraphs)
    editable = {row['paragraph_id'] for row in targets}
    if any(row['paragraph_id'] not in editable for row in repaired['patches']):
        raise EvidenceError('Idea repair targets a paragraph outside the editable set')
    return apply_report_patches(body, patches), repaired, patches


def review_draft(workflow, case, body, rows, used, ambiguities):
    data = audit_request(case, body, rows, workflow.ledger, used=used, full_review=True)
    data['numeric_claims_to_classify'] = ambiguities
    audit = call(workflow, 'audit', data, 1600)
    if (not isinstance(audit, dict) or type(audit.get('ok')) is not bool
            or not isinstance(audit.get('issues'), list) or not all(isinstance(x, str) for x in audit['issues'])):
        raise EvidenceError('invalid Idea semantic audit response')
    return audit, list(audit['issues']) + audit_gaps(audit, body)


def compose_idea(workflow, case):
    filenames = sorted({e.filename for e in workflow.ledger})
    if not filenames:
        raise EvidenceError('Idea requires authorized readable source evidence')
    refs = {name: i+1 for i, name in enumerate(filenames)}
    evidence = [dict(asdict(e), ref=refs[e.filename]) for e in workflow.ledger]
    rows = model_evidence_rows(evidence)
    history, feedback = [], []
    body = None
    format_candidate = None
    repair_used = False
    reviewed = False
    body_limit = 4500 if case.case_id == 'E-04' else 6000
    # E-04仅在余量足够时先规划一次；失败仍走原有完整正文恢复，不用规划拼答卷。
    try:
        design_plan = prepare_depth_plan(workflow, case, rows)
    except ModelQuotaError as exc:
        workflow.audit = {'ok':False, 'mode':'model_report_failed', 'history':[
            {'stage':'plan_unavailable', 'issues':[str(exc)]}], 'model_issues':[str(exc)]}
        raise EvidenceError('Idea has no valid complete model report: '+str(exc)) from exc
    attempts = 3 if current_session() and remaining() >= 300 else 2
    for attempt in range(attempts):
        data = {'task': report_task(case), 'claims': compact_claims(rows), 'papers': refs,
                'coverage_requirements': REQUIREMENTS, 'repair_issues': feedback,
                'output_limits': {'minimum_body_characters':800, 'model_body_characters':6000,
                                  'target_body_characters':1700 if attempt else 2100},
                'response_fields': {'markdown':'完整模型正文', 'used_evidence_ids':'实际引用的证据ID数组'}}
        if case.case_id == 'E-04':
            # 留出机制定义和对照篇幅，同时在模型调用与真实正文校验两处保留4500边界。
            data['output_limits'].update(model_body_characters=4500, target_body_characters=2800 if not attempt else 2400)
            if design_plan is not None:
                data['design_plan'] = design_plan
            data['citation_format'] = {
                'syntax': '[E:ev-N]',
                'allowed_ids': [row['evidence_id'] for row in rows],
                'instruction': '引文使用半角方括号，保留完整ev-前缀。全角括号只用于参数区间，不能用于引用。',
            }
        start = len(getattr(workflow.llm, 'response_failures', []))
        value = None
        try:
            if attempt == 1 and format_candidate is not None:
                repair_used = True
                revised, repaired, patches = patch_draft(workflow, case, format_candidate, feedback, rows)
                value = {'markdown':revised}
                workflow.report_trace.append({'attempt':attempt+1,'raw_response':repaired,
                    'raw_markdown':revised,'patches':patches,'stage':'format_repair'})
            else:
                try:
                    value = call(workflow, 'report', data, 3600 if attempt else 4200, retry=bool(attempt))
                except ModelError as exc:
                    if isinstance(exc, ModelQuotaError): raise
                    value = complete_markdown(getattr(workflow.llm, 'response_failures', [])[start:])
                    if value is None: raise
                    history.append({'stage':'complete_field_recovery','attempt':attempt+1,'original_error':str(exc)})
                workflow.report_trace.append({'attempt':attempt+1,'raw_response':value,
                    'raw_markdown':value.get('markdown') if isinstance(value,dict) else None,'source_task':report_task(case)})
            body, used, ambiguities = normalize(value, evidence, maximum_body=body_limit)
            workflow.last_draft = body
            break
        except (ModelError, EvidenceError, TimeoutError, ValueError, TypeError, KeyError) as exc:
            feedback = [str(exc)]
            history.append({'stage':'draft_failed','attempt':attempt+1,'issues':feedback})
            # 完整稿仅数学区间/数字引用格式不合格时，让模型局部改字；程序不代写正文。
            if not attempt and isinstance(exc, EvidenceError) and 'numeric bracket token' in str(exc):
                raw = value.get('markdown') if isinstance(value, dict) else None
                if isinstance(raw, str):
                    raw = raw.split('## 参考文献', 1)[0].strip()
                    try:
                        validate_report_limits(raw+'\n\n', 'Idea')
                        if len(raw+'\n\n') <= body_limit:
                            format_candidate = raw
                    except ValueError:
                        pass
            if attempt == attempts-1 or not retryable(exc) or remaining() < (180 if attempt else 90):
                workflow.audit = {'ok':False,'mode':'model_report_failed','history':history,'model_issues':feedback}
                raise EvidenceError('Idea has no valid complete model report: '+str(exc)) from exc
    if body is None:
        raise EvidenceError('Idea has no complete model report')
    # 任一可交付稿先保存到本题状态。之后的审查/修订不可破坏该稿。
    for review in range(2):
        if remaining() < 35:
            feedback = feedback + ['semantic review unavailable within remaining case budget']
            break
        try:
            audit, feedback = review_draft(workflow, case, body, rows, used, ambiguities)
            reviewed = True
            history.append({'stage':'audit','audit':audit,'attempt':review+1})
            if audit['ok'] and not feedback:
                workflow.audit = {**audit,'history':history,'local_validation':True}
                return workflow._report_result(case, body, filenames, refs)
            feedback = feedback or ['semantic audit rejected without details']
        except (ModelError, EvidenceError, TimeoutError, ValueError, TypeError, KeyError) as exc:
            feedback = [str(exc)]; history.append({'stage':'audit_unavailable','issues':feedback})
            if isinstance(exc, ModelQuotaError):
                reviewed = False
            break
        if review or repair_used or remaining() < 42:
            break
        try:
            repair_used = True
            revised, repaired, patches = patch_draft(workflow, case, body, feedback, rows)
            candidate, candidate_used, candidate_ambiguities = normalize(
                {'markdown':revised}, evidence, maximum_body=body_limit)
            body, used, ambiguities = candidate, candidate_used, candidate_ambiguities
            workflow.last_draft = body
            workflow.report_trace.append({'attempt':review+2,'raw_response':repaired,'raw_markdown':body,'patches':patches})
        except (ModelError, EvidenceError, TimeoutError, ValueError, TypeError, KeyError) as exc:
            history.append({'stage':'repair_unavailable','issues':[str(exc)]})
            if isinstance(exc, ModelQuotaError):
                reviewed = False
            break
    if case.case_id == 'E-04' and reviewed and remaining() >= 220:
        try:
            rebuilt = call(workflow, 'report', {
                **data, 'previous_draft': body, 'repair_issues': feedback,
                'rebuild_instruction': (
                    '重写完整报告，保留经证据支持的文献事实。只比较一项核心机制，固定双方输入、预算、'
                    '数据划分及种子。核对每个中间量的生产者、降级仍可用的输入和可观察的指标分子。'
                    '跨节规则必须一致。以正文和引用完整的新JSON交付。'
                ),
            }, 4200, retry=True)
            rebuilt_body, rebuilt_used, rebuilt_ambiguities = normalize(rebuilt, evidence, maximum_body=body_limit)
            workflow.report_trace.append({'stage':'depth_rebuild','raw_response':rebuilt,'raw_markdown':rebuilt_body})
            rebuilt_audit, rebuilt_feedback = review_draft(
                workflow, case, rebuilt_body, rows, rebuilt_used, rebuilt_ambiguities)
            history.append({'stage':'depth_rebuild_audit','audit':rebuilt_audit,'issues':rebuilt_feedback})
            if rebuilt_audit['ok'] and not rebuilt_feedback:
                workflow.last_draft = rebuilt_body
                workflow.audit = {**rebuilt_audit,'history':history,'local_validation':True}
                return workflow._report_result(case, rebuilt_body, filenames, refs)
            if rebuilt_feedback and remaining() >= 160:
                revised, repaired, patches = patch_draft(
                    workflow, case, rebuilt_body, rebuilt_feedback, rows)
                final_body, final_used, final_ambiguities = normalize(
                    {'markdown': revised}, evidence, maximum_body=body_limit)
                workflow.report_trace.append({'stage':'depth_final_repair','raw_response':repaired,
                                              'raw_markdown':final_body,'patches':patches})
                final_audit, final_feedback = review_draft(
                    workflow, case, final_body, rows, final_used, final_ambiguities)
                history.append({'stage':'depth_final_audit','audit':final_audit,'issues':final_feedback})
                if final_audit['ok'] and not final_feedback:
                    workflow.last_draft = final_body
                    workflow.audit = {**final_audit,'history':history,'local_validation':True}
                    return workflow._report_result(case, final_body, filenames, refs)
        except (ModelError, EvidenceError, TimeoutError, ValueError, TypeError, KeyError) as exc:
            history.append({'stage':'depth_rebuild_unavailable','issues':[str(exc)]})
    workflow.audit = {'ok':False,'mode':'model_report_needs_review','local_validation':True,
                      'model_issues':feedback,'history':history}
    return workflow._report_result(case, body, filenames, refs)
