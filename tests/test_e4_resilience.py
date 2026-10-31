import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fixtures import case
from research_agent.core.models import CaseContext, Evidence
from research_agent.core.session import CaseSession
from research_agent.integrations.llm import ModelError, ModelQuotaError
from research_agent.integrations.idea_prompt import REQUIREMENTS
from research_agent.workflows.tasks import TaskHandlers
from research_agent.workflows.idea_workflow import complete_markdown, call_window
from research_agent.contracts.validation import validate_result
from research_agent.evidence import EvidenceError

ROOT=Path(__file__).resolve().parents[1]
BODY='本方案拟议固定计算资源与数据划分，以独立测试集比较各模块和完整方案，记录效益、代价与失败边界。'*25+'[E:ev-1]'


def run(responses):
    class Model:
        def __init__(self): self.stages=[];self.response_failures=[]
        def json(self,stage,data,**kwargs):
            self.stages.append(stage)
            response=responses.pop(0)
            if callable(response):return response(self,stage,data)
            if isinstance(response,Exception):raise response
            return response
    model=Model();workflow=TaskHandlers(model,'test');workflow._source_report=True
    workflow.ledger=[Evidence('ev-1','We compare controlled experimental conditions.','a.pdf',1,'c','We compare controlled experimental conditions.',role='source_context')]
    return workflow,model


def good_audit(body=BODY):
    return {'ok':True,'issues':[], 'coverage':[dict(key=key,status='supported',paragraph_ids=['p1']) for key in REQUIREMENTS]}


@pytest.mark.parametrize('failure',[ModelError('model returned invalid JSON'),ModelError('model response incomplete'),ModelError('model transport failure (APITimeoutError)'),{'markdown':''},{'markdown':'过短的正文[E:ev-1]'}, {'markdown':BODY.replace('ev-1','ev-99')}])
def test_initial_failure_gets_one_bounded_e_only_retry(failure):
    workflow,model=run([failure,{'markdown':BODY},good_audit()])
    result=workflow.compose_report(CaseContext.from_dict(case('E-01')))
    assert model.stages==['report','report','audit']
    validate_result(CaseContext.from_dict(case('E-01')),result,team='test',filenames=['a.pdf'])


@pytest.mark.parametrize('error',[ModelQuotaError('quota rejected'),ModelError('model gateway is not configured')])
def test_permanent_failure_is_not_retried_or_faked(error):
    workflow,model=run([error])
    with pytest.raises(EvidenceError):workflow.compose_report(CaseContext.from_dict(case('E-01')))
    assert model.stages==['report'] and workflow.last_draft==''


def test_incomplete_response_recovers_only_complete_markdown_field():
    def broken(model,stage,data):
        content='{"markdown":'+json.dumps(BODY)+',"used_evidence_ids":['
        model.response_failures.append({'stage':'report','choices':[{'message':{'content':content}}]})
        raise ModelError('model response incomplete')
    workflow,model=run([broken,good_audit()])
    result=workflow.compose_report(CaseContext.from_dict(case('E-01')))
    assert model.stages==['report','audit']
    assert workflow.audit['history'][0]['stage']=='complete_field_recovery'
    assert BODY.replace('[E:ev-1]','[1, p.1]') in result['markdown']


@pytest.mark.parametrize('content',['{"markdown":"未闭合正文','{"other":"\\"markdown\\":not a field"}', '{"markdown":null}', '{"markdown":"valid","markdown":"duplicate"}'])
def test_partial_or_ambiguous_markdown_is_not_reconstructed(content):
    assert complete_markdown([{'stage':'report','choices':[{'message':{'content':content}}]}]) is None


@pytest.mark.parametrize('failure',[ModelError('transport timeout'),{'ok':'true','issues':[]},{'ok':False,'issues':[{}]}])
def test_audit_failure_preserves_hard_valid_actual_draft(failure):
    workflow,model=run([{'markdown':BODY},failure])
    result=workflow.compose_report(CaseContext.from_dict(case('E-01')))
    assert workflow.audit['ok'] is False and BODY.replace('[E:ev-1]','[1, p.1]') in result['markdown']
    assert model.stages==['report','audit']


@pytest.mark.parametrize('patch',[{'paragraph_id':'p999','replacement':'wrong'},{'paragraph_id':'p1','replacement':'short[E:ev-1]'}, {'paragraph_id':'p1','replacement':BODY.replace('ev-1','ev-99')}])
def test_bad_repair_never_overwrites_previous_valid_draft(patch):
    audit=good_audit();audit.update(ok=False,issues=['改进该段设计。'])
    workflow,model=run([{'markdown':BODY},audit,{'patches':[patch]}])
    result=workflow.compose_report(CaseContext.from_dict(case('E-01')))
    assert BODY.replace('[E:ev-1]','[1, p.1]') in result['markdown']
    assert workflow.audit['history'][-1]['stage']=='repair_unavailable'


def test_exhausted_recovery_never_fabricates_report():
    workflow,model=run([ModelError('invalid JSON'),ModelError('invalid JSON')])
    with pytest.raises(EvidenceError):workflow.compose_report(CaseContext.from_dict(case('E-01')))
    assert model.stages==['report','report'] and workflow.last_draft==''


def test_e_call_restores_shared_instance_and_class_after_failure():
    class Model:
        REQUEST_TIMEOUT_CAP=60
        RETRY_ATTEMPTS=2
    model=Model();model.deadline=time.monotonic()+300;before=vars(model).copy()
    with CaseSession('E-01',170).activate() as session:
        with pytest.raises(RuntimeError):
            with call_window(model,'report'):
                assert model.deadline<=session.deadline-60 and model.RETRY_ATTEMPTS==1
                raise RuntimeError('test')
    assert vars(model)==before and Model.RETRY_ATTEMPTS==2


def test_e4_freezes_all_d_and_c_specific_files():
    import hashlib
    before=json.loads((ROOT/'tests/fixtures/e4-protected-source-hashes.json').read_text())
    allowed={'src/research_agent/workflows/reports.py','src/research_agent/integrations/idea_prompt.py'}
    for name,digest in before.items():
        if name not in allowed:
            assert hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()==digest,name


def test_shared_reports_diff_is_only_e_dispatch():
    import hashlib
    before=json.loads((ROOT/'tests/fixtures/e4-protected-source-hashes.json').read_text())['src/research_agent/workflows/reports.py']
    after=(ROOT/'src/research_agent/workflows/reports.py').read_text(encoding='utf-8')
    guard="        if case.function == 'Idea' and getattr(self, '_source_report', False):\n            from .idea_workflow import compose_idea\n            return compose_idea(self, case)\n"
    assert after.count(guard)==1 and hashlib.sha256(after.replace(guard,'').encode()).hexdigest()==before


def test_no_second_draft_when_only_fifty_seconds_remain():
    from research_agent.core.session import current_session
    def delayed_failure(model,stage,data):
        current_session().started_at-=120
        raise ModelError('model transport failure: timeout')
    workflow,model=run([delayed_failure])
    with CaseSession('E-01',170).activate():
        with pytest.raises(EvidenceError):workflow.compose_report(CaseContext.from_dict(case('E-01')))
    assert model.stages==['report']


@pytest.mark.parametrize('choices',[None,{},[None],[{'message':None}],[{'message':{'content':[]}}]])
def test_malformed_gateway_choices_do_not_break_e_recovery(choices):
    assert complete_markdown([{'stage':'report','choices':choices}]) is None


def test_recovery_does_not_override_provider_content_filter():
    value={'stage':'report','choices':[{'finish_reason':'content_filter','message':{'content':json.dumps({'markdown':BODY})}}]}
    assert complete_markdown([value]) is None


def test_local_repair_keeps_global_paragraph_ids_and_full_context():
    target='这一段需要补充具体输入输出和检测规则。'
    source=BODY+'\n\n'+target
    audit=good_audit();audit.update(ok=False,issues=['p2 中“'+target+'”需要完善。'])
    def repair(model,stage,data):
        assert stage=='repair' and [p['paragraph_id'] for p in data['paragraphs']]==['p2']
        assert data['full_document'].endswith(target)
        return {'patches':[{'paragraph_id':'p2','replacement':'修订后的具体模块使用完整输入，按事先约定的规则验证输出。'}]}
    workflow,model=run([{'markdown':source},audit,repair,good_audit()])
    result=workflow.compose_report(CaseContext.from_dict(case('E-01')))
    assert BODY.replace('[E:ev-1]','[1, p.1]') in result['markdown']
    assert target not in result['markdown'] and '修订后的具体模块' in result['markdown']


def test_repair_cannot_edit_unselected_global_paragraph():
    target='这一段需要补充具体输入输出和检测规则。'
    source=BODY+'\n\n'+target
    audit=good_audit();audit.update(ok=False,issues=['p2 中“'+target+'”需要完善。'])
    workflow,model=run([{'markdown':source},audit,{'patches':[{'paragraph_id':'p1','replacement':BODY+'其他内容。'}]}])
    result=workflow.compose_report(CaseContext.from_dict(case('E-01')))
    assert source.replace('[E:ev-1]','[1, p.1]') in result['markdown']
    assert '其他内容。' not in result['markdown']


def test_numeric_interval_gets_model_local_patch_not_whole_regeneration():
    invalid='拟议阈值的搜索区间为[0,1]，在开发集校准后固定。'
    fixed='拟议阈值的搜索区间为［0,1］，在开发集校准后固定。'
    def repair(model,stage,data):
        assert stage=='repair' and invalid in data['full_document']
        return {'patches':[{'paragraph_id':'p2','replacement':fixed}]}
    workflow,model=run([{'markdown':BODY+'\n\n'+invalid},repair,good_audit()])
    result=workflow.compose_report(CaseContext.from_dict(case('E-01')))
    assert model.stages==['report','repair','audit']
    assert fixed in result['markdown'] and invalid not in result['markdown']
    assert BODY.replace('[E:ev-1]','[1, p.1]') in result['markdown']


def test_failed_format_patch_never_returns_invalid_first_draft():
    invalid=BODY+'\n\n拟议阈值的搜索区间为[0,1]，在开发集校准后固定。'
    workflow,model=run([{'markdown':invalid},ModelError('transport timeout')])
    with pytest.raises(EvidenceError): workflow.compose_report(CaseContext.from_dict(case('E-01')))
    assert workflow.last_draft=='' and model.stages==['report','repair']
