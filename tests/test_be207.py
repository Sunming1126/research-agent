import json
import time
import pytest
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.config import Settings
from research_agent.core.session import CaseSession
from research_agent.core.models import CaseContext
from research_agent.integrations.llm import ModelError
from research_agent.workflows.idea_workflow import call_window
from test_e4_resilience import run,BODY,good_audit
from fixtures import case

@pytest.mark.parametrize('cid,fn,seconds,unlimited',[
 ('A-01','Chat',170,False),('B-02','Literature_Retrieval',530,True),
 ('C-05','Survey',170,False),('D-02','Reproduction',1780,True),('E-04','Idea',885,True)])
def test_category_budget_isolation(tmp_path,cid,fn,seconds,unlimited):
    settings=Settings(tmp_path,tmp_path/'out',experiment_mode='platform')
    engine=ResearchAgentEngine(settings)
    assert engine._case_timeout({'function':fn})==seconds
    with CaseSession(cid,seconds).activate():
        llm=engine.llm_factory()
        assert llm.budget==(float('inf') if unlimited else settings.llm_tokens)
        if unlimited:
            llm.total_tokens=120000
            assert llm.output_capacity('report',{},maximum=4200)==4200

def test_e_model_failure_after_old_180_second_boundary_can_recover():
    from research_agent.core.session import current_session
    def slow(model,stage,data):
        current_session().started_at-=185
        raise ModelError('transport timeout')
    w,m=run([slow,ModelError('invalid JSON'),{'markdown':BODY},good_audit()])
    with CaseSession('E-04',885).activate():
        result=w.compose_report(CaseContext.from_dict(case('E-04')))
    assert result['markdown'] and m.stages==['report','report','report','audit']

def test_e_extended_window_restores_llm_and_does_not_extend_case_deadline():
    class L:pass
    llm=L();llm.deadline=time.monotonic()+885;before=vars(llm).copy()
    with CaseSession('E-04',885).activate() as session:
        with call_window(llm,'report'):
            assert 200 < llm.deadline-time.monotonic() <=240
            assert llm.deadline<session.deadline
    assert vars(llm)==before

def test_b_no_approval_checks_outside_original_pool(monkeypatch):
    from test_b5_conditions import chunk,run as b_run
    from test_b6_retrieval import ScreeningModel,response
    monkeypatch.setenv('RESEARCH_B_CANDIDATE_LIMIT','8')
    model=ScreeningModel(lambda data:{'papers':[response(c,'relevant' if c['filename']=='z.pdf' else 'irrelevant') for c in data['candidates']]})
    w,result=b_run(model,[chunk(str(i)+'.pdf','urban cooling remote sensing study.') for i in range(8)]+[chunk('z.pdf','urban cooling remote sensing study.')])
    assert [p['filename'] for p in result['papers']]==['z.pdf']
    assert w.retrieval_trace['outside_rescue']==['z.pdf']

def test_b_audit_has_bounded_calls_and_preserves_results(monkeypatch):
    from test_b5_conditions import chunk,run as b_run
    from test_b6_retrieval import ScreeningModel,response
    model=ScreeningModel(lambda data:{'papers':[response(c) for c in data['candidates']]})
    w,result=b_run(model,[chunk(str(i)+'.pdf','urban cooling study.') for i in range(32)])
    assert len([d for s,d in model.calls if d.get('verification')])<=3
    assert len(result['papers'])==32


@pytest.mark.parametrize('cid,expected',[('E-04',900000),('C-05',180000),('B-02',540000),('D-02',1800000)])
def test_sdk_envelope_timeout_is_category_specific(cid,expected):
    import asyncio
    from research_agent.integrations.http_runtime import ResearchRequestAdapter
    captured={}
    class Captured(Exception):pass
    async def app(scope,receive,send):
        captured.update(json.loads((await receive())['body']))
        raise Captured()
    async def receive():return {'type':'http.request','body':json.dumps(case(cid)).encode()}
    async def send(message):pass
    adapter=ResearchRequestAdapter(app,lambda:180)
    with pytest.raises(Captured):asyncio.run(adapter({'type':'http','path':'/solve','method':'POST','headers':[]},receive,send))
    assert captured['constraints']['timeout_ms']==expected
