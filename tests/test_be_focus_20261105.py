from dataclasses import replace

import pytest

from fixtures import case
from research_agent.core.models import CaseContext
from research_agent.core.session import CaseSession
from research_agent.workflows.b_audit import paper_context, scope_conflict, scope_profile, assess_audit, source_quote
from research_agent.workflows.idea_workflow import prepare_depth_plan, call_window
from research_agent.evidence import EvidenceError
from test_b5_conditions import chunk
from test_e4_resilience import BODY, good_audit, run


def mechanism_plan():
    return {
        'source_facts': [{'evidence_id': 'ev-1', 'quote': 'We compare controlled experimental conditions.',
                          'meaning': '原材料描述受控实验。'}],
        'hypothesis': '待验证假设。', 'design': '一项可执行机制。', 'experiment': '同输入配对对照。',
        'mechanism_contract': dict.fromkeys(
            ('shared_conditions', 'target_event', 'dataflow', 'fallback', 'controls', 'measurements', 'decision'),
            '同一问题、相同预算，记录中间量并用配对实验检验。',
        ),
    }


@pytest.mark.parametrize('fault', ['none', 'quote', 'contract'])
def test_e04_plan_requires_source_and_complete_contract(fault):
    plan = mechanism_plan()
    if fault == 'quote':
        plan['source_facts'][0]['quote'] = 'This sentence never occurred in the supplied paper.'
    if fault == 'contract':
        del plan['mechanism_contract']['controls']
    workflow, model = run([plan])
    with CaseSession('E-04', 850).activate():
        accepted = prepare_depth_plan(workflow, CaseContext.from_dict(case('E-04')), [])
    assert (accepted is not None) is (fault == 'none')
    assert model.stages == ['extract_batch']
    if fault == 'none':
        assert workflow.evidence_audit['idea_plan']['semantic_claims_verified'] is False


def test_e04_planning_reasoning_is_scoped_and_restored():
    from types import SimpleNamespace
    model = SimpleNamespace(thinking_override='disabled')
    with CaseSession('E-04', 850).activate() as session:
        with call_window(model, 'extract_batch'):
            assert model.thinking_override == 'enabled'
            assert model.deadline < session.deadline
    assert vars(model) == {'thinking_override': 'disabled'}
    with call_window(model, 'report'):
        assert model.thinking_override == 'disabled'
    assert vars(model) == {'thinking_override': 'disabled'}


def test_e04_writer_receives_verified_plan_without_calling_it_proven():
    plan = mechanism_plan()

    def write(model, stage, data):
        assert stage == 'report' and data['design_plan'] == plan
        assert data['output_limits']['model_body_characters'] == 4500
        return {'markdown': BODY}

    workflow, model = run([plan, write, good_audit()])
    with CaseSession('E-04', 850).activate():
        result = workflow.compose_report(CaseContext.from_dict(case('E-04')))
    assert result['markdown'] and workflow.audit['ok']
    assert model.stages == ['extract_batch', 'report', 'audit']


@pytest.mark.parametrize('case_id,stage,enabled', [
    ('E-04', 'audit', True), ('E-01', 'audit', False), ('E-04', 'report', True),
    ('E-04', 'repair', True), ('E-01', 'repair', False),
])
def test_reasoned_semantic_stages_are_e04_only_and_restore_on_failure(case_id, stage, enabled):
    from types import SimpleNamespace
    from research_agent.workflows.idea_workflow import call
    model = SimpleNamespace(thinking_override='disabled')

    def fail(actual_stage, data, maximum, **kwargs):
        assert actual_stage == stage
        assert (model.thinking_override == 'enabled') is enabled
        assert maximum == (32768 if enabled else 1600)
        raise RuntimeError('injected model failure')

    workflow = SimpleNamespace(llm=model, _budgeted_model_step=fail)
    with CaseSession(case_id, 850).activate():
        with pytest.raises(RuntimeError, match='injected'):
            call(workflow, stage, {'task': {'case_id': case_id}}, 1600)
    assert vars(model) == {'thinking_override': 'disabled'}


@pytest.mark.parametrize('case_id,challenge,enabled', [
    ('B-02', True, True), ('B-02', False, False), ('B-01', True, False),
])
def test_b02_counterevidence_reasoning_is_local_and_restored(case_id, challenge, enabled):
    from types import SimpleNamespace
    from research_agent.workflows.b_workflow import call
    model = SimpleNamespace(thinking_override='disabled')

    def fail(stage, data, max_tokens):
        assert (model.thinking_override == 'enabled') is enabled
        assert max_tokens == (16000 if enabled else 6000)
        raise RuntimeError('injected transport failure')

    model.json = fail
    before = vars(model).copy()
    with CaseSession(case_id, 530).activate():
        with pytest.raises(RuntimeError, match='injected'):
            call(model, 'retrieve', {'task': {'case_id': case_id},
                 'counterevidence_review': challenge}, 6000)
    assert vars(model) == before


def test_b02_requirement_segments_keep_negation_and_antecedent_together():
    from research_agent.workflows.b_audit import task_segments
    task = {'case_id': 'B-02', 'input':
            '仅使用既有模块给其他任务供输入，不满足目标一；目标二独立成立，不要求同时满足目标一。'}
    assert [r['text'] for r in task_segments(task)] == [
        '仅使用既有模块给其他任务供输入，不满足目标一；',
        '目标二独立成立，不要求同时满足目标一。',
    ]


def test_e04_long_plan_excerpt_still_requires_full_source_match():
    from dataclasses import replace
    passage = 'This continuous supplied excerpt describes the actual retrieval mechanism. ' * 4
    plan = mechanism_plan()
    plan['source_facts'][0]['quote'] = passage
    workflow, model = run([plan])
    workflow.ledger[0] = replace(workflow.ledger[0], quote=passage, claim=passage)
    with CaseSession('E-04', 850).activate():
        assert prepare_depth_plan(workflow, CaseContext.from_dict(case('E-04')), []) == plan


def test_e04_final_local_repair_requires_fresh_semantic_acceptance():
    from research_agent.integrations.llm import ModelError
    rejected = {**good_audit(), 'ok': False, 'issues': ['需补训练和冻结划分。']}
    workflow, model = run([
        ModelError('optional plan unavailable'), {'markdown': BODY}, rejected, {'patches': []},
        {'markdown': BODY}, rejected,
        {'patches': [{'paragraph_id': 'p1', 'replacement': '拟议训练与校准后冻结。' + BODY}]},
        good_audit(),
    ])
    with CaseSession('E-04', 850).activate():
        result = workflow.compose_report(CaseContext.from_dict(case('E-04')))
    assert workflow.audit['ok'] and '拟议训练与校准后冻结。' in result['markdown']
    assert workflow.audit['history'][-1]['stage'] == 'depth_final_audit'


@pytest.mark.parametrize('accepted', [True, False])
def test_e04_additional_repairs_are_bounded_and_cannot_promote_unapproved_text(accepted):
    from research_agent.integrations.llm import ModelError
    rejected = {**good_audit(), 'ok': False, 'issues': ['统计判据跨节不一致。']}
    responses = [ModelError('plan unavailable'), {'markdown': BODY}, rejected,
                 {'patches': []}, {'markdown': '重新生成。' + BODY}, rejected]
    for number in range(1, 4):
        responses += [{'patches': [{'paragraph_id': 'p1', 'replacement': f'修订{number}。' + BODY}]},
                      good_audit() if accepted and number == 2 else rejected]
        if accepted and number == 2:
            break
    workflow, model = run(responses)
    with CaseSession('E-04', 850).activate():
        result = workflow.compose_report(CaseContext.from_dict(case('E-04')))
    reviews = [r for r in workflow.audit['history'] if r['stage'] == 'depth_final_audit']
    assert len(reviews) == (2 if accepted else 3)
    assert workflow.audit['ok'] is accepted
    assert ('修订2。' in result['markdown']) is accepted
    assert '修订3。' not in result['markdown']


@pytest.mark.parametrize('text,valid', [
    ('概率 p_c∈[0,1]，映射后 q∈ [−0.5, 1e0]。', True),
    ('错误引文[0,1]。', False),
    ('无来源引文[E:ev-999]。', False),
])
def test_e04_numeric_membership_preserves_values_without_weakening_citations(text, valid):
    from dataclasses import asdict
    from research_agent.workflows.idea_workflow import normalize
    workflow, _ = run([])
    evidence = [dict(asdict(workflow.ledger[0]), ref=1)]
    value = {'markdown': BODY + text}
    if not valid:
        with pytest.raises(EvidenceError):
            normalize(value, evidence, maximum_body=4500, math_intervals=True)
    else:
        body, used, _ = normalize(value, evidence, maximum_body=4500, math_intervals=True)
        assert 'p_c∈［0,1］' in body and 'q∈ ［−0.5, 1e0］' in body
        assert '[1, p.1]' in body and used == ['ev-1']


@pytest.mark.parametrize('case_id,fault,retried', [
    ('E-04', 'length', True), ('E-04', 'transport', False),
    ('E-04', 'quota', False), ('E-01', 'length', False), ('E-04', 'malformed', False),
])
def test_audit_retry_requires_recorded_truncation_and_never_retries_quota(case_id, fault, retried):
    from dataclasses import asdict
    from research_agent.integrations.llm import ModelError, ModelQuotaError
    from research_agent.workflows.idea_workflow import review_draft

    def failure(model, stage, data):
        if fault != 'transport':
            model.response_failures.append({'stage': stage, 'choices':
                None if fault == 'malformed' else [{'finish_reason': 'length'}]})
        raise ModelQuotaError('quota') if fault == 'quota' else ModelError('injected incomplete audit')

    workflow, model = run([failure, good_audit()])
    rows = [dict(asdict(workflow.ledger[0]), ref=1)]
    with CaseSession(case_id, 850).activate():
        if retried:
            result, issues = review_draft(workflow, CaseContext.from_dict(case(case_id)), BODY, rows, ['ev-1'], [])
            assert result['ok'] and not issues
        else:
            with pytest.raises(ModelError):
                review_draft(workflow, CaseContext.from_dict(case(case_id)), BODY, rows, ['ev-1'], [])
    assert model.stages == ['audit'] * (2 if retried else 1)


@pytest.mark.parametrize('evidence_id,valid', [('ev-1', True), ('ev-999', False)])
def test_e04_fullwidth_citation_retains_source_validation(evidence_id, valid):
    workflow, model = run([
        mechanism_plan(), {'markdown': BODY.replace('[E:ev-1]', '［E:' + evidence_id + '］')},
        good_audit() if valid else {'markdown': 'invalid'},
        {'markdown': 'invalid'},
    ])
    with CaseSession('E-04', 850).activate():
        if valid:
            result = workflow.compose_report(CaseContext.from_dict(case('E-04')))
            assert '[1, p.1]' in result['markdown']
        else:
            with pytest.raises(EvidenceError):
                workflow.compose_report(CaseContext.from_dict(case('E-04')))


def test_b_context_recovers_paper_contribution_outside_query_window():
    from types import SimpleNamespace
    entries = [chunk('reader.pdf', 'References to dense retrieval.', id='front', page=1),
               replace(chunk('reader.pdf', 'Technical detail about the adopted retriever.', id='detail', page=2), start=40),
               chunk('reader.pdf', 'We propose a generative reader that combines multiple passages.', id='contribution', page=4)]
    context = paper_context(SimpleNamespace(chunks=entries), 'reader.pdf')
    assert any('generative reader' in row['text'] for row in context)
    assert all(row['chunk_id'] in {c.chunk_id for c in entries} for row in context)


def test_b_scope_quotes_accept_pdf_layout_but_not_invented_words():
    candidate = {'chunks': [], 'paper_context': [
        {'chunk_id': 'abstract', 'text': 'We introduce a generative\nreader for open domain answering.'}]}
    assert source_quote({'chunk_id': 'abstract', 'quote': 'We introduce a generative reader for open domain answering.'}, candidate)
    assert not source_quote({'chunk_id': 'abstract', 'quote': 'We introduce a dense retriever for open domain answering.'}, candidate)


def test_b02_independent_target_unknown_does_not_veto_grounded_target():
    task = {'case_id': 'B-02', 'title': '', 'input': 'dense retrieval; reflection generation', 'constraints': []}
    plan = {
        'targets': [{'id': 't1', 'source_quote': 'dense retrieval'},
                    {'id': 't2', 'source_quote': 'reflection generation'}],
        'criteria': [{'scope': 't1', 'source_quote': 'dense retrieval'},
                     {'scope': 't2', 'source_quote': 'reflection generation'}],
    }
    candidate = {'filename': 'dense.pdf', 'chunks': [
        {'chunk_id': 'method', 'page': 1, 'text': 'We introduce a dense passage retriever.'}]}
    row = {'filename': 'dense.pdf', 'status': 'relevant', 'reason': '核心研究为稠密段落检索。',
           'evidence_ids': ['method'], 'targets': ['t1'],
           'requirements': [
               {'task_id': 'q1', 'verdict': 'met', 'evidence_ids': ['method']},
               {'task_id': 'q2', 'verdict': 'unknown', 'evidence_ids': []}],
           'match_dimensions': {'object': ['method'], 'method': ['method']}}
    accepted = assess_audit(row, candidate, task, plan)
    assert accepted['accepted'] and len(accepted['non_applicable_requirements']) == 1


@pytest.mark.parametrize('remaining', ['met', 'unknown', 'not_met', 'malformed'])
def test_b_scope_rejection_needs_all_or_targets_and_real_source(remaining):
    sentence = 'This study introduces a generative reader over retrieved evidence.'
    candidate = {'filename': 'reader.pdf', 'chunks': [],
                 'paper_context': [{'chunk_id': 'abstract', 'text': sentence, 'page': 1}]}
    evidence = {'summary': '生成阅读器研究。', 'chunk_id': 'abstract', 'quote': sentence}
    profile = scope_profile({'object': evidence, 'method': evidence}, candidate)
    checks = [{'target_id': 't1', 'task_id': 'q1', 'verdict': 'not_met',
               'chunk_id': 'abstract', 'quote': sentence, 'reason': '不满足第一目标。'},
              {'target_id': 't2', 'task_id': 'q2', 'verdict': remaining,
               'chunk_id': 'abstract', 'quote': sentence, 'reason': '逐项核查第二目标。'}]
    if remaining == 'malformed':
        checks[1]['target_id'] = ['t2']
    assert scope_conflict({'target_checks': checks}, profile, candidate, {'t1', 't2'}, {'q1', 'q2'}) is (remaining == 'not_met')
