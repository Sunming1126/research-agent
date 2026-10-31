"""Full-report faults must be recoverable before a draft becomes deliverable.

Synthetic responses isolate orchestration; these are not model-quality scores.
"""
import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from fixtures import case
from research_agent.contracts.validation import validate_result
from research_agent.core.models import CaseContext, Evidence
from research_agent.evidence import EvidenceError
from research_agent.workflows.tasks import TaskHandlers


class ContractModel:
    def __init__(self, lengths, audit_timeout=False):
        self.lengths = lengths
        self.audit_timeout = audit_timeout
        self.stages = []
        self.report_calls = 0

    def json(self, stage, data, **kwargs):
        self.stages.append(stage)
        if stage == 'report':
            length = self.lengths[min(self.report_calls, len(self.lengths) - 1)]
            self.report_calls += 1
            prefix = '# 离线软件验证\n\n以下文字仅为故障注入夹具，不代表真实论文结论。\n\n'
            suffix = '\n\n' + ' '.join(f'[{i}, p.1]' for i in range(1, 6))
            fixed = len(re.sub(r'\s', '', prefix + suffix))
            return {'markdown': prefix + '测' * (length - fixed) + suffix,
                    'used_evidence_ids': [f'ev-{i}' for i in range(1, 6)]}
        if stage == 'repair':
            # This valid patch changes no non-whitespace character count.
            return {'patches': [{'original': '# 离线软件验证', 'replacement': '# 离线软件核验'}]}
        if stage == 'audit':
            if self.audit_timeout:
                raise TimeoutError('injected semantic audit timeout')
            return {'ok': True, 'issues': []}
        raise AssertionError('Unexpected model stage: ' + stage)


@pytest.fixture
def capture(request):
    record = {'kind': 'synthetic_no_model_failure_injection', 'test': request.node.name,
              'target_model_calls': 0, 'source': 'injected_fixture_not_platform_response'}
    yield record
    if not os.environ.get('P0_CASE_EVIDENCE_DIR'):
        return
    model = record.pop('_model', None)
    handler = record.pop('_handler', None)
    if model:
        record.update(stages=model.stages, report_calls=model.report_calls)
    if handler:
        record.update(report_trace=handler.report_trace, audit=handler.audit, ledger_size=len(handler.ledger))
    target = Path(os.environ['P0_CASE_EVIDENCE_DIR'])
    target.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[^a-zA-Z0-9_.-]', '_', request.node.name) + '.json'
    (target / name).write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def setup(kind, lengths, capture, *, audit_timeout=False):
    model = ContractModel(lengths, audit_timeout)
    handler = TaskHandlers(model, 'synthetic-workspace')
    handler._source_report = True
    handler.ledger = [Evidence(f'ev-{i}', '仅为软件验证而构造的原文。', f'Study_{i}.pdf', 1,
                               f'chunk-{i}', '仅为软件验证而构造的原文。',
                               kind='source_context', role='source_context', verification='verbatim_only')
                      for i in range(1, 6)]
    task = CaseContext.from_dict(case(kind + '-01', [f'Study_{i}.pdf' for i in range(1, 6)]))
    payload = task.to_dict()
    capture.update(_model=model, _handler=handler, input=payload, injected_body_lengths=lengths,
                   input_sha256=hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
    return model, handler, task


def validated(handler, task, capture):
    result = handler.compose_report(task)
    capture['result'] = result
    try:
        validate_result(task, result, team=handler.team, filenames=task.materials)
    except Exception as exc:
        capture['final_contract_error'] = {'type': type(exc).__name__, 'message': str(exc)}
        raise
    capture['final_contract_passed'] = True
    return result


@pytest.mark.parametrize('kind', ['C', 'E'])
def test_short_first_draft_recovers_within_existing_attempt_limit(kind, capture):
    model, handler, task = setup(kind, [799, 1100], capture)
    result = validated(handler, task, capture)
    assert model.report_calls == 2 and 'repair' not in model.stages
    assert len(re.sub(r'\s', '', result['markdown'].split('## 参考文献')[0])) == 1100


@pytest.mark.parametrize('kind,limit', [('C', 2), ('E', 3)])
def test_all_short_drafts_are_rejected_without_a_fake_fallback(kind, limit, capture):
    model, handler, task = setup(kind, [799], capture)
    with pytest.raises(EvidenceError, match='shorter than 800') as error:
        handler.compose_report(task)
    capture['expected_rejection'] = {'type': type(error.value).__name__, 'message': str(error.value)}
    assert model.report_calls == limit and 'audit' not in model.stages
    assert len(handler.ledger) == 5


@pytest.mark.parametrize('kind', ['C', 'E'])
def test_legal_800_character_boundary_uses_no_additional_generation(kind, capture):
    model, handler, task = setup(kind, [800], capture)
    validated(handler, task, capture)
    assert model.report_calls == 1


@pytest.mark.parametrize('kind', ['C', 'E'])
def test_current_valid_draft_survives_later_audit_timeout(kind, capture):
    model, handler, task = setup(kind, [1100], capture, audit_timeout=True)
    validated(handler, task, capture)
    assert model.report_calls == 1
    assert handler.audit['ok'] is False and handler.audit['mode'] == 'model_report_needs_review'
