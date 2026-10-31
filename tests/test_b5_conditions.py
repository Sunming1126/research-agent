import copy
import hashlib
import json
import time
from pathlib import Path
import pytest
from research_agent.core.models import Chunk,CaseContext
from research_agent.core.session import CaseSession
from research_agent.rag.retrieval import HybridRetriever
from research_agent.integrations.llm import ModelError,ModelQuotaError
from research_agent.workflows.tasks import TaskHandlers
from research_agent.workflows.b_conditions import assess,make_plan,fallback_plan,requested_limit
from research_agent.workflows.b_evidence import recall,window as text_window
from research_agent.workflows.b_workflow import window

ROOT=Path(__file__).resolve().parents[1]
def task(question='Find urban cooling studies using remote sensing; exclude reviews.'):
    return CaseContext.from_dict(dict(case_id='B-01',function='Literature_Retrieval',title='Public synthetic retrieval fixture',input=question,materials=[],constraints=[],expected_output='Return papers with filenames and reasons.',output_schema_ref='Literature_Retrieval'))

def chunk(name,text,id=None,page=1):return Chunk(id or name,name,page,'Method',text,'text','hash')
def plan():
    return {'targets':[{'id':'t','text':'urban cooling','query':'urban cooling','source_quote':'urban cooling'}],
            'criteria':[{'id':'r1','kind':'must','scope':'common','text':'urban cooling','query':'urban cooling','source_quote':'urban cooling'},
                        {'id':'r2','kind':'must','scope':'t','text':'remote sensing','query':'remote sensing','source_quote':'remote sensing'},
                        {'id':'r3','kind':'exclude','scope':'common','text':'reviews','query':'review','source_quote':'exclude reviews'}],'queries':[]}

def judge(candidate,p,states=None):
    return {'filename':candidate['filename'],'reason':'原文提供研究对象与实际方法的证据。','checks':[{'criterion_id':c['id'],
        'status':(states or {}).get(c['id'],'met' if c['kind']=='must' else 'not_met'),
        'evidence':[] if (states or {}).get(c['id'])=='unknown' else [{'chunk_id':candidate['chunks'][0]['chunk_id'],'quote':candidate['chunks'][0]['text'][:150]}]} for c in p['criteria']]}

def candidate():return {'filename':'a.pdf','chunks':[{'chunk_id':'a','text':'We measure urban cooling using satellite remote sensing. This is an original experimental study.'}]}

@pytest.mark.parametrize('states,accepted',[({},True),({'r2':'not_met'},False),({'r2':'unknown'},False),({'r3':'met'},False),({'r3':'unknown'},False)])
def test_all_conditions_and_exclusion(states,accepted):
    c=candidate();d=assess(judge(c,plan(),states),c,plan());assert d['accepted']==accepted

@pytest.mark.parametrize('fault',['quote','id','duplicate','missing','status','evidence_type','reason','filename'])
def test_bad_evidence_cannot_approve(fault):
    c=candidate();r=judge(c,plan())
    if fault=='quote':r['checks'][0]['evidence'][0]['quote']='This sentence was never present in the source.'
    elif fault=='id':r['checks'][0]['evidence'][0]['chunk_id']='another-paper'
    elif fault=='duplicate':r['checks'].append(r['checks'][0])
    elif fault=='missing':r['checks'].pop()
    elif fault=='status':r['checks'][0]['status']=[]
    elif fault=='evidence_type':r['checks'][0]['evidence']={}
    elif fault=='reason':r['reason']=None
    else:r['filename']='invented.pdf'
    if fault in ('quote','id','missing'):
        result=assess(r,c,plan());assert not result['accepted'] and result['missing']
    else:
        with pytest.raises(ModelError):assess(r,c,plan())

def test_independent_targets_are_or_but_common_conditions_stay_and():
    p=plan();p['targets'].append({'id':'other'});p['criteria'].append({'id':'r4','kind':'must','scope':'other'})
    c=candidate();assert assess(judge(c,p,{'r2':'not_met'}),c,p)['targets']==['other']
    assert not assess(judge(c,p,{'r1':'not_met'}),c,p)['accepted']

@pytest.mark.parametrize('question,limit',[('至少三篇',None),('各方向找一篇',None),('比较同一篇的两个实验',None),('找十二篇',12),('合计三篇',3),('Find at least 12 papers',None),('Find 12 papers',12),('Find one paper for each target',None),('最多0篇',0),('Find at most five papers',5)])
def test_explicit_quantity_semantics(question,limit):assert requested_limit(question)==limit

def test_source_grounded_plan_and_bad_scope():
    assert make_plan(plan(),task().to_dict())['criteria']==plan()['criteria']
    bad=plan();bad['criteria'][0]['source_quote']='made up constraint'
    with pytest.raises(ModelError):make_plan(bad,task().to_dict())
    bad=plan();bad['criteria'][0]['scope']=[]
    with pytest.raises(ModelError):make_plan(bad,task().to_dict())

def test_multichannel_keeps_complementary_spans_and_fair_target_order():
    index=HybridRetriever([chunk('a.pdf','urban cooling '+('land temperature '*20),'urban'),chunk('a.pdf','remote sensing satellite measurements','method',8),chunk('b.pdf','protein folding molecular structure','bio')])
    rows=recall(index,[('t1','urban cooling'),('t1','remote sensing'),('t2','protein folding')],2)
    assert {r['filename'] for r in rows}=={'a.pdf','b.pdf'}
    assert {'urban','method'}<=set(next(r for r in rows if r['filename']=='a.pdf')['hits'])

def test_background_exclusion_does_not_reduce_recall():
    index=HybridRetriever([chunk('a.pdf','We compare remote sensing with prior reviews as background; our urban cooling experiment uses satellite observations.')])
    assert recall(index,[('t','urban cooling remote sensing')],5)[0]['filename']=='a.pdf'

def test_query_window_is_contiguous_and_keeps_late_method():
    text='background '*160+'We use remote sensing to quantify urban cooling.'+' remaining text'*100
    result=text_window(text,['remote sensing urban cooling'],350)
    assert result in text and 'We use remote sensing' in result and len(result)<=350

class Model:
    def __init__(self,behavior=None):self.calls=[];self.behavior=behavior
    def json(self,stage,data,**kwargs):
        self.calls.append(stage)
        if stage=='query':
            self.active_plan=plan();return self.active_plan
        data={**data,'condition_plan':getattr(self,'active_plan',plan())}
        value=self.behavior(self,data) if self.behavior else {'papers':[judge(c,data['condition_plan']) for c in data['candidates']]}
        for row in value.get('papers',[]):
            c=next(c for c in data['candidates'] if c['filename']==row['filename'])
            decision=assess(row,c,data['condition_plan'])
            row.update(status='relevant' if decision['accepted'] else 'uncertain' if decision['missing'] else 'irrelevant',
                       evidence_ids=[c['chunks'][0]['chunk_id']],targets=decision['targets'])
        return value

def run(model,chunks,question=None,expander=None):
    h=TaskHandlers(model,'test');h.document_expander=expander
    return h,h.handle(task(question) if question else task(),HybridRetriever(chunks))

def test_no_unrequested_ten_paper_cap_or_lexical_tail_cut():
    cs=[chunk(f'p{i}.pdf','urban cooling remote sensing original experiment with satellite observations.') for i in range(13)]
    h,result=run(Model(),cs);assert len(result['papers'])==13
    assert set(result)=={'case_id','papers'} and all(set(p)=={'filename','reason'} for p in result['papers'])

def test_first_batch_timeout_has_one_recovery_round():
    def behavior(m,data):
        if m.calls.count('retrieve')==1:raise ModelError('model transport timeout')
        return {'papers':[judge(c,data['condition_plan']) for c in data['candidates']]}
    m=Model(behavior);h,r=run(m,[chunk('a.pdf','urban cooling remote sensing original experimental evidence.')]);assert r['papers'] and m.calls==['query','retrieve','retrieve']

def test_later_quota_preserves_only_verified_papers():
    def behavior(m,data):
        if m.calls.count('retrieve')>1:raise ModelQuotaError('quota rejected')
        return {'papers':[judge(c,data['condition_plan']) for c in data['candidates']]}
    m=Model(behavior);h,r=run(m,[chunk(f'p{i}.pdf','urban cooling remote sensing original experimental study.') for i in range(12)])
    assert len(r['papers'])==8 and m.calls.count('retrieve')==2
    assert h.retrieval_trace['outcome']=='partial'

def test_no_valid_draft_does_not_disguise_failure_as_no_matches():
    m=Model(lambda m,d: {'papers':[]})
    with pytest.raises(ModelError):run(m,[chunk('a.pdf','urban cooling remote sensing method under test.')])
    assert m.calls.count('retrieve')==2

def test_unknown_condition_triggers_fulltext_read_once():
    calls=[]
    def expand(names):calls.append(names);return [chunk('a.pdf','urban cooling original study uses remote sensing satellite methods.','full',9)]
    def behavior(m,data):
        return {'papers':[judge(c,data['condition_plan'],{} if any('satellite methods' in x['text'] for x in c['chunks']) else {'r2':'unknown'}) for c in data['candidates']]}
    h,r=run(Model(behavior),[chunk('a.pdf','urban cooling original study; methods are explained later.')],expander=expand)
    assert r['papers'] and calls==[['a.pdf']]

def test_llm_settings_restore_on_failure():
    m=Model();m.RETRY_ATTEMPTS=2;m.deadline=time.monotonic()+180;before=vars(m).copy()
    with CaseSession('B-01',170).activate():
        with pytest.raises(RuntimeError):
            with window(m,'retrieve'):raise RuntimeError('fault')
    assert vars(m)==before

def test_protected_production_files_and_shared_dispatch_only():
    expected={'src/research_agent/workflows/tasks.py': '        if case.function == "Literature_Retrieval":\n            from .b_workflow import retrieve\n            return retrieve(self, case, retriever)\n',
              'src/research_agent/integrations/prompts.py': "    if isinstance(data, dict) and data.get('b_protocol') == 'conditions-v1' and data.get('task', {}).get('function') == 'Literature_Retrieval' and stage in ('query', 'retrieve'):\n        from .b_prompt import QUERY, RETRIEVE\n        return QUERY if stage == 'query' else RETRIEVE\n"}
    baseline=json.loads((ROOT/'tests/fixtures/b5-protected-source-hashes.json').read_text())
    for name,old in baseline.items():
        new=(ROOT/name).read_text(encoding='utf-8')
        if name in expected:
            assert new.count(expected[name])==1;new=new.replace(expected[name],'')
        assert old==hashlib.sha256(new.encode()).hexdigest(),name


def test_compact_document_judgment_does_not_grow_with_condition_count():
    class DetailedModel(Model):
        def json(self,stage,data,**kwargs):
            if stage=='query':
                p=plan();p['criteria']=[{**p['criteria'][0],'id':f'r{i}'} for i in range(12)]
                self.active_plan=p;return p
            return super().json(stage,data,**kwargs)
    h,r=run(DetailedModel(),[chunk(f'p{i}.pdf','urban cooling remote sensing original experimental study.') for i in range(6)])
    assert len(r['papers'])==6 and h.retrieval_trace['rounds'][0]['batch_sizes']==[6]


def test_transport_recovery_does_not_expand_already_loaded_evidence():
    def expand(names):raise AssertionError('Transport failure does not establish a missing-source condition')
    def behavior(m,data):
        if m.calls.count('retrieve')==1:raise ModelError('model transport timeout')
        return {'papers':[judge(c,data['condition_plan']) for c in data['candidates']]}
    h,r=run(Model(behavior),[chunk('a.pdf','urban cooling remote sensing original experimental evidence.')],expander=expand)
    assert r['papers'] and 'expanded_documents' not in h.retrieval_trace


def test_target_local_order_is_not_overridden_by_common_query_popularity(monkeypatch):
    import research_agent.workflows.b_evidence as module
    def search(index,query,limit):
        names=['b.pdf','a.pdf','c.pdf'] if query=='target2' else ['a.pdf','c.pdf','b.pdf']
        return [(name,1,[(1,chunk(name,'authorized source text'))]) for name in names]
    monkeypatch.setattr(module,'search',search)
    rows=recall(None,[('t1','target1'),('t2','target2')]+[('common',f'common{i}') for i in range(5)],2)
    assert [r['filename'] for r in rows]==['a.pdf','b.pdf']


def test_partial_other_target_checks_do_not_discard_a_proven_target():
    p=plan();p['targets'].append({'id':'other'});p['criteria'].append({'id':'r4','kind':'must','scope':'other'})
    c=candidate();row=judge(c,p);row['checks'].pop()
    result=assess(row,c,p)
    assert result['accepted'] and result['targets']==['t'] and result['issues']


def test_pdf_line_hyphen_is_restored_to_actual_source_quote():
    from research_agent.workflows.b_conditions import source_span
    source='We train a dual encoder with ex-\nperimental evidence.'
    assert source_span('with experimental evidence',source)=='with ex-\nperimental evidence'
    assert source_span('with invented evidence',source) is None


def test_all_unknown_evidence_is_a_failure_not_a_valid_empty_search():
    def behavior(m,data):return {'papers':[judge(c,data['condition_plan'],{'r2':'unknown'}) for c in data['candidates']]}
    with pytest.raises(ModelError):run(Model(behavior),[chunk('a.pdf','urban cooling related introduction without methods.')])


def test_negative_gets_at_most_one_review_without_automatic_acceptance():
    def reject(m,data):return {'papers':[judge(c,data['condition_plan'],{'r2':'not_met'}) for c in data['candidates']]}
    h,r=run(Model(reject),[chunk('a.pdf','urban cooling remote sensing original experimental evidence.')])
    assert not r['papers'] and len(h.retrieval_trace['rounds'])==2


def test_selected_span_id_binds_original_text_without_model_transcription():
    c=candidate();c['chunks'][0]['source_chunk_id']='original-page-block'
    item=judge(c,plan())
    for check in item['checks']:check['evidence']=[{'chunk_id':'a'}]
    result=assess(item,c,plan())
    assert result['accepted']
    assert result['checks'][0]['evidence']==[{'chunk_id':'original-page-block','quote':c['chunks'][0]['text']}]


def test_evidence_spans_are_bounded_and_remain_contiguous_source():
    from research_agent.workflows.b_evidence import evidence
    text='We study urban cooling using remote sensing. '*90
    row={'filename':'a.pdf','hits':{'a':(1,chunk('a.pdf',text,'a'))}}
    c=evidence(row,plan())
    assert len(c['chunks'])>1
    assert len({s['chunk_id'] for s in c['chunks']})==len(c['chunks'])
    assert all(len(s['text'])<=360 and s['text'] in text for s in c['chunks'])


def test_borderline_negative_with_an_unknown_still_gets_review():
    c=candidate();result=assess(judge(c,plan(),{'r2':'not_met','r3':'unknown'}),c,plan())
    assert not result['accepted'] and set(result['review_criteria'])=={'r2','r3'}


def test_low_remaining_time_returns_only_already_verified_papers():
    session=CaseSession('B-01',170)
    def behavior(m,data):
        result={'papers':[judge(c,data['condition_plan']) for c in data['candidates']]}
        session.started_at=time.monotonic()-146
        return result
    with session.activate():
        h,r=run(Model(behavior),[chunk(f'p{i}.pdf','urban cooling remote sensing original experimental study.') for i in range(12)])
    assert len(r['papers'])==8 and h.retrieval_trace['outcome']=='partial'
    assert len(h.retrieval_trace['unprocessed'])==4


def test_disjoint_complete_target_quotes_repair_incorrect_common_scopes():
    q='分别查找urban cooling或protein folding，符合任意一种即可。'
    p={'targets':[{'id':'a','text':'urban cooling','source_quote':'urban cooling'}, {'id':'b','text':'protein folding','source_quote':'protein folding'}],
       'criteria':[{'id':'c1','text':'urban cooling','kind':'must','scope':'common','source_quote':'urban cooling'},
                   {'id':'c2','text':'protein folding','kind':'must','scope':'common','source_quote':'protein folding'}]}
    repaired=make_plan(p,task(q).to_dict())
    assert [c['scope'] for c in repaired['criteria']]==['a','b']
    c=candidate();result=assess(judge(c,repaired,{'c2':'unknown'}),c,repaired)
    assert result['accepted'] and result['targets']==['a']


def test_multiple_targets_cannot_silently_collapse_to_common_and():
    p=plan();p['targets'].append({'id':'second','text':'another objective','source_quote':'remote sensing'})
    # Neither target has its own positive predicate; no exact complete-target quote permits guessing a fix.
    p['criteria']=[{'id':'b','text':'study','kind':'must','scope':'common','source_quote':'studies'}]
    with pytest.raises(ModelError,match='own positive condition'):make_plan(p,task().to_dict())
