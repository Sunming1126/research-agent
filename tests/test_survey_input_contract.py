"""C类八字段从入口到写作/审查/修订的完整传递。"""
import json
import pytest

from fixtures import case, papers
from test_eval_20261026_regressions import DirectLLM
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.models import CaseContext
from research_agent.workflows.report_context import report_task, repair_context
from research_agent.integrations.llm import ModelError
from research_agent.evidence import EvidenceError


def test_full_official_case_reaches_report_and_audit_and_saved_receipt(tmp_path):
    names = papers(tmp_path / 'papers')
    payload = case('C-03', names)
    payload.update(title='按标题比较方法、实验条件与局限', input='阅读指定论文，按输出要求写综述。',
                   expected_output='返回 case_id、path 和完整 Markdown 综述。')
    class Capture(DirectLLM):
        def json(self, stage, data, **kwargs):
            if stage in {'report', 'audit'}:
                assert data['task'] == payload
            return super().json(stage, data, **kwargs)
    settings = Settings(tmp_path, tmp_path/'out', 'platform-workspace', [tmp_path/'papers'])
    result = ResearchAgentEngine(settings, Capture).solve_sync(payload)
    assert set(result) == {'case_id', 'path', 'markdown'}
    receipt = next((settings.output/'data/platform-workspace/work/C-03').glob('*/receipt.json'))
    assert json.loads(receipt.read_text())['source_case'] == payload
    assert json.loads((settings.output/'data/platform-workspace/C-03.json').read_text()) == result
    assert (settings.output/result['path']).read_text() == result['markdown']


def test_survey_repair_retains_requirements_outside_input():
    payload = case('C-01', ['指定论文.pdf'])
    payload['expected_output'] = '返回 case_id、path 和完整 Markdown 综述。'
    context = CaseContext.from_dict(payload)
    assert report_task(context) == payload
    assert repair_context('正文', ['正文'], ['需要补充限制'], [], case=context)['task'] == payload


@pytest.mark.parametrize('invalid', [
    {'markdown': {'nested': 'wrong type'}, 'used_evidence_ids': []},
    {'markdown': '正文', 'used_evidence_ids': [{'id': 'ev-1'}]},
    ModelError('model returned invalid JSON after retries'),
    ModelError('model response incomplete after retries'),
])
def test_bad_first_model_response_is_regenerated_not_submitted(tmp_path, invalid):
    class Recover(DirectLLM):
        attempts = 0
        def json(self, stage, data, **kwargs):
            if stage == 'report':
                self.attempts += 1
                if self.attempts == 1:
                    if isinstance(invalid, Exception):
                        raise invalid
                    return invalid
            return super().json(stage, data, **kwargs)
    settings = Settings(tmp_path, tmp_path/'out', 'platform-workspace', [tmp_path/'papers'])
    llm = Recover()
    result = ResearchAgentEngine(settings, lambda: llm).solve_sync(case('C-01', papers(tmp_path/'papers')))
    assert llm.attempts == 2
    assert isinstance(result['markdown'], str) and len(result['markdown']) > 800


def test_transport_failure_does_not_retry_or_create_fake_report(tmp_path):
    class Timeout(DirectLLM):
        calls = 0
        def json(self, stage, data, **kwargs):
            self.calls += 1
            raise ModelError('model transport failure (APITimeoutError) after retries')
    settings = Settings(tmp_path, tmp_path/'out', 'platform-workspace', [tmp_path/'papers'])
    llm = Timeout()
    with pytest.raises(EvidenceError):
        ResearchAgentEngine(settings, lambda: llm).solve_sync(case('C-01', papers(tmp_path/'papers')))
    assert llm.calls == 1
    assert not (settings.output/'data/platform-workspace/C-01.json').exists()
