from dataclasses import replace

import pytest

from research_agent.core.models import CaseContext
from research_agent.evidence import EvidenceError
from research_agent.rag.retrieval import HybridRetriever
from research_agent.workflows.d_selftest import test_plan_result as parse_test_plan
from research_agent.workflows.tasks import TaskHandlers
from fixtures import case
from test_b5_conditions import chunk, task
from test_db206 import audit_response, reply
from test_b6_retrieval import ScreeningModel, response
from test_e4_resilience import BODY, good_audit, run


DEPTH_BODY = (
    '## 问题本质拆解\n\n' + '拟检验同一测量任务中，混合来源引入的条件变化是否导致结论不稳定。' * 8
    + '[E:ev-1]\n\n## 现有研究与方案的局限\n\n'
    + '以只在一种控制条件下比较方法为对照，跨条件迁移的影响列为待验证问题。' * 8
    + '\n\n## 核心创新与技术路径\n\n'
    + '增加跨条件配对接口，接收条件编号和同源测量，输出配对差异与不确定性。' * 8
    + '\n\n## 落地可行性与预期价值\n\n'
    + '拟以相同样本和预算做消融，报告失效条件、估计区间及超过预算时的停止规则。' * 8
)


@pytest.mark.parametrize('fault', ['missing', 'one_dimension', 'wrong_source', 'none'])
def test_b02_final_review_owns_admission(fault):
    def respond(data):
        rows = []
        for candidate in data['candidates']:
            if not data.get('verification'):
                rows.append(response(candidate))
                continue
            if fault == 'missing':
                continue
            row = audit_response(candidate)
            evidence = row['requirements'][0]['evidence_ids'][0]
            row['match_dimensions'] = {'object': [evidence]}
            if fault != 'one_dimension':
                row['match_dimensions']['method'] = ['unrelated-id' if fault == 'wrong_source' else evidence]
            rows.append(row)
        return {'papers': rows}

    workflow = TaskHandlers(ScreeningModel(respond), 'test')
    workflow.document_expander = None
    request = replace(task(), case_id='B-02')
    result = workflow.handle(request, HybridRetriever([chunk(
        'study.pdf', 'We measure urban cooling using satellite remote sensing in an original experiment.'
    )]))
    assert [p['filename'] for p in result['papers']] == (['study.pdf'] if fault == 'none' else [])


def test_d_duplicate_inputs_cannot_inflate_or_contradict_coverage():
    materials = {'method': 'Return the requested value unchanged for each legal input.'}
    row = {'name': 'base', 'material_id': 'method:1', 'derivation': 'Copy value',
           'request': {'phase': 'base', 'value': 1}, 'expected': 1}
    repeated = {**row, 'name': 'duplicate', 'request': {'value': 1, 'phase': 'base'}}
    other = {**row, 'name': 'other', 'request': {'phase': 'other', 'value': 1}}
    duplicate = parse_test_plan(reply({'tests': [row, repeated, other]}), materials)
    assert [t['name'] for t in duplicate['tests']] == ['base', 'other']
    assert duplicate['rejected'] == ['duplicate_request']
    conflict = parse_test_plan(reply({'tests': [row, {**repeated, 'expected': 2}, row, other]}), materials)
    assert [t['name'] for t in conflict['tests']] == ['other']
    assert conflict['rejected'] == ['conflicting_oracle', 'conflicting_oracle']


def test_e04_rebuilds_incomplete_structure_before_review():
    workflow, model = run([{'markdown': BODY}, {'markdown': DEPTH_BODY}, good_audit()])
    result = workflow.compose_report(CaseContext.from_dict(case('E-04')))
    assert '## 核心创新与技术路径' in result['markdown']
    assert model.stages == ['report', 'report', 'audit']


def test_e04_missing_semantic_approval_prevents_release():
    from research_agent.integrations.llm import ModelError

    workflow, model = run([{'markdown': DEPTH_BODY}, ModelError('audit transport timeout')])
    with pytest.raises(EvidenceError, match='E-04'):
        workflow.compose_report(CaseContext.from_dict(case('E-04')))
    assert model.stages == ['report', 'audit']
