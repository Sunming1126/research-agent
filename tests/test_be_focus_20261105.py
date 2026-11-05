from dataclasses import replace

import pytest

from fixtures import case
from research_agent.core.models import CaseContext
from research_agent.core.session import CaseSession
from research_agent.workflows.b_audit import paper_context, scope_conflict, scope_profile
from research_agent.workflows.idea_workflow import prepare_depth_plan
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


def test_b_context_recovers_paper_contribution_outside_query_window():
    from types import SimpleNamespace
    entries = [chunk('reader.pdf', 'References to dense retrieval.', id='front', page=1),
               replace(chunk('reader.pdf', 'Technical detail about the adopted retriever.', id='detail', page=2), start=40),
               chunk('reader.pdf', 'We propose a generative reader that combines multiple passages.', id='contribution', page=4)]
    context = paper_context(SimpleNamespace(chunks=entries), 'reader.pdf')
    assert any('generative reader' in row['text'] for row in context)
    assert all(row['chunk_id'] in {c.chunk_id for c in entries} for row in context)


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
