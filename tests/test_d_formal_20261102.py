import json,time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from research_agent.core.models import CaseContext
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.session import CaseSession
from research_agent.workflows import layered_d_runner as mod
from research_agent.workflows.source_generation import build_source_prompt
from research_agent.workflows.d_generation_policy import generation_window,formal_timeout
from research_agent.integrations.llm import ModelQuotaError

def case(cid):
    return CaseContext.from_dict(dict(case_id=cid,function='Reproduction',title='new task',input='Use current materials',materials=[],constraints=[],expected_output='runtime fields',output_schema_ref='Reproduction'))
def setup(tmp_path,cid,fail=0):
    directory=tmp_path/cid;directory.mkdir()
    for name in ('method.md','protocol.json','sample.json'):(directory/name).write_text('NEW_RUNTIME_CONTRACT',encoding='utf-8')
    class LLM:
        calls=0
        def completion(self,stage,prompt,max_tokens):
            self.calls+=1
            assert 'NEW_RUNTIME_CONTRACT' in prompt
            if stage=='d_review':
                return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps({'decision':'keep','defects':[]})}}]}
            assert max_tokens>5200
            return {'choices':[{'finish_reason':'length' if self.calls<=fail else 'stop','message':{'role':'assistant','content':"def run_experiment(request):\n    return {'runtime_field': request['data']}\n"}}]}
    llm=LLM();return mod.LayeredDRunner(Settings(tmp_path,tmp_path/'out'),tmp_path/'work',llm),llm
@pytest.mark.parametrize('cid',['D-01','D-02','D-05'])
def test_current_materials_only(cid):
    text=build_source_prompt(case(cid),{'method':'new method','protocol':'dynamic fields','sample':'runtime sample'})
    assert 'new method' in text and 'dynamic fields' in text
    assert not any(x in text for x in ('BM25','TF-IDF','softmax','Jain','title_weight','candidate=null'))
@pytest.mark.parametrize('cid',['D-01','D-02','D-05'])
def test_no_practice_semantic_gate(tmp_path,cid):
    runner,llm=setup(tmp_path,cid)
    def forbidden(*args):raise AssertionError('practice oracle must not execute')
    path=runner.prepare_checked_implementation(case(cid),tester=forbidden)
    assert 'runtime_field' in path.read_text()
    assert llm.calls==1
@pytest.mark.parametrize('fail',[1,2,3])
def test_truncation_retry_bounded(tmp_path,fail):
    runner,llm=setup(tmp_path,'D-02',fail)
    if fail==3:
        with pytest.raises(RuntimeError):runner.prepare_checked_implementation(case('D-02'))
    else:assert runner.prepare_checked_implementation(case('D-02')).is_file()
    assert llm.calls==min(3,fail+1)
@pytest.mark.parametrize('cid',['D-02','D-05'])
def test_formal_entry_called_after_generation(tmp_path,cid,monkeypatch):
    runner,llm=setup(tmp_path,cid)
    monkeypatch.setenv('RESEARCH_EXPERIMENT_CASE',cid)
    actual=Path.is_file
    monkeypatch.setattr(Path,'is_file',lambda p:True if str(p).replace('\\','/')=='/data/experiments/run_experiment.py' else actual(p))
    specs=[('one','x'),('two','x'),('three','x')]
    submission=runner._failed(cid,specs,'failed');calls=[]
    def execute(command,**kwargs):
        calls.append(command);assert kwargs['timeout']>1000
        assert '--implementation' in command
        return SimpleNamespace(returncode=0,stdout=json.dumps(submission),stderr='')
    monkeypatch.setattr(mod.subprocess,'run',execute)
    monkeypatch.setattr(mod,'sha256_file',lambda p:'hash')
    with CaseSession(cid,1780).activate():result=runner.run(case(cid),specs)
    assert len(calls)==1 and result==submission
    assert (runner.work/'platform-submission.json').is_file()

def test_d_timeout_and_non_d_unchanged(tmp_path):
    engine=ResearchAgentEngine(Settings(tmp_path,tmp_path/'out'))
    assert engine._case_timeout({'function':'Reproduction'})==1780
    assert engine._case_timeout({'function':'Literature_Retrieval'})==530
    assert engine._case_timeout({'function':'Idea'})==885
    for function in ('Chat','Survey'):
        assert engine._case_timeout({'function':function})==170

def test_window_restores_model_controls():
    llm=SimpleNamespace(deadline=time.monotonic()+1700,REQUEST_TIMEOUT_CAP=60,RETRY_ATTEMPTS=2)
    saved=dict(vars(llm))
    with CaseSession('D-02',1780).activate():
        with generation_window(llm,baseline=False,case_id='D-02'):
            assert llm.REQUEST_TIMEOUT_CAP==420 and llm.thinking_override=='enabled'
    assert vars(llm)==saved

def test_packaging_excludes_old_semantic_modules():
    rules=Path('.dockerignore').read_text(encoding='utf-8')
    for name in ('d02_policy','d05_policy','d_public_review','d_boundary_cases','d05_review','sample_validation'):
        assert 'src/research_agent/workflows/'+name+'.py' in rules

@pytest.mark.parametrize('function,expected',[('Reproduction',1800000),('Survey',180000),('Idea',900000),('Literature_Retrieval',540000),('Chat',180000)])
def test_http_sdk_timeout(function,expected):
    import asyncio
    from research_agent.integrations.http_runtime import ResearchRequestAdapter
    payload=case('D-02').to_dict();payload['function']=function;payload['output_schema_ref']=function
    payload['case_id']={'Reproduction':'D-02','Survey':'C-03','Idea':'E-01','Literature_Retrieval':'B-01','Chat':'A-01'}[function]
    seen=[]
    async def app(scope,receive,send):
        message=await receive();seen.append(json.loads(message['body']))
        await send({'type':'http.response.start','status':202,'headers':[]})
        await send({'type':'http.response.body','body':b'{}'})
    async def receive():return {'type':'http.request','body':json.dumps(payload).encode(),'more_body':False}
    async def send(message):pass
    adapter=ResearchRequestAdapter(app,lambda:180)
    asyncio.run(adapter({'type':'http','path':'/solve','method':'POST','headers':[]},receive,send))
    assert seen[0]['constraints']['timeout_ms']==expected
