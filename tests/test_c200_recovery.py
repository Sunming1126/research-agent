"""C-only recovery and strict citation regression."""
import json
import pytest
from fixtures import case,papers
from test_eval_20261026_regressions import DirectLLM
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.evidence import EvidenceError
from research_agent.workflows.survey_repair import normalize_response,cited_ids,repair_request,apply_repair
ROWS=[{'evidence_id':'ev-1','ref':1,'page':3},{'evidence_id':'ev-2','ref':2,'page':8}]
@pytest.mark.parametrize('token',['[E:ev-1,ev-2]','［E：ev-1，ev-2］','[e: EV-1; ev-2]','[ev-1,ev-2]'])
def test_normalize_explicit_known_ids(token):
    assert normalize_response({'markdown':'事实'+token},ROWS)['markdown']=='事实[E:ev-1,ev-2]'
@pytest.mark.parametrize('token',['[E:ev-999]','[E:ev-1-ev-2]','[E:ev-1','[ev-1 nonsense]','［E：未提供］'])
def test_unknown_ambiguous_ids_rejected(token):
    with pytest.raises(EvidenceError):normalize_response({'markdown':'事实'+token},ROWS)
def test_actual_citations_only():
    assert cited_ids('事实[1, p.3][2]',ROWS)==['ev-1','ev-2']
    assert cited_ids('事实[1, p.99]',ROWS)==[]
    assert cited_ids('事实[1,2]',ROWS)==['ev-1','ev-2']
def test_atomic_patch_rejects_other_paragraph():
    body='第一段完整论断。[E:ev-1]\n\n第二段完整论断。[E:ev-2]'
    data=repair_request({'paragraphs':['[E:ev-2]']},body)
    revised,_=apply_repair(body,{'patches':[{'paragraph_id':'p2','replacement':'第二段修正论断。[E:ev-2]'}]},data)
    assert revised.startswith(body.split('\n\n')[0])
    with pytest.raises(EvidenceError):apply_repair(body,{'patches':[{'paragraph_id':'p1','replacement':'不在范围'}]},data)
def test_overlapping_patches_rejected():
    body='完整的第一段文本，包含待修改的具体论断。[E:ev-1]'
    data=repair_request({'paragraphs':[body]},body)
    with pytest.raises(EvidenceError):apply_repair(body,{'patches':[{'original':body,'replacement':'替换'},
        {'original':'包含待修改的具体论断','replacement':'其它'}]},data)
def solve(tmp_path,llm):
    settings=Settings(tmp_path,tmp_path/'out','runtime-id',[tmp_path/'papers'])
    result=ResearchAgentEngine(settings,lambda:llm).solve_sync(case('C-01',papers(tmp_path/'papers')))
    assert set(result)=={'case_id','path','markdown'}
    assert json.loads((settings.output/'data/runtime-id/C-01.json').read_text(encoding='utf-8'))==result
    assert (settings.output/result['path']).read_text(encoding='utf-8')==result['markdown']
    evidence=json.loads(next((settings.output/'data/runtime-id/work/C-01').glob('*/evidence.json')).read_text(encoding='utf-8'))
    return result,evidence
@pytest.mark.parametrize('metadata',[None,[],['ev-999'],{'bad':True},[{'id':'ev-1'}]])
def test_metadata_cannot_destroy_valid_body(tmp_path,metadata):
    class LLM(DirectLLM):
        def json(self,stage,data,**kwargs):
            value=super().json(stage,data,**kwargs)
            if stage=='report':value['used_evidence_ids']=metadata
            return value
    llm=LLM();result,evidence=solve(tmp_path,llm)
    assert llm.stages==['report','audit']
    assert evidence['audit']['local_validation'] is True
@pytest.mark.parametrize('repair_mode',['bad','good','persistent'])
def test_format_repair_and_bounded_regeneration(tmp_path,repair_mode):
    class LLM(DirectLLM):
        reports=0
        def json(self,stage,data,**kwargs):
            if stage=='repair':
                self.stages.append(stage)
                if repair_mode=='good':
                    target=next(p for p in data['paragraphs'] if '[E:ev-999]' in p['text'])
                    return {'patches':[{'paragraph_id':target['paragraph_id'],'replacement':target['text'].replace('[E:ev-999]','')}]}
                return {'patches':[{'paragraph_id':'p999','replacement':'不存在的段落'}]}
            value=super().json(stage,data,**kwargs)
            if stage=='report':
                self.reports+=1
                if self.reports==1 or repair_mode=='persistent':value['markdown']+='[E:ev-999]'
            return value
    llm=LLM()
    if repair_mode=='persistent':
        with pytest.raises(EvidenceError):solve(tmp_path,llm)
        assert llm.stages==['report','repair','report']
        assert not (tmp_path/'out/data/runtime-id/C-01.json').exists()
    else:
        result,evidence=solve(tmp_path,llm)
        assert llm.stages==(['report','repair','audit'] if repair_mode=='good' else ['report','repair','report','audit'])
        assert 'ev-999' not in result['markdown']
def test_failed_semantic_patch_keeps_valid_draft(tmp_path):
    class LLM(DirectLLM):
        def json(self,stage,data,**kwargs):
            if stage=='audit':return {'ok':False,'issues':['需核查条件']}
            if stage=='repair':return {'patches':[{'paragraph_id':'p999','replacement':'不合法修订'}]}
            return super().json(stage,data,**kwargs)
    result,evidence=solve(tmp_path,LLM())
    assert len(result['markdown'])>800
    assert evidence['audit']['ok'] is False
    assert evidence['audit']['mode']=='model_report_needs_review'

@pytest.mark.parametrize('bad',[None, [], 'ok', {'ok':'true','issues':[]}])
def test_malformed_audit_never_loses_valid_body(tmp_path,bad):
    class LLM(DirectLLM):
        def json(self,stage,data,**kwargs):
            if stage=='audit':return bad
            if stage=='repair':return None
            return super().json(stage,data,**kwargs)
    result,evidence=solve(tmp_path,LLM())
    assert len(result['markdown'])>800
    assert evidence['audit']['ok'] is False

def test_abcde_prompt_isolation():
    import subprocess
    from research_agent.integrations.prompts import instructions_for
    old=subprocess.check_output(['git','show','96ed32d:src/research_agent/integrations/prompts.py']).decode('utf-8')
    namespace={'__package__':'research_agent.integrations'}
    exec(compile(old,'<B6-prompts>','exec'),namespace)
    for function in ('Chat','Literature_Retrieval','Reproduction','Idea'):
        for stage in ('query','retrieve','report','audit','repair'):
            data={'task':{'function':function},'claims':[], 'paragraphs':[], 'b_protocol':'conditions-v1'}
            assert instructions_for(stage,data)==namespace['instructions_for'](stage,data)

def test_recovery_does_not_start_without_time(tmp_path):
    import time
    from research_agent.core.session import current_session
    class LLM(DirectLLM):
        def json(self,stage,data,**kwargs):
            value=super().json(stage,data,**kwargs)
            if stage=='report':
                value['markdown']+='[E:ev-999]'
                session=current_session()
                session.started_at=time.monotonic()-session.timeout+25
            return value
    llm=LLM()
    with pytest.raises(EvidenceError):solve(tmp_path,llm)
    assert llm.stages==['report']

C_PROMPT_GUARD = "    if (stage == 'repair' and isinstance(data, dict) and data.get('task', {}).get('function') == 'Survey'\n            and any(isinstance(p, dict) for p in data.get('paragraphs', []))):\n        from .survey_prompt import REPAIR_INSTRUCTION\n        return REPAIR_INSTRUCTION\n"

def assert_report_isolation():
    import ast,subprocess
    from pathlib import Path
    old=subprocess.check_output(['git','show','96ed32d:src/research_agent/workflows/reports.py']).decode('utf-8')
    new=Path('src/research_agent/workflows/reports.py').read_text(encoding='utf-8')
    def methods(text):
        cls=next(n for n in ast.parse(text).body if isinstance(n,ast.ClassDef))
        return {n.name:ast.dump(n,include_attributes=False) for n in cls.body if isinstance(n,ast.FunctionDef) and n.name!='compose_report'}
    assert methods(old)==methods(new)
    guard="        if case.function == 'Idea' and getattr(self, '_source_report', False):\n            from .idea_workflow import compose_idea\n            return compose_idea(self, case)\n"
    assert old.count(guard)==new.count(guard)==1

def test_shared_report_non_c_methods_unchanged():
    assert_report_isolation()
