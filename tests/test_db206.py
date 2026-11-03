import json
import sys
import pytest
from research_agent.workflows.d_review import grounded_defects,review_result
from research_agent.workflows.d_selftest import run_tests,parse_tests,same_result,preserves_tests
from research_agent.workflows.b_audit import assess_audit
from test_b5_conditions import chunk,run,task,plan
from test_b6_retrieval import ScreeningModel,response

M={'method':'Return the sorted unique integers in ascending order. Empty input returns an empty list.'}
SOURCE="def run_experiment(request):\n    return sorted(set(request['values']))\n"
def reply(value,fenced=False):
    content=json.dumps(value)
    if fenced:content='```json\n'+content+'\n```'
    return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':content}}]}

def test_fenced_review_and_one_bad_defect_do_not_erase_good_diagnosis():
    d={'material_quote':M['method'],'source_quote':"return sorted( set(request['values']) )",'explanation':'example diagnosis','counterexample':'a small legal input'}
    result=review_result(reply({'decision':'repair','defects':[{'bad':True},d]},True),M,SOURCE)
    assert result['state']=='repair' and result['rejected']==1
    assert result['defects'][0]['source_quote']=="return sorted(set(request['values']))"

@pytest.mark.parametrize('content',[{},None,{'decision':'keep','defects':[{'x':1}]}])
def test_invalid_review_is_distinct_from_keep(content):
    assert review_result(reply(content),M,SOURCE)['state']=='invalid_response'

def test_test_plan_requires_real_material_and_json_expected():
    t={'name':'duplicates','material_quote':M['method'],'derivation':'Sort after deduplication','request':{'values':[3,1,3]},'expected':[1,3]}
    assert parse_tests(reply({'tests':[t]},True),M)==[t]
    assert parse_tests(reply({'tests':[{**t,'material_quote':'an invented method requirement'}]}),M)==[]

@pytest.mark.parametrize('actual,expected,ok',[([1,3],[1,3],True),([3,1],[1,3],False),(True,1,False),(1.000000001,1.0,True),({'x':1,'y':2},{'x':1},False)])
def test_local_result_comparison(actual,expected,ok):assert same_result(actual,expected)==ok

@pytest.mark.skipif(sys.platform!='linux',reason='Production isolation is Linux-only')
def test_isolated_worker_computes_and_denies_files_network_and_processes():
    tests=[{'name':'order','request':{'values':[3,1,3]},'expected':[1,3]}]
    good=run_tests(SOURCE,tests)
    assert good['state']=='tested' and good['results'][0]['passed'],good
    for statement in ["return open('/etc/passwd').read()", "import socket; return socket.socket()", "import os; return os.fork()", "import subprocess; return subprocess.run(['echo','x'])"]:
        blocked=run_tests('def run_experiment(request):\n    '+statement+'\n',tests)
        assert blocked['state']=='tested' and not blocked['results'][0]['passed'],blocked
        assert blocked['results'][0]['error']

@pytest.mark.skipif(sys.platform!='linux',reason='Production isolation is Linux-only')
def test_wrong_algorithm_is_detected_and_regressing_repair_rejected():
    tests=[{'name':'duplicates','request':{'values':[3,1,3]},'expected':[1,3]}, {'name':'empty','request':{'values':[]},'expected':[]}]
    wrong=run_tests("def run_experiment(request):\n    return sorted(request['values'])\n",tests)
    good=run_tests(SOURCE,tests)
    assert [r['passed'] for r in wrong['results']]==[False,True]
    assert preserves_tests(wrong,good) and not preserves_tests(good,wrong)


def audit_response(c,status='relevant',verdict='met'):
    row=response(c,status)
    row['requirements']=[{'task_quote':'urban cooling','verdict':verdict,'evidence_ids':[c['chunks'][0]['chunk_id']]}]
    return row


def test_b_valid_ids_alone_cannot_approve_a_contradicted_condition():
    c={'filename':'a.pdf','chunks':[{'chunk_id':'a','text':'Review of urban cooling','page':1}]}
    with pytest.raises(Exception):assess_audit(audit_response(c,verdict='not_met'),c,task().to_dict(),plan())
    row=audit_response(c);row['requirements'][0]['task_quote']='unrequested year cutoff'
    with pytest.raises(Exception):assess_audit(row,c,task().to_dict(),plan())


def test_b_terminal_fulltext_audit_corrects_false_positive_and_false_negative():
    def model(data):
        rows=[]
        for c in data['candidates']:
            if data.get('verification'):
                relevant=c['filename']=='methods.pdf'
                rows.append(audit_response(c,'relevant' if relevant else 'irrelevant','met' if relevant else 'not_met'))
            else:rows.append(response(c,'irrelevant' if c['filename']=='methods.pdf' else 'relevant'))
        return {'papers':rows}
    def expand(names):
        return [chunk(n,'We measure urban cooling using satellites.' if n=='methods.pdf' else 'We only review prior urban cooling studies.',n+'-full',12) for n in names]
    h,result=run(ScreeningModel(model),[chunk(n,'Urban cooling discussion.') for n in ('methods.pdf','review.pdf')],expander=expand)
    assert [p['filename'] for p in result['papers']]==['methods.pdf']
    assert len(h.retrieval_trace['audit']['checked'])==2


def test_b_malformed_audit_preserves_previous_sourced_approval():
    m=ScreeningModel(lambda data:{'papers':[] if data.get('verification') else [response(c) for c in data['candidates']]})
    h,result=run(m,[chunk('a.pdf','We measure urban cooling.')])
    assert result['papers'] and h.retrieval_trace['audit']['errors']


def test_d_regressing_rewrite_preserves_original_source_response_pair(tmp_path,monkeypatch):
    from test_d_formal_20261102 import setup,case
    from research_agent.core.session import CaseSession
    from research_agent.workflows import d_selftest
    runner,llm=setup(tmp_path,'D-02');path=runner.prepare_checked_implementation(case('D-02'))
    original=path.read_bytes();receipt=(runner.work/'generation-response.json').read_bytes()
    tests={'tests':[{'name':'one','material_quote':'NEW_RUNTIME_CONTRACT','derivation':'Direct contract consequence','request':{},'expected':1}]}
    def completion(stage,prompt,**kw):
        if stage=='d_tests':return reply(tests)
        if stage=='d_review':return reply({'decision':'repair','defects':[{'material_quote':'NEW_RUNTIME_CONTRACT','source_quote':"request['data']",'explanation':'A grounded hypothesis','counterexample':'A small legal input'}]})
        return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'def run_experiment(request):\n    return 0\n'}}]}
    llm.completion=completion
    monkeypatch.setattr(d_selftest,'run_tests',lambda source,tests:{'state':'tested','results':[{'passed':'runtime_field' in source}]})
    with CaseSession('D-02',1780).activate():runner.review_implementation(case('D-02'),path)
    assert path.read_bytes()==original and (runner.work/'generation-response.json').read_bytes()==receipt
    assert json.loads((runner.work/'source-review.json').read_text())['repair']=='rejected_test_regression'


def test_d_empty_invalid_test_plan_retries_once_and_does_not_prevent_formal_candidate(tmp_path):
    from test_d_formal_20261102 import setup,case
    from research_agent.core.session import CaseSession
    runner,llm=setup(tmp_path,'D-02');path=runner.prepare_checked_implementation(case('D-02'))
    calls=[]
    def completion(stage,prompt,**kw):
        calls.append(stage)
        if stage=='d_tests':return reply({'tests':[{'name':'incomplete'}]})
        return reply({'decision':'keep','defects':[]})
    llm.completion=completion
    with CaseSession('D-02',1780).activate():runner.review_implementation(case('D-02'),path)
    assert calls==['d_tests','d_tests','d_review'] and path.is_file()
    audit=json.loads((runner.work/'source-review.json').read_text())
    assert audit['selftest']['rejected']==['missing_fields'] and audit['selected']=='original'


def test_b_condition_ids_bind_to_original_task_without_retyping_chinese():
    from research_agent.workflows.b_audit import task_segments
    t=task().to_dict();c={'filename':'a.pdf','chunks':[{'chunk_id':'a','text':'Urban cooling using satellites.','page':1}]}
    row=response(c);row['requirements']=[{'task_id':task_segments(t)[0]['id'],'verdict':'met','evidence_ids':['a']}]
    assert assess_audit(row,c,t,plan())['requirements'][0]['task_quote']==task_segments(t)[0]['text']
    row['requirements'][0]['task_id']='invented'
    with pytest.raises(Exception):assess_audit(row,c,t,plan())


def test_integer_contract_does_not_accept_float_or_overflow():
    assert not same_result(1.0,1)
    assert same_result(10**1000,10**1000)
    assert not same_result(10**1000,1.0)


def test_b_irrelevant_extra_condition_does_not_erase_grounded_counterevidence():
    c={'filename':'review.pdf','chunks':[{'chunk_id':'a','text':'We only review previous urban cooling studies.','page':1}]}
    row=audit_response(c,'irrelevant','not_met')
    row['requirements'].append({'task_quote':'urban cooling','verdict':'met','evidence_ids':[]})
    result=assess_audit(row,c,task().to_dict(),plan())
    assert not result['accepted'] and result['requirements'][0]['verdict']=='not_met'
    assert result['rejected_requirements']==['B audit requirement lacks evidence']
    # 同样的缺证据不能用来批准，或在没有有效反证时排除论文。
    row['requirements'][0]['verdict']='met'
    with pytest.raises(Exception):assess_audit(row,c,task().to_dict(),plan())
    row['status']='relevant';row['targets']=['T1']
    with pytest.raises(Exception):assess_audit(row,c,task().to_dict(),plan())
