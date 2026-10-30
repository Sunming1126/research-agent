"""9d66d4f8：正文前超时/输出丢失，以及公开自检替代正式实验。"""
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures import FakeLLM, case, papers
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.models import CaseContext
from research_agent.core.session import CaseSession
from research_agent.evidence import EvidenceError
from research_agent.integrations.llm import ModelError
from research_agent.workflows.tasks import TaskHandlers
from research_agent.workflows.layered_d_runner import LayeredDRunner


class DirectLLM(FakeLLM):
    supports_batched_extraction = True
    supports_source_reports = True

    def __init__(self):
        super().__init__()
        self.stages = []

    def json(self, stage, data, **kwargs):
        self.stages.append(stage)
        assert stage not in {'query', 'extract', 'extract_batch', 'verify_claims'}
        if stage == 'report':
            assert all(row['verification'] == 'verbatim_only' for row in data['claims'])
            assert all(row['role'] == 'source_context' for row in data['claims'])
            names = {row['ref'] for row in data['claims']}
            used = [next(row['evidence_id'] for row in data['claims'] if row['ref'] == ref) for ref in names]
            return {'markdown': '# 本题研究比较\n\n' +
                    '本题材料的研究对象与适用条件需要分别比较，不能将不同实验条件下的观测当作统一结论。' * 25 +
                    ''.join('[E:' + key + ']' for key in used), 'used_evidence_ids': used}
        if stage == 'audit':
            raise ModelError('review endpoint timeout')
        return super().json(stage, data, **kwargs)


@pytest.mark.parametrize('case_id', ['C-01', 'E-01'])
def test_first_call_writes_body_and_optional_review_cannot_remove_valid_files(tmp_path, case_id):
    names = papers(tmp_path/'papers')
    if case_id.startswith('E'):
        names = names[:2]
    settings = Settings(tmp_path, tmp_path/'out', 'injected-workspace', [tmp_path/'papers'])
    llm = DirectLLM()
    result = ResearchAgentEngine(settings, lambda: llm).solve_sync(case(case_id, names))
    assert llm.stages == ['report', 'audit']
    assert set(result) == {'case_id', 'path', 'markdown'}
    assert json.loads((settings.output/f'data/injected-workspace/{case_id}.json').read_text()) == result
    assert (settings.output/result['path']).read_text() == result['markdown']
    assert '逐篇可回查事实' not in result['markdown']
    evidence_path = next((settings.output/f'data/injected-workspace/work/{case_id}').glob('*/evidence.json'))
    evidence = json.loads(evidence_path.read_text())
    assert evidence['audit']['ok'] is False
    assert evidence['audit']['mode'] == 'model_report_needs_review'


def test_source_report_keeps_real_deadline_and_restores_transport_controls():
    llm = DirectLLM()
    llm.deadline = time.monotonic()+300
    llm.REQUEST_TIMEOUT_CAP, llm.RETRY_ATTEMPTS = 60, 2
    handler = TaskHandlers(llm, 'runtime-id')
    handler._source_report, handler._report_function = True, 'Survey'
    with CaseSession('C-01', 170).activate() as session:
        with handler._stage_time('report'):
            assert llm.REQUEST_TIMEOUT_CAP == 135 and llm.RETRY_ATTEMPTS == 1
            assert llm.thinking_override == 'disabled'
            assert llm.deadline <= session.deadline-25
        assert llm.REQUEST_TIMEOUT_CAP == 60 and llm.RETRY_ATTEMPTS == 2
        assert llm.thinking_override is None


def test_internal_citation_space_keeps_exact_original_source_binding():
    from research_agent.evidence.reporting import render_evidence_citations
    evidence = [{'evidence_id':'ev-1', 'ref':1, 'page':3},
                {'evidence_id':'ev-2', 'ref':2, 'page':8}]
    assert render_evidence_citations('事实[E: ev-1, ev-2 ]', evidence) == ('事实[1, p.3][2, p.8]', ['ev-1','ev-2'])
    with pytest.raises(EvidenceError, match='unknown'):
        render_evidence_citations('事实[E: ev-999]', evidence)


def test_source_content_question_is_recorded_but_unknown_citation_still_fails():
    from research_agent.evidence.reporting import validate_citations
    rows = [{'evidence_id':'ev-1','ref':1,'page':1,'filename':'DPR.pdf','claim':'A method','quote':'A method'},
            {'evidence_id':'ev-2','ref':2,'page':1,'filename':'FiD.pdf','claim':'B method','quote':'B method'}]
    body = '待核对的研究条件。'*100 + '\n\nDPR采用该阅读器。[2, p.1]'
    issues = validate_citations(body, rows, ['ev-2'], strict_facts=False)
    assert any('DPR' in issue for issue in issues)
    with pytest.raises(EvidenceError):
        validate_citations(body+'[3, p.1]', rows, ['ev-2'], strict_facts=False)


def test_gateway_requests_json_mode_and_saves_original_invalid_response(monkeypatch):
    from research_agent.integrations.llm import GatewayLLM
    monkeypatch.setenv('PLATFORM_LLM_URL', 'https://example.invalid/v1')
    monkeypatch.setenv('PLATFORM_API_KEY', 'test-key')
    monkeypatch.setenv('PLATFORM_LLM_MODEL', 'test-model')
    llm = GatewayLLM()
    llm.RETRY_ATTEMPTS = 1
    def invalid(*args):
        assert llm.json_response is True
        return {'choices':[{'finish_reason':'stop', 'message':{'content':'{invalid'}}],
                'usage':{'total_tokens':10}}
    monkeypatch.setattr(llm, '_request_once', invalid)
    with pytest.raises(ModelError):
        llm.json('report', {'task':{'function':'Survey'}}, max_tokens=10)
    assert llm.response_failures[0]['choices'][0]['message']['content'] == '{invalid'
    assert llm.json_response is False


@pytest.mark.parametrize('case_id', ['C-01', 'E-01'])
def test_no_model_body_means_no_counterfeit_submission(tmp_path, case_id):
    class Missing(DirectLLM):
        def json(self, stage, data, **kw):
            return {'report': 'wrong field', 'used_evidence_ids': []}
    names = papers(tmp_path/'papers')
    settings = Settings(tmp_path, tmp_path/'out', 'runtime-id', [tmp_path/'papers'])
    with pytest.raises(EvidenceError):
        ResearchAgentEngine(settings, Missing).solve_sync(case(case_id, names))
    assert not (settings.output/f'data/runtime-id/{case_id}.json').exists()


def setup_d(tmp_path, responses):
    root = tmp_path/'D-02'
    root.mkdir()
    for name in ('method.md', 'protocol.json', 'sample.json'):
        (root/name).write_text('{}')
    class LLM:
        def completion(self, *args, **kwargs):
            value = responses.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
    runner = LayeredDRunner(Settings(tmp_path, tmp_path/'out', 'runtime-id'), tmp_path/'work', LLM())
    return runner, CaseContext.from_dict(case('D-02'))


SOURCE = "def run_experiment(request):\n    return {'baseline': {}, 'candidate': None, 'details': {}}\n"


def response(source=SOURCE, finish='stop'):
    return {'choices':[{'finish_reason':finish, 'message':{'role':'assistant', 'content':source}}]}


@pytest.mark.parametrize('second', [response(), response('truncated code', 'length'), ModelError('timeout')])
def test_public_failure_retains_only_complete_current_source_for_formal_runner(tmp_path, second):
    runner, current = setup_d(tmp_path, [response(), second])
    def reject(*args):
        raise ValueError('public sample incomplete')
    source = runner.prepare_checked_implementation(current, tester=reject)
    assert source.read_text() == SOURCE
    saved = json.loads((runner.work/'generation-response.json').read_text())
    assert saved['choices'][0]['message']['content'] == SOURCE
    audit = json.loads((runner.work/'public-selftest.json').read_text())
    assert audit['public_selftest_passed'] is False
    assert audit['decision'] == 'submit_complete_model_source_for_formal_evaluation'


def test_without_complete_source_d_never_submits_a_stub(tmp_path):
    runner, current = setup_d(tmp_path, [response('broken', 'length'), response('broken', 'length')])
    with pytest.raises(RuntimeError, match='no complete'):
        runner.prepare_checked_implementation(current)
    assert not (runner.work/'implementation.py').exists()


def test_failed_candidate_upgrade_keeps_verified_baseline_and_its_own_response(tmp_path):
    runner, current = setup_d(tmp_path, [response(), response('def run_experiment(request):\n    return {}\n')])
    def validate(path, *args):
        if path.read_text() != SOURCE:
            raise ValueError('candidate upgrade has invalid output')
        return {'status':'passed','candidate_present':False}
    with CaseSession(current.case_id,170).activate():
        selected = runner.prepare_checked_implementation(current,tester=validate)
    assert selected.read_text() == SOURCE
    audit = json.loads((runner.work/'public-selftest.json').read_text())
    assert audit['public_selftest_passed'] is True and audit['selected_attempt'] == 1
    assert audit['attempts'][1]['status'] == 'failed'
    assert json.loads((runner.work/'generation-response.json').read_text()) == response()


def test_d05_selftest_exercises_simultaneous_arrivals_not_only_easy_sample(tmp_path):
    from research_agent.workflows.sample_validation import run_public_sample
    source = tmp_path/'heap_bug.py'
    source.write_text('''import heapq
def run_experiment(request):
    data = request['data']
    events = []
    for j in data['jobs']:
        heapq.heappush(events, (j['arrival_ms'], j))
    value = {'trace': [], 'served_ms': {f:0 for f in data['flow_weights']},
             'dropped': [{'id':j['id'],'reason':'horizon'} for j in data['jobs']]}
    return {'baseline':value,'candidate':None,'details':{}}
''')
    request = {'case_id':'D-05','phase':'run','data':{
        'jobs':[{'id':'a','flow':'f','arrival_ms':0,'service_ms':1,'deadline_ms':8},
                {'id':'b','flow':'f','arrival_ms':1,'service_ms':1,'deadline_ms':8}],
        'flow_weights':{'f':1},'servers':1,'setup_ms':0,'horizon_ms':10}}
    materials = {'sample':json.dumps(request),'protocol':json.dumps({'case_id':'D-05'})}
    assert run_public_sample(source,materials,'D-05',_edge_check=False)['status'] == 'passed'
    with pytest.raises(ValueError,match='simultaneous_public_arrivals'):
        run_public_sample(source,materials,'D-05')


def test_failed_selftest_still_reaches_real_client_boundary(tmp_path, monkeypatch):
    import subprocess
    runner, current = setup_d(tmp_path, [response(), response()])
    # 公开样例失败后，生产路径必须调用当前挂载客户端；不伪造成功。
    method = runner.prepare_checked_implementation
    def reject(*args):
        raise ValueError('public sample mismatch')
    monkeypatch.setattr(runner, 'prepare_checked_implementation', lambda c: method(c, tester=reject))
    monkeypatch.setenv('RESEARCH_EXPERIMENT_CASE', current.case_id)
    script = Path('/data/experiments/run_experiment.py')
    real_exists = Path.is_file
    monkeypatch.setattr(Path, 'is_file', lambda p: True if p == script else real_exists(p))
    monkeypatch.setattr('research_agent.workflows.layered_d_runner.sha256_file', lambda p: 'test-hash')
    result = {'case_id':current.case_id, 'status':'failed', 'metrics':[], 'figures':[]}
    calls = []
    def invoke(command, **kwargs):
        calls.append(command)
        assert Path(command[-1]).read_text() == SOURCE
        return subprocess.CompletedProcess(command, 0, json.dumps(result), '')
    monkeypatch.setattr('research_agent.workflows.layered_d_runner.subprocess.run', invoke)
    assert runner.run(current, []) == result
    assert len(calls) == 1 and calls[0][1] == str(script)
    assert runner.proof['submission'] == result
