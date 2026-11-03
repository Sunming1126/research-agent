from types import SimpleNamespace
import json
import pytest
from research_agent.workflows import d_holdout
from research_agent.core.session import CaseSession
from test_db206 import reply
from research_agent.workflows.d_review import review_result

@pytest.mark.parametrize('mode',['regression','wrong_oracle','improvement','timeout','unavailable','duplicate'])
def test_independent_validation_preserves_source_pair(tmp_path,monkeypatch,mode):
    path=tmp_path/'implementation.py'; path.write_text('new')
    receipt=tmp_path/'generation-response.json';receipt.write_bytes(b'new-receipt')
    material={'method':'Use the current contract.'}
    seen=[]
    def completion(stage,prompt,**kw):
        seen.append(prompt)
        if mode=='timeout':raise TimeoutError()
        if stage=='d_review':
            return reply({'decision':'keep','defects':[]}) if mode=='wrong_oracle' else reply({'decision':'repair','defects':[{'material_id':'method:1','source_quote':'new','explanation':'Violates current rule','counterexample':'Legal input x=2'}]})
        return reply({'tests':[{'name':'independent','material_id':'method:1','derivation':'By current rule','request':{'x':2},'expected':2}]})
    llm=SimpleNamespace(supports_source_reports=True,completion=completion)
    runner=SimpleNamespace(llm=llm,work=tmp_path)
    def run(source,tests):
        if mode=='unavailable':return {'state':'isolation_unavailable','results':[]}
        return {'state':'tested','results':[{'passed':source=='old' if mode in ('regression','wrong_oracle') else source=='new'}]}
    monkeypatch.setattr(d_holdout,'run_tests',run)
    previous=[{'request':{'x':2}}] if mode=='duplicate' else []
    with CaseSession('D-02',1780).activate():
        result=d_holdout.validate_revision(runner,SimpleNamespace(case_id='D-02'),path,material,previous,b'old',b'old-receipt')
    assert result['reverted']==(mode=='regression')
    assert path.read_bytes()==(b'old' if mode=='regression' else b'new')
    assert receipt.read_bytes()==(b'old-receipt' if mode=='regression' else b'new-receipt')
    assert 'old-receipt' not in seen[0] and 'new-receipt' not in seen[0]

def test_short_comparison_defect_is_not_discarded():
    r=review_result(reply({'decision':'repair','defects':[{'material_id':'method:1','source_quote':'x<y','explanation':'Must include equality','counterexample':'x=y should pass'}]}),{'method':'Use inclusive comparison.'},'return x<y')
    assert r['state']=='repair'
