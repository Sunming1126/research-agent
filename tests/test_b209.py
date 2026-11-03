from types import SimpleNamespace
import pytest
from test_b5_conditions import task, plan, chunk, run
from test_b6_retrieval import ScreeningModel, response
from test_db206 import audit_response
from research_agent.rag.retrieval import HybridRetriever
from research_agent.workflows.b_recovery import recovery_plan, rescue_candidates
from research_agent.workflows.b_evidence import lexical_index, compact_evidence
from research_agent.workflows.b_audit import audit
from research_agent.integrations.llm import ModelError, ModelQuotaError


def test_new_queries_find_outside_old_pool_without_turning_queries_into_evidence():
    t=task().to_dict();trace={}
    p=recovery_plan(t,plan(),{},None,lambda *a:{'recovery_queries':[{'task_id':'q1','query':'canopy thermography'}]},lambda:300,trace)
    index=lexical_index(HybridRetriever([chunk('a.pdf','Urban cooling studies'),chunk('z.pdf','Canopy thermography shows a thermal contrast.')]))
    rows=rescue_candidates(index,t,p,{'a.pdf'})
    assert [r['filename'] for r in rows]==['z.pdf']
    assert p['criteria'][0]['source_quote'] != 'canopy thermography'
    assert trace['query_recovery']['state']=='replanned'


@pytest.mark.parametrize('value',[None,{}, {'recovery_queries':[{'task_id':'invented','query':'answer'}]}, {'recovery_queries':'bad'}])
def test_bad_replan_preserves_original_task(value):
    t=task().to_dict();trace={}
    p=recovery_plan(t,plan(),{},None,lambda *a:value,lambda:300,trace)
    assert p['targets'][0]['id']=='t0' and p['criteria'][0]['query']==t['input']
    assert trace['query_recovery']['state']=='unavailable'


def test_recovery_budget_and_quota_do_not_loop():
    calls=[];trace={}
    def call(*a):calls.append(1);raise ModelQuotaError('quota')
    recovery_plan(task().to_dict(),plan(),{},None,call,lambda:179,trace)
    assert not calls
    recovery_plan(task().to_dict(),plan(),{},None,call,lambda:300,trace)
    assert calls==[1] and trace['query_recovery']['quota_exhausted']


def test_empty_audit_selects_original_task_evidence_instead_of_bad_plan():
    wrong=plan();wrong['criteria']=[dict(wrong['criteria'][0],query='unrelated algebra theorem')]
    t=task().to_dict();seen=[]
    chunks=[chunk('a.pdf','Unrelated algebra theorem. '*90,'bad',2),chunk('a.pdf','We use satellite remote sensing to measure urban cooling.','good',3)]
    def call(llm,stage,data,maximum):
        seen.extend(data['candidates'][0]['chunks'])
        return {'papers':[audit_response(c) for c in data['candidates']]}
    audit(SimpleNamespace(llm=None),t,wrong,HybridRetriever(chunks),{}, {},None,lambda:300,call,candidate_names=['a.pdf'])
    assert seen[0]['chunk_id']=='good'


def test_nonmatching_own_method_is_available_during_first_screen():
    seen=[]
    def model(data):
        seen.extend(data['candidates'])
        return {'papers':[audit_response(c,'irrelevant','not_met') if data.get('verification') else response(c,'irrelevant') for c in data['candidates']]}
    _,result=run(ScreeningModel(model),[chunk('a.pdf','Urban cooling remote sensing related research.','intro',1),chunk('a.pdf','We measure canopy thermography using drone transects.','own',3)])
    assert any(c['chunk_id']=='own' for c in seen[0]['chunks'])
    assert result['papers']==[]  # 检索表达和候选不是自动批准的理由。


def test_empty_first_round_can_recover_from_fresh_evidence():
    class Model(ScreeningModel):
        def json(self,stage,data,**kw):
            if data.get('recovery'):
                return {'recovery_queries':[{'task_id':'q1','query':'canopy thermography'}]}
            if stage=='query':return plan()
            rows=[]
            for c in data['candidates']:
                found=any('canopy thermography' in x['text'] for x in c['chunks'])
                ok=found and (data.get('verification') or 'review_note' in c)
                rows.append(audit_response(c,'relevant' if ok else 'irrelevant','met' if ok else 'not_met') if data.get('verification') else response(c,'relevant' if ok else 'irrelevant'))
            return {'papers':rows}
    w,result=run(Model(),[chunk('a.pdf','Urban cooling discussion.','intro',1),chunk('a.pdf','Canopy thermography measurements.'.lower(),'method',5)])
    assert [r['filename'] for r in result['papers']]==['a.pdf']
    assert w.retrieval_trace['query_recovery']['state']=='replanned'
