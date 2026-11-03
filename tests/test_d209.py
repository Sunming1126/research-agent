import json
from types import SimpleNamespace
import pytest
from test_db206 import reply
from test_d_formal_20261102 import setup,case
from research_agent.core.session import CaseSession
from research_agent.workflows.d_refinement import refine_remaining
from research_agent.workflows import d_selftest


def executed(*passed):
    return {'state':'tested','results':[{'passed':p} for p in passed]}


@pytest.mark.parametrize('outcome',['improve','regress','unchanged','timeout','invalid','keep'])
def test_second_repair_preserves_best_source_response_pair(tmp_path,monkeypatch,outcome):
    runner,llm=setup(tmp_path,'D-02');path=runner.prepare_checked_implementation(case('D-02'))
    original=path.read_bytes();receipt=(runner.work/'generation-response.json').read_bytes()
    calls=[]
    def completion(stage,prompt,**kw):
        calls.append(stage)
        if stage=='d_review':
            if outcome=='keep':return reply({'decision':'keep','defects':[]})
            if outcome=='invalid':return reply(None)
            return reply({'decision':'repair','defects':[{'material_id':'method:1','source_quote':"request['data']",'explanation':'The field access violates the current contract','counterexample':'A legal small input, derived from method.'}]})
        if outcome=='timeout':raise TimeoutError('timed out')
        return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'def run_experiment(request):\n    return 7\n'}}]}
    llm.completion=completion
    after=executed(True,True) if outcome=='improve' else executed(False,True) if outcome=='regress' else executed(True,False)
    monkeypatch.setattr(d_selftest,'run_tests',lambda *a:after)
    with CaseSession('D-02',1780).activate():
        result=refine_remaining(runner,case('D-02'),path,{'method':'NEW_RUNTIME_CONTRACT'},[{},{}],executed(True,False))
    assert len(calls)<=2
    if outcome=='improve':
        assert result['selected'] and 'return 7' in path.read_text()
        assert 'return 7' in (runner.work/'generation-response.json').read_text()
    else:
        assert not result['selected']
        assert path.read_bytes()==original and (runner.work/'generation-response.json').read_bytes()==receipt


@pytest.mark.parametrize('execution,seconds',[(executed(True,True),1780),(executed(True,False),1000),({'state':'isolation_unavailable','results':[]},1780)])
def test_second_review_only_with_real_failure_and_time(tmp_path,execution,seconds):
    runner,llm=setup(tmp_path,'D-05');path=runner.prepare_checked_implementation(case('D-05'))
    def forbidden(*a,**k):raise AssertionError('unnecessary call')
    llm.completion=forbidden
    with CaseSession('D-05',seconds).activate():
        result=refine_remaining(runner,case('D-05'),path,{},[],execution)
    assert not result['selected']


def test_runner_second_iteration_fixes_only_remaining_failure(tmp_path,monkeypatch):
    runner,llm=setup(tmp_path,'D-02');path=runner.prepare_checked_implementation(case('D-02'));sources=[]
    def completion(stage,prompt,**kw):
        if stage=='d_tests':
            return reply({'tests':[{'name':str(i),'material_id':'method:1','derivation':'Derived independently','request':{'x':i},'expected':i} for i in (1,2)]})
        if stage=='d_review':
            quote='return 1' if sources else "request['data']"
            return reply({'decision':'repair','defects':[{'material_id':'method:1','source_quote':quote,'explanation':'Contract mismatch','counterexample':'A small legal value'}]})
        sources.append(len(sources)+1)
        return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':f'def run_experiment(request):\n    return {len(sources)}\n'}}]}
    llm.completion=completion
    monkeypatch.setattr(d_selftest,'run_tests',lambda source,tests:executed('return 1' in source or 'return 2' in source,'return 2' in source))
    with CaseSession('D-02',1780).activate():runner.review_implementation(case('D-02'),path)
    audit=json.loads((runner.work/'source-review.json').read_text())
    assert audit['selected']=='review-2' and audit['followup']['retest']['passed']==2
    assert sources==[1,2]
