"""D-only strategy, provenance fallback, platform failures and public method checks."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time

import pytest

from research_agent.core.session import CaseSession
from research_agent.workflows.d_generation_policy import generation_window, PlatformExperimentUnavailable
from research_agent.workflows.d_public_review import review_public_result
from research_agent.workflows.layered_d_runner import LayeredDRunner
from research_agent.workflows.source_generation import build_source_prompt
from test_eval_20261026_regressions import setup_d, response, SOURCE
from fixtures import case
from research_agent.core.models import CaseContext


@pytest.mark.parametrize('failure', [False, True])
def test_d_generation_restores_every_instance_attribute_and_class_default(failure):
    class Model:
        REQUEST_TIMEOUT_CAP = 60
        RETRY_ATTEMPTS = 2
        def __init__(self):
            self.deadline = time.monotonic() + 170
    model = Model()
    before = vars(model).copy()
    try:
        with generation_window(model, baseline=False):
            assert model.REQUEST_TIMEOUT_CAP <= 95
            assert model.RETRY_ATTEMPTS == 1
            assert model.thinking_override == 'disabled'
            assert Model.REQUEST_TIMEOUT_CAP == 60
            if failure:
                raise RuntimeError('generation fails')
    except RuntimeError:
        assert failure
    assert vars(model) == before
    assert Model.RETRY_ATTEMPTS == 2


@pytest.mark.parametrize('case_id, baseline', [('D-01', False), ('D-02', False), ('D-05', False)])
def test_first_generation_strategy_is_case_scoped(tmp_path, case_id, baseline):
    runner, _ = setup_d(tmp_path, [])
    (tmp_path/'D-02').rename(tmp_path/case_id) if case_id != 'D-02' else None
    current = CaseContext.from_dict(case(case_id))
    calls = []
    def generate(current, **kwargs):
        calls.append(kwargs['baseline_only'])
        runner.work.mkdir(parents=True, exist_ok=True)
        path = runner.work/'implementation.py'; path.write_text(SOURCE)
        (runner.work/'generation-response.json').write_text(json.dumps(response()))
        return path
    runner.prepare_generated_implementation = generate
    runner.prepare_checked_implementation(current, tester=lambda *args: {'status':'passed', 'candidate_present':not baseline})
    assert calls == [baseline]


def test_cancelled_d_does_not_retry_or_submit_source(tmp_path):
    runner, current = setup_d(tmp_path, [response(), response()])
    cancelled = threading.Event()
    def tester(*args):
        cancelled.set()
        raise ValueError('failed sample while cancelled')
    with CaseSession(current.case_id, 170, cancelled=cancelled).activate():
        with pytest.raises(TimeoutError, match='cancelled'):
            runner.prepare_checked_implementation(current, tester=tester)
    assert not (runner.work/'source-attempt-2.json').exists()


def enable_client(monkeypatch, runner, current, reply=None, stderr='', code=0):
    monkeypatch.setenv('RESEARCH_EXPERIMENT_CASE', current.case_id)
    original = Path.is_file
    monkeypatch.setattr(Path, 'is_file', lambda p: True if p == Path('/data/experiments/run_experiment.py') else original(p))
    source = runner.work/'implementation.py'; source.parent.mkdir(parents=True, exist_ok=True); source.write_text(SOURCE)
    monkeypatch.setattr(runner, 'prepare_checked_implementation', lambda c: source)
    monkeypatch.setattr('research_agent.workflows.layered_d_runner.sha256_file', lambda p: 'hash')
    calls = []
    def invoke(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args[0], code, json.dumps(reply) if reply is not None else '', stderr)
    monkeypatch.setattr('research_agent.workflows.layered_d_runner.subprocess.run', invoke)
    return calls


@pytest.mark.parametrize('error', ['D_SOURCE_AUDIT_UNAVAILABLE', 'HTTP Error 503: Service Unavailable'])
def test_platform_service_failure_is_not_algorithm_failed(tmp_path, monkeypatch, error):
    runner, current = setup_d(tmp_path, [])
    calls = enable_client(monkeypatch, runner, current, stderr='RuntimeError: '+error, code=1)
    with pytest.raises(PlatformExperimentUnavailable):
        runner.run(current, [])
    assert len(calls) == 1 and runner.proof is None
    record = json.loads((runner.work/'experiment-error.json').read_text())
    assert record['stage'] == 'platform_service'


def test_source_rejection_returns_honest_failed_schema_without_formal_retry(tmp_path, monkeypatch):
    runner, current = setup_d(tmp_path, [])
    calls = enable_client(monkeypatch, runner, current, stderr='RuntimeError: D_SOURCE_NOT_GENERATED_THIS_SESSION', code=1)
    specs = [('m1','u1'), ('m2','u2'), ('m3','u3')]
    result = runner.run(current, specs)
    assert result == runner._failed(current.case_id, specs, 'failed')
    assert len(calls) == 1


def test_platform_partial_measurements_and_figures_are_preserved(tmp_path, monkeypatch):
    runner, current = setup_d(tmp_path, [])
    metrics = [{'name':n, 'unit':u, 'reference':None, 'observed':v, 'relative_error':None}
               for n,u,v in [('m1','u1',2),('m2','u2',None),('m3','u3',None)]]
    result = {'case_id':current.case_id,'status':'failed','metrics':metrics,'figures':[]}
    enable_client(monkeypatch, runner, current, reply=result)
    assert runner.run(current, [(m['name'],m['unit']) for m in metrics]) == result
    assert runner.proof['submission'] == result


def test_postprocessing_failure_never_replaces_received_platform_result(tmp_path, monkeypatch):
    runner, current = setup_d(tmp_path, [])
    result = {'case_id':current.case_id,'status':'completed','metrics':[],'figures':[]}
    enable_client(monkeypatch, runner, current, reply=result)
    def missing(*args):
        raise FileNotFoundError('figure unavailable')
    monkeypatch.setattr(runner, '_copy_figures', missing)
    with pytest.raises(RuntimeError, match='postprocessing'):
        runner.run(current, [])
    assert json.loads((runner.work/'platform-submission.json').read_text()) == result


def public(case_id):
    root = Path('/materials')/case_id
    request = json.loads((root/'sample.json').read_text())
    request['protocol'] = json.loads((root/'protocol.json').read_text())
    return request


def test_d01_detects_false_idf_even_when_predictions_consistent():
    from research_agent.workflows.d_algorithms.d01 import run_d01
    request = public('D-01'); result = run_d01(request)
    review_public_result('D-01', request, result)
    value = result['details']['idf']
    if isinstance(value, dict): value[next(iter(value))] += .3
    else: value[0] += .3
    with pytest.raises(ValueError, match='IDF'):
        review_public_result('D-01', request, result)


def test_d02_detects_fabricated_group_statistics():
    from research_agent.workflows.d_algorithms.d02 import run_d02
    request = public('D-02'); result = run_d02(request)
    review_public_result('D-02', request, result)
    result['details']['search'][0]['group_metrics']['macro_mrr_at_10'] += .1
    with pytest.raises(ValueError, match='macro_mrr'):
        review_public_result('D-02', request, result)


def test_d02_detects_rankings_that_ignore_query_tokens():
    from research_agent.workflows.d_algorithms.d02 import run_d02
    request = public('D-02'); result = run_d02(request)
    for rows in result['baseline'].values():
        for row in rows: row['score'] = 0
        rows.sort(key=lambda row: row['id'])
    with pytest.raises(ValueError, match='BM25'):
        review_public_result('D-02', request, result)


def test_d05_detects_missing_flow_switch_cost():
    from research_agent.workflows.d_algorithms.d05 import run_d05
    request = public('D-05'); request['data']['horizon_ms']=30
    for job in request['data']['jobs']: job['deadline_ms']=30
    result = run_d05(request)
    review_public_result('D-05', request, result)
    result['baseline']['trace'][-1]['setup_ms'] = 0
    with pytest.raises(ValueError, match='setup'):
        review_public_result('D-05', request, result)


def test_d05_reject_then_dispatch_regression(tmp_path):
    from research_agent.workflows.sample_validation import run_public_sample
    source = Path(__file__).parent/'data/d05_rejected_job_generated_source.txt'
    material_root = Path('/materials/D-05')
    materials = {key: (material_root/name).read_text() for key,name in
                 [('method','method.md'),('protocol','protocol.json'),('sample','sample.json')]}
    with pytest.raises(ValueError, match='retry_same_server_after_rejection'):
        run_public_sample(source, materials, 'D-05')


def test_d05_arrival_event_must_not_skip_dispatch(tmp_path):
    from research_agent.workflows.sample_validation import run_public_sample
    source = Path(__file__).parent/'data/d05_delayed_dispatch_generated_source.txt'
    root = Path('/materials/D-05')
    materials = {key: (root/name).read_text() for key,name in
                 [('method','method.md'),('protocol','protocol.json'),('sample','sample.json')]}
    with pytest.raises(ValueError, match='dispatch available work immediately'):
        run_public_sample(source, materials, 'D-05')


def test_d_generation_failure_then_c_uses_fresh_state(tmp_path, monkeypatch):
    import shutil
    from fixtures import papers
    from test_eval_20261026_regressions import DirectLLM
    from research_agent.core.config import Settings
    from research_agent.core.engine import ResearchAgentEngine
    from research_agent.core.session import current_session
    from research_agent.integrations.llm import ModelError
    directory = tmp_path/'data/experiments/D-01'
    shutil.copytree('/materials/D-01', directory)
    protocol = json.loads((directory/'protocol.json').read_text())
    d = case('D-01', ['data/experiments/D-01'])
    d['input'] = ';'.join('name='+row['name']+', unit='+row['unit'] for row in protocol['metrics'])
    class BrokenD(DirectLLM):
        REQUEST_TIMEOUT_CAP = 60
        RETRY_ATTEMPTS = 2
        def completion(self, *args, **kwargs):
            assert self.RETRY_ATTEMPTS == 1
            raise ModelError('simulated D generation timeout')
    models = [BrokenD(), DirectLLM()]
    initial = vars(models[0]).copy()
    count = iter(models)
    library = tmp_path/'papers';names = papers(library)
    settings = Settings(tmp_path, tmp_path/'out', 'isolation-team', [library])
    engine = ResearchAgentEngine(settings, lambda: next(count))
    monkeypatch.setenv('RESEARCH_EXPERIMENT_CASE', 'D-01')
    original = Path.is_file
    monkeypatch.setattr(Path, 'is_file', lambda path: True if path == Path('/data/experiments/run_experiment.py') else original(path))
    failed = engine.solve_sync(d)
    assert failed['status'] == 'failed' and len(failed['metrics']) == 3
    assert current_session() is None and not engine.lock.locked()
    for name in ('REQUEST_TIMEOUT_CAP','RETRY_ATTEMPTS','thinking_override'):
        assert (name in vars(models[0])) == (name in initial)
    success = engine.solve_sync(case('C-01', names))
    assert len(success['markdown']) > 800
    assert current_session() is None and not engine.lock.locked()
    assert getattr(models[1], 'thinking_override', None) is None
