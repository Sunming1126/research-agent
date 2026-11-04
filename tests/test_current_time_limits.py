"""Fixed-time probes for formal limits, using no external model or long sleeps."""
import asyncio
import json
import subprocess
from unittest.mock import patch

import pytest

from fixtures import case, FakeLLM
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.session import CaseSession
from research_agent.integrations.http_runtime import ResearchRequestAdapter


CASES = [('A-05', 540), ('B-02', 540), ('C-05', 900), ('D-05', 1800), ('E-04', 900)]


def test_default_global_ceiling_does_not_truncate_any_formal_category(tmp_path):
    assert Settings(tmp_path/'data', tmp_path/'output').case_timeout == 1800


def test_env_default_and_explicit_smaller_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv('RESEARCH_CONFIG', raising=False)
    assert Settings.from_env().case_timeout == 1800
    config = tmp_path/'settings.json'
    config.write_text(json.dumps({'case_timeout':75, 'workspace':str(tmp_path/'data'),
                                 'output':str(tmp_path/'output'), 'material_roots':[]}))
    monkeypatch.setenv('RESEARCH_CONFIG', str(config))
    assert Settings.from_env().case_timeout == 75


@pytest.mark.parametrize('case_id,maximum', CASES)
@pytest.mark.parametrize('configured', [None, 75, 7200])
def test_engine_caps_and_explicit_smaller_deadline(tmp_path, case_id, maximum, configured):
    settings = Settings(tmp_path/'data', tmp_path/'out')
    if configured is not None:
        settings.case_timeout = configured
    wire = min(maximum, configured) if configured is not None else maximum
    engine = ResearchAgentEngine(settings, FakeLLM)
    assert engine._case_timeout(case(case_id)) == wire - min(10, wire*.1)
    assert settings.llm_calls == 30 and settings.llm_tokens == 40000


@pytest.mark.parametrize('case_id,maximum', CASES)
@pytest.mark.parametrize('configured', [75, 7200])
def test_http_passes_same_category_ceiling_into_sdk(case_id, maximum, configured):
    payload = case(case_id)
    captured, sent = [], []

    async def app(scope, receive, send):
        captured.append(json.loads((await receive())['body']))
        await send({'type':'http.response.start','status':422,'headers':[]})
        await send({'type':'http.response.body','body':b'{}'})

    async def receive():
        return {'type':'http.request','body':json.dumps(payload).encode(),'more_body':False}

    async def send(message):
        sent.append(message)

    scope = {'type':'http','path':'/solve','method':'POST','headers':[]}
    asyncio.run(ResearchRequestAdapter(app, lambda:configured)(scope,receive,send))
    assert captured[0]['constraints']['timeout_ms'] == min(maximum,configured)*1000
    assert captured[0]['input'] == payload
    assert sent[0]['status'] == 422


@pytest.mark.parametrize('case_id,elapsed', [('B-02',400),('C-05',600),('D-05',1000),('E-04',600)])
def test_valid_work_is_not_cancelled_at_old_180_second_boundary(tmp_path, case_id, elapsed):
    engine = ResearchAgentEngine(Settings(tmp_path/'data',tmp_path/'out'), FakeLLM)
    session = CaseSession(case_id, engine._case_timeout(case(case_id)), started_at=0)
    with patch('research_agent.core.session.time.monotonic',return_value=elapsed):
        with session.activate():
            session.checkpoint('working')
    with patch('research_agent.core.session.time.monotonic',return_value=session.deadline):
        with pytest.raises(TimeoutError,match='deadline'):
            session.checkpoint()
    session.cancelled.set()
    with patch('research_agent.core.session.time.monotonic',return_value=0):
        with pytest.raises(TimeoutError,match='cancelled'):
            session.checkpoint()


@pytest.mark.parametrize('elapsed', [0,600])
def test_d_formal_client_uses_remaining_deadline_not_old_175_second_cap(tmp_path, monkeypatch, elapsed):
    from research_agent.core.models import CaseContext
    from research_agent.workflows.layered_d_runner import LayeredDRunner
    from research_agent.workflows.experiments import failed_result
    from pathlib import Path
    settings = Settings(tmp_path/'data',tmp_path/'out')
    runner = LayeredDRunner(settings,tmp_path/'work')
    runner.work.mkdir()
    source = runner.work/'implementation.py'
    source.write_text('def run_experiment(request): return {}')
    script = Path('/data/experiments/run_experiment.py')
    real = Path.is_file
    monkeypatch.setattr(Path,'is_file',lambda p:True if p==script else real(p))
    monkeypatch.setenv('RESEARCH_EXPERIMENT_CASE','D-01')
    monkeypatch.setattr('research_agent.workflows.layered_d_runner.read_generation_materials',lambda *a:{})
    monkeypatch.setattr(runner,'prepare_checked_implementation',lambda c:source)
    monkeypatch.setattr('research_agent.workflows.layered_d_runner.sha256_file',lambda p:'fixture')
    limits=[]
    specs=[('Macro-F1','分数'),('Accuracy','%'),('运行时间','秒')]
    answer=failed_result('D-01',specs,'failed')
    def execute(*a,**kw):
        limits.append(kw['timeout'])
        return subprocess.CompletedProcess([],0,json.dumps(answer),'')
    monkeypatch.setattr('research_agent.workflows.layered_d_runner.subprocess.run',execute)
    session=CaseSession('D-01',1790,started_at=0)
    with patch('research_agent.core.session.time.monotonic',return_value=elapsed):
        with session.activate():
            assert runner.run(CaseContext.from_dict(case('D-01')),specs)==answer
    assert limits == [1790-elapsed-5]
