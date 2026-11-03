from research_agent.workflows.b_recovery import uncovered_plan,rescue_candidates
from research_agent.workflows.b_evidence import lexical_index
from research_agent.rag.retrieval import HybridRetriever
from test_b5_conditions import chunk

def test_nonempty_answer_can_have_an_uncovered_target():
    p={'targets':[{'id':'a','text':'urban','query':'urban'},{'id':'b','text':'canopy','query':'canopy'}],
       'criteria':[{'scope':'a'},{'scope':'b'},{'scope':'common'}]}
    d={'first':{'accepted':True,'targets':['a']}}
    gap=uncovered_plan(p,d)
    assert [t['id'] for t in gap['targets']]==['b']
    assert [c['scope'] for c in gap['criteria']]==['b','common']
    d['second']={'accepted':True,'targets':['b']}
    assert uncovered_plan(p,d) is None
    assert uncovered_plan(p,{}) is None

def test_single_target_does_not_force_more_papers():
    assert uncovered_plan({'targets':[{'id':'a'}]}, {'x':{'accepted':True,'targets':[]}}) is None

def test_workflow_recovers_missing_target_without_discarding_existing_match(monkeypatch):
    from research_agent.workflows import b_workflow
    from test_b5_conditions import run
    from test_b6_retrieval import ScreeningModel,response
    from test_db206 import audit_response
    original=b_workflow.recall
    monkeypatch.setenv('RESEARCH_B_CANDIDATE_LIMIT','8')
    monkeypatch.setattr(b_workflow,'recall',lambda *a: [r for r in original(*a) if r['filename']=='first.pdf'])
    class Model(ScreeningModel):
        def json(self,stage,data,**kw):
            if stage=='query':
                return {'targets':[{'id':x,'text':x,'query':x,'source_quote':x} for x in ('urban','canopy')],
                        'criteria':[{'id':x,'scope':x,'kind':'must','text':x,'query':x,'source_quote':x} for x in ('urban','canopy')],'queries':[]}
            rows=[]
            for c in data['candidates']:
                r=audit_response(c) if data.get('verification') else response(c)
                r['targets']=['canopy' if c['filename']=='second.pdf' else 'urban'];rows.append(r)
            return {'papers':rows}
    cs=[chunk('first.pdf','urban study')]+[chunk(f'other{i}.pdf','urban study') for i in range(7)]+[chunk('second.pdf','canopy study')]
    w,r=run(Model(),cs,question='Find urban or canopy studies.')
    assert {'first.pdf','second.pdf'} <= {p['filename'] for p in r['papers']}
    assert w.retrieval_trace['target_gap_rescue']['candidates']==['second.pdf']
