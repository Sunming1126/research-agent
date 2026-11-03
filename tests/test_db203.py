import json
from types import SimpleNamespace
import pytest
from test_d_formal_20261102 import setup,case
from test_b5_conditions import chunk,run
from test_b6_retrieval import ScreeningModel,response
from research_agent.core.session import CaseSession
from research_agent.workflows.b_workflow import window

@pytest.mark.parametrize("failure", ["truncated", "transport", "quota"])
def test_d_review_failure_preserves_exact_source_response_pair(tmp_path,failure):
    from research_agent.integrations.llm import ModelError,ModelQuotaError
    runner,llm=setup(tmp_path,'D-05')
    path=runner.prepare_checked_implementation(case('D-05'))
    original=path.read_bytes(); receipt=(runner.work/'generation-response.json').read_bytes()
    def review(*args,**kwargs):
        if failure=='transport':raise ModelError('transport')
        if failure=='quota':raise ModelQuotaError('quota')
        return {'choices':[{'finish_reason':'length','message':{'role':'assistant','content':'def broken('}}]}
    llm.completion=review
    with CaseSession('D-05',1780).activate():runner.review_implementation(case('D-05'),path)
    assert path.read_bytes()==original
    assert (runner.work/'generation-response.json').read_bytes()==receipt
    assert json.loads((runner.work/'source-review.json').read_text())['selected']=='original'

def test_d_review_selects_matching_complete_response(tmp_path):
    runner,llm=setup(tmp_path,'D-05');path=runner.prepare_checked_implementation(case('D-05'))
    source="def run_experiment(request):\n    return {'runtime_field': request.get('data', [])}\n"
    def review(stage,prompt,**kwargs):
        assert 'NEW_RUNTIME_CONTRACT' in prompt
        if stage=='d_review':
            content=json.dumps({'decision':'repair','defects':[{'material_quote':'NEW_RUNTIME_CONTRACT','source_quote':"request['data']",'explanation':'Missing optional field','counterexample':'Empty request should return empty array'}]})
            return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':content}}]}
        return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':source}}]}
    llm.completion=review
    with CaseSession('D-05',1780).activate():runner.review_implementation(case('D-05'),path)
    assert path.read_text()==source
    assert json.loads((runner.work/'generation-response.json').read_text())['choices'][0]['message']['content']==source

@pytest.mark.parametrize('seconds,budget',[(500,40000),(1780,1000)])
def test_d_review_skips_when_official_execution_budget_needed(tmp_path,seconds,budget):
    runner,llm=setup(tmp_path,'D-05');path=runner.prepare_checked_implementation(case('D-05'));llm.budget=budget
    with CaseSession('D-05',seconds).activate():runner.review_implementation(case('D-05'),path)
    assert llm.calls==1

def test_b_omitted_response_gets_fulltext_not_same_shallow_evidence():
    calls=[]
    def expand(names):
        calls.append(names)
        return [chunk('a.pdf','We measure urban cooling with satellite observations.','deep',11)]
    def fn(data):
        if not calls:return {'papers':[]}
        return {'papers':[response(c) for c in data['candidates']]}
    handler,result=run(ScreeningModel(fn),[chunk('a.pdf','Urban cooling overview.')],expander=expand)
    assert calls==[['a.pdf']] and result['papers']
    assert handler.retrieval_trace['decisions']['a.pdf']['evidence'][0]['page']==11

def test_b_window_restores_instance_settings():
    llm=SimpleNamespace(REQUEST_TIMEOUT_CAP=60,thinking_override='enabled')
    before=dict(vars(llm))
    with CaseSession('B-02',530).activate():
        with window(llm,'retrieve'):assert llm.REQUEST_TIMEOUT_CAP==90
    assert vars(llm)==before
