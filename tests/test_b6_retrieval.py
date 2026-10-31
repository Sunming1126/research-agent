"""Coverage, evidence validity and budget regressions for the isolated B6 path."""
import time
import pytest
from test_b5_conditions import chunk,task,plan,run
from research_agent.core.session import CaseSession
from research_agent.rag.retrieval import HybridRetriever
from research_agent.workflows.b_evidence import lexical_index,search,compact_evidence
from research_agent.workflows.b_workflow import assess_screen
from research_agent.integrations.llm import ModelError


def response(c,status='relevant'):
    return dict(filename=c['filename'],reason='本篇实际分析城市绿地与温度的关系。',status=status,
                evidence_ids=[c['chunks'][0]['chunk_id']],targets=['t'])


class ScreeningModel:
    def __init__(self,fn=None):self.calls=[];self.fn=fn
    def json(self,stage,data,**kwargs):
        self.calls.append((stage,data))
        if stage=='query':return plan()
        return self.fn(data) if self.fn else {'papers':[response(c) for c in data['candidates']]}


def test_pdf_lexical_normalization_is_symmetric_and_shared_index_unchanged():
    index=HybridRetriever([chunk('a.pdf','We use a dual-encoder. Sparse/dense retrieval handles repre-\nsentations.')])
    before=[dict(c) for c in index.counts]
    normalized=lexical_index(index)
    assert {'dual','encoder','sparse','dense','representations'}<=normalized.counts[0].keys()
    assert search(normalized,'representations encoder dense')[0][0]=='a.pdf'
    assert index.counts==before and not hasattr(index,'b_normalized')


def test_all_40_documents_are_reviewed_in_five_calls_not_14():
    m=ScreeningModel();h,r=run(m,[chunk(f'{i}.pdf','Urban cooling remote sensing original study.') for i in range(40)])
    assert len(r['papers'])==40
    assert len([s for s,_ in m.calls if s=='retrieve'])==5
    assert h.retrieval_trace['screened_documents']==40


def test_unknown_does_not_stop_unseen_candidates_after_nine_documents():
    session=CaseSession('B-01',170);spent=0
    def fn(data):
        nonlocal spent
        spent+=25;session.started_at=time.monotonic()-spent
        return {'papers':[response(c,'uncertain' if c['filename']=='00.pdf' else 'relevant') for c in data['candidates']]}
    with session.activate():
        h,r=run(ScreeningModel(fn),[chunk(f'{i:02}.pdf','urban cooling remote sensing original study.') for i in range(40)])
    assert h.retrieval_trace['screened_documents']>=24
    assert len(r['papers'])>=23 and h.retrieval_trace['outcome']=='partial'


def test_small_library_zero_lexical_match_still_gets_semantic_screen():
    m=ScreeningModel();h,r=run(m,[chunk('a.pdf','Canopy-associated heat differences are measured in metropolitan districts.')])
    assert r['papers'] and h.retrieval_trace['screened_documents']==1


def test_full_task_judgment_not_invented_condition_matrix_is_authoritative():
    # Plan labels remote sensing mandatory, but this task says it is only a focus.
    h,r=run(ScreeningModel(),[chunk('a.pdf','Urban green cover and temperature are compared using field thermometers.')],
        question='Find urban cooling studies; focus especially on remote sensing; exclude reviews.')
    assert r['papers']
    # The planning model can overstate soft preferences. Its must labels must never
    # be fed back to the final relevance model as if the user wrote them.
    m=ScreeningModel();run(m,[chunk('a.pdf','urban cooling field thermometer study.')])
    for stage,payload in m.calls:
        if stage=='retrieve':assert 'condition_plan' not in payload and 'criteria' not in payload


@pytest.mark.parametrize('fault',['invented_id','other_filename','missing_ids','bad_status','bad_targets'])
def test_bad_screening_output_cannot_approve(fault):
    c={'filename':'a.pdf','chunks':[{'chunk_id':'a','text':'Urban thermal measurements.','page':1}]}
    row=response(c)
    if fault=='invented_id':row['evidence_ids']=['another-paper-id']
    if fault=='other_filename':row['filename']='b.pdf'
    if fault=='missing_ids':row['evidence_ids']=[]
    if fault=='bad_status':row['status']='yes'
    if fault=='bad_targets':row['targets']={}
    with pytest.raises(ModelError):assess_screen(row,c,plan())


def test_missing_or_duplicate_rows_never_become_valid_empty_results():
    def fn(data):
        c=data['candidates'][0];return {'papers':[response(c),response(c)]}
    with pytest.raises(ModelError):run(ScreeningModel(fn),[chunk('a.pdf','urban cooling remote sensing study.')])


def test_uncertain_fulltext_expansion_is_once_and_evidence_is_original():
    calls=[]
    def expand(names):calls.append(names);return [chunk('a.pdf','We measure urban cooling with satellite observations.','deep',11)]
    def fn(data):return {'papers':[response(c,'relevant' if any('satellite observations' in x['text'] for x in c['chunks']) else 'uncertain') for c in data['candidates']]}
    h,r=run(ScreeningModel(fn),[chunk('a.pdf','urban cooling study; method is on a later page.')],expander=expand)
    assert calls==[['a.pdf']] and r['papers']
    assert h.retrieval_trace['decisions']['a.pdf']['evidence'][0]['page']==11


def test_compact_evidence_budget_and_complementary_sections():
    a=chunk('a.pdf','urban cooling introduction. '*80,'intro')
    b=chunk('a.pdf','Methods use satellite remote sensing measurements. '*80,'methods',9)
    c=compact_evidence({'filename':'a.pdf','hits':{'intro':(.9,a),'methods':(.8,b)}},plan())
    assert sum(len(s['text']) for s in c['chunks'])<=1800
    assert {s['chunk_id'] for s in c['chunks']}=={'intro','methods'}
    assert all(s['text'] in {'intro':a.text,'methods':b.text}[s['chunk_id']] for s in c['chunks'])


def test_method_action_not_crowded_out_by_keyword_rich_background():
    intro=chunk('a.pdf','Urban cooling remote sensing original research introduction. '*30,'intro')
    prior=chunk('a.pdf','Prior studies discuss urban cooling remote sensing coverage. '*30,'related',2)
    method=chunk('a.pdf','We use calibrated satellite measurements in our experiment.','method',4)
    c=compact_evidence({'filename':'a.pdf','hits':{'intro':(1,intro),'related':(.9,prior),'method':(.1,method)}},plan())
    assert c['chunks'][1]['chunk_id']=='method'


def test_invalid_condition_does_not_discard_search_only_translation():
    class M(ScreeningModel):
        def json(self,stage,data,**kw):
            if stage=='query':
                return {'targets':[], 'criteria':[{'source_quote':'invented requirement'}],
                        'queries':['forest evapotranspiration satellite water flux',None,123]}
            return super().json(stage,data,**kw)
    h,r=run(M(),[chunk('a.pdf','We use satellites to estimate forest evapotranspiration.')],question='检索森林蒸散的卫星估计研究。')
    assert h.retrieval_trace['condition_plan']['queries']==['forest evapotranspiration satellite water flux']
    assert h.query_plan_status=='original_conditions_fallback' and r['papers']
