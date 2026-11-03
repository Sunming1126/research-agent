import json
import pytest
from research_agent.core.session import CaseSession
from research_agent.workflows.d_review import review_retry_reason
from test_db206 import reply,audit_response
from test_b5_conditions import chunk,run
from test_b6_retrieval import ScreeningModel,response


def test_b_audit_recovers_candidate_missing_from_every_screen():
    def model(data):
        if data.get('verification'):
            return {'papers':[audit_response(c) for c in data['candidates']]}
        return {'papers':[]}
    workflow,result=run(ScreeningModel(model),[chunk('a.pdf','We measure urban cooling using satellites.')])
    assert [p['filename'] for p in result['papers']]==['a.pdf']
    assert workflow.retrieval_trace['pending']==[]
    assert workflow.retrieval_trace['audit']['checked'][0]['previous_status']=='unresolved'


def test_b_empty_result_audit_reaches_beyond_old_twelve_without_fabrication():
    def model(data):
        rows=[]
        for c in data['candidates']:
            ok=data.get('verification') and c['filename']=='23.pdf'
            rows.append(audit_response(c,'relevant' if ok else 'irrelevant','met' if ok else 'not_met')
                        if data.get('verification') else response(c,'irrelevant'))
        return {'papers':rows}
    workflow,result=run(ScreeningModel(model),[chunk(f'{i:02}.pdf','Urban cooling discussion.') for i in range(24)])
    assert [p['filename'] for p in result['papers']]==['23.pdf']
    assert len(workflow.retrieval_trace['audit']['checked'])==24


def test_b_final_review_reserves_deadline(monkeypatch):
    from research_agent.workflows.b_audit import audit
    from research_agent.rag.retrieval import HybridRetriever
    from test_b5_conditions import task,plan
    called=[];trace={}
    audit(None,task().to_dict(),plan(),HybridRetriever([chunk('a.pdf','Urban cooling')]),{},trace,None,
          lambda:70,lambda *a,**k:called.append(True),candidate_names=['a.pdf'])
    assert not called and trace['audit']['budget_seconds']==30


@pytest.mark.parametrize('state,execution,reason',[
 ('ungrounded',{'state':'no_tests'},'ungrounded'),
 ('keep',{'state':'tested','results':[{'passed':False}]},'keep_despite_test_failure'),
 ('keep',{'state':'tested','results':[{'passed':True}]},None),
 ('keep',{'state':'isolation_unavailable','results':[]},None),
 ('repair',{'state':'tested','results':[{'passed':False}]},None)])
def test_d_retry_only_for_incomplete_or_conflicting_review(state,execution,reason):
    assert review_retry_reason({'state':state},execution)==reason


@pytest.mark.parametrize('second_decision',['keep','repair'])
def test_d_failed_test_keep_is_rechecked_without_forcing_repair(tmp_path,monkeypatch,second_decision):
    from test_d_formal_20261102 import setup,case
    from research_agent.workflows import d_selftest
    runner,llm=setup(tmp_path,'D-05');path=runner.prepare_checked_implementation(case('D-05'))
    original=path.read_bytes();receipt=(runner.work/'generation-response.json').read_bytes();calls=[]
    test={'name':'one','material_quote':'NEW_RUNTIME_CONTRACT','derivation':'Derived from this contract','request':{},'expected':1}
    def completion(stage,prompt,**kw):
        calls.append(stage)
        if stage=='d_tests':return reply({'tests':[test]})
        if stage=='d_review':
            if calls.count('d_review')==1 or second_decision=='keep':return reply({'decision':'keep','defects':[]})
            return reply({'decision':'repair','defects':[{'material_quote':'NEW_RUNTIME_CONTRACT','source_quote':"request['data']",'explanation':'Wrong input access','counterexample':'A legal small request'}]})
        return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'def run_experiment(request):\n    return 1\n'}}]}
    llm.completion=completion
    monkeypatch.setattr(d_selftest,'run_tests',lambda source,tests:{'state':'tested','results':[{'passed':'return 1' in source}]})
    with CaseSession('D-05',1780).activate():runner.review_implementation(case('D-05'),path)
    assert calls.count('d_review')==2
    audit=json.loads((runner.work/'source-review.json').read_text())
    assert audit['retry_reason']=='keep_despite_test_failure'
    if second_decision=='keep':
        assert path.read_bytes()==original and (runner.work/'generation-response.json').read_bytes()==receipt
        assert 'd_source' not in calls
    else:
        assert audit['selected']=='review' and audit['retest']['passed']==1


def test_d_invalid_test_response_gets_one_recovery(tmp_path):
    from test_d_formal_20261102 import setup,case
    runner,llm=setup(tmp_path,'D-05');path=runner.prepare_checked_implementation(case('D-05'));calls=[]
    def completion(stage,prompt,**kw):
        calls.append(stage)
        if stage=='d_tests':return reply(None)
        return reply({'decision':'keep','defects':[]})
    llm.completion=completion
    with CaseSession('D-05',1780).activate():runner.review_implementation(case('D-05'),path)
    assert calls==['d_tests','d_tests','d_review'] and path.is_file()


def test_d_material_ids_bind_without_model_copying_or_inventing_quote():
    from research_agent.workflows.d_review import material_segments,review_result
    from research_agent.workflows.d_selftest import parse_tests
    mats={'method':'Return the sorted unique integers in ascending order.'}
    item={'name':'order','material_id':'method:1','derivation':'Deduplicate and sort','request':{'values':[3,1,3]},'expected':[1,3]}
    tests=parse_tests(reply({'tests':[item]}),mats)
    assert tests[0]['material_quote']==mats['method']
    assert parse_tests(reply({'tests':[{**item,'material_id':'method:99'}]}),mats)==[]
    source="def run_experiment(request): return sorted(request['values'])"
    defect={'material_id':'method:1','source_quote':"sorted(request['values'])",'explanation':'Duplicates remain','counterexample':'[3,1,3] should produce [1,3]'}
    assert review_result(reply({'decision':'repair','defects':[defect]}),mats,source)['state']=='repair'
    assert review_result(reply({'decision':'repair','defects':[{**defect,'material_id':'invented'}]}),mats,source)['state']=='ungrounded'


@pytest.mark.skipif(__import__('sys').platform!='linux',reason='Production worker is Linux only')
def test_d_object_string_confusion_is_caught_by_generated_contract_test():
    from research_agent.workflows.d_selftest import run_tests,parse_tests
    mats={'method':'Return an object with field value equal to the input value.'}
    tests=parse_tests(reply({'tests':[{'name':'object-input','material_id':'method:1','derivation':'Copy the value','request':{'value':2},'expected':{'value':2}}]}),mats)
    source='import json\ndef run_experiment(request):\n    return json.dumps(json.loads(request))\n'
    result=run_tests(source,tests)
    assert result['state']=='tested' and result['results'][0]['error']=='TypeError'
    good=run_tests('def run_experiment(request):\n    return {"value":request["value"]}\n',tests)
    assert good['results'][0]['passed']


def test_b_empty_audit_does_not_reuse_strengthened_planner_target():
    from research_agent.workflows.b_audit import audit
    from research_agent.workflows.b_conditions import fallback_plan
    from research_agent.rag.retrieval import HybridRetriever
    from test_b5_conditions import task,plan
    t=task().to_dict();p=plan();p['targets'][0]['text']='Unrequested invented restriction'
    candidate={'filename':'a.pdf','chunks':[{'chunk_id':'a','page':1,'text':'Urban cooling'}]}
    decisions={'a.pdf':{'accepted':False,'status':'irrelevant'}};seen=[]
    def call(llm,stage,data,maximum):
        seen.append(data)
        return {'papers':[audit_response(c) for c in data['candidates']]}
    from types import SimpleNamespace
    audit(SimpleNamespace(llm=None),t,p,HybridRetriever([chunk('a.pdf','Urban cooling')]),decisions,{},None,lambda:300,call)
    assert seen[0]['search_targets']==fallback_plan(t)['targets']
    assert decisions['a.pdf']['accepted']
