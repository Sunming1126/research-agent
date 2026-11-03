import json
import pytest
from test_d_formal_20261102 import setup,case
from test_b5_conditions import chunk,run
from test_b6_retrieval import ScreeningModel,response
from research_agent.workflows.d_review import grounded_defects
from research_agent.core.session import CaseSession

@pytest.mark.parametrize('content',[{'decision':'keep','defects':[]},{'decision':'repair','defects':[{'material_quote':'invented','source_quote':'request','explanation':'x','counterexample':'x'}]},None])
def test_d_unsubstantiated_review_never_rewrites(tmp_path,content):
    runner,llm=setup(tmp_path,'D-02');path=runner.prepare_checked_implementation(case('D-02'));old=path.read_bytes()
    receipt=(runner.work/'generation-response.json').read_bytes();calls=[]
    def review(stage,prompt,**kw):
        calls.append(stage)
        return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(content)}}]}
    llm.completion=review
    with CaseSession('D-02',1780).activate():runner.review_implementation(case('D-02'),path)
    assert calls==(['d_tests','d_tests','d_review'] if content and content.get('decision')=='keep' else ['d_tests','d_tests','d_review','d_review']) and path.read_bytes()==old
    assert (runner.work/'generation-response.json').read_bytes()==receipt

@pytest.mark.parametrize('bad',[None,{'choices':None},{'choices':[None]},{'choices':[{'finish_reason':'stop','message':[]}]}])
def test_malformed_d_review_is_not_a_source_replacement(bad):
    assert grounded_defects(bad,{'method':'current materials'},'def run_experiment(request): pass')==[]

def test_b_reserves_recovery_before_spending_whole_budget():
    class Limited(ScreeningModel):
        def output_capacity(self,stage,payload,*,reserve=0,maximum=3000):
            return 0 if reserve and len(self.calls)>=3 else maximum
    m=Limited(lambda data:{'papers':[response(c,'relevant' if 'review_note' in c else 'uncertain') for c in data['candidates']]})
    h,result=run(m,[chunk(f'{i:02}.pdf','urban cooling remote sensing study.') for i in range(32)])
    assert h.retrieval_trace['initial_stop']=='reserve_followup_tokens'
    assert len(result['papers'])==16 and h.retrieval_trace['outcome']=='partial'

def test_b_recovery_splits_large_fulltext_batch_before_model_rejection():
    class Limited(ScreeningModel):
        def output_capacity(self,stage,payload,*,reserve=0,maximum=3000):
            rows=payload.get('candidates',[])
            return 0 if len(rows)>1 and any('review_note' in r for r in rows) else maximum
    m=Limited(lambda data:{'papers':[response(c,'relevant' if 'review_note' in c else 'uncertain') for c in data['candidates']]})
    h,result=run(m,[chunk(f'{i}.pdf','urban cooling remote sensing.') for i in range(4)])
    assert len(result['papers'])==4
    assert [len(d['candidates']) for s,d in m.calls if s=='retrieve' and not d.get('verification')]==[4,1,1,1,1]

@pytest.mark.parametrize('cid',['A-01','B-02','C-03','D-02','E-01'])
def test_only_bd_remove_local_total_token_limit(tmp_path,cid):
    from research_agent.core.engine import ResearchAgentEngine
    from research_agent.core.config import Settings
    engine=ResearchAgentEngine(Settings(tmp_path,tmp_path/'out'))
    with CaseSession(cid,530).activate():llm=engine.llm_factory()
    assert llm.budget==(float('inf') if cid.startswith(('B-', 'D-', 'E-')) else 40000)
    if cid.startswith(('B-', 'D-', 'E-')):
        llm.total_tokens=90000
        assert llm.output_capacity('retrieve',{},maximum=3000)==3000
