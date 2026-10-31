"""D/E 独立改进：实质算法错误、证据链缺口与 C 路由隔离。"""
import copy
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from fixtures import case
from research_agent.core.models import CaseContext, Chunk, Evidence
from research_agent.integrations.prompts import instructions_for
from research_agent.integrations.idea_prompt import REQUIREMENTS, audit_gaps
from research_agent.workflows.report_context import audit_request
from research_agent.workflows.idea_sources import idea_ledger
from research_agent.workflows.d05_review import check_dispatch_policy
from research_agent.workflows.d_boundary_cases import boundary_cases
from research_agent.workflows.sample_validation import run_public_sample
from research_agent.workflows.layered_d_runner import LayeredDRunner
from research_agent.core.config import Settings
from research_agent.rag.retrieval import HybridRetriever

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ROOT/'科研助手赛题材料/research-experiments-20261020-source-v1'


def test_non_idea_prompts_equal_committed_baseline():
    old = subprocess.check_output(['git','show','04216cf:src/research_agent/integrations/prompts.py'],cwd=ROOT).decode('utf-8')
    namespace = {'__package__':'research_agent.integrations'}
    exec(compile(old,'<committed-prompts>','exec'),namespace)
    for function in ('Survey','Chat','Literature_Retrieval','Reproduction'):
        for stage in ('report','audit','repair','query','extract','extract_batch','verify_claims'):
            for claims in ([],[{'role':'source_context'}]):
                data={'task':{'function':function},'claims':claims}
                assert instructions_for(stage,data)==namespace['instructions_for'](stage,data)


def test_c_audit_request_equal_committed_baseline():
    old = subprocess.check_output(['git','show','04216cf:src/research_agent/workflows/report_context.py'],cwd=ROOT).decode('utf-8')
    namespace={'__package__':'research_agent.workflows'};exec(compile(old,'<committed-context>','exec'),namespace)
    current=CaseContext.from_dict(case('C-01'))
    ledger=[Evidence('ev-1','quote','a.pdf',2,'c1','quote',role='source_context')]
    rows=[dict(evidence_id='ev-1',claim='quote',filename='a.pdf',page=2,ref=1,role='source_context')]
    for patches in ([],[{'original':'before sentence','replacement':'after sentence [1, p.2]'}]):
        args=(current,'after sentence [1, p.2]',rows,ledger)
        assert audit_request(*args,patches=patches)==namespace['audit_request'](*args,patches=patches)


def test_e_coverage_cannot_be_inferred_from_ok_or_headings():
    body='具体正文中给出分子分母和对照设计。'
    assert audit_gaps({'ok':True},body)
    rows=[dict(key=key,status='supported',excerpt=body) for key in REQUIREMENTS]
    assert audit_gaps({'coverage':rows},body)==[]
    rows[0]['excerpt']='正文没有这句'
    assert audit_gaps({'coverage':rows},body)
    rows[0].update(status='partial',excerpt='')
    assert audit_gaps({'coverage':rows},body)


def test_e_sources_cover_evidence_facets_with_bounded_continuous_quotes():
    chunks=[]
    for name in ('one.pdf','two.pdf'):
        for i,(heading,content) in enumerate([
            ('Method','We propose our method. Encoder input produces a representation. '),
            ('Model','Our model has a second decoder component. '),
            ('Datasets','BenchmarkABC corpus and task split are evaluated. '),
            ('Evaluation','Metric accuracy compares controlled ablations and baselines. '),
            ('Limitations','Computational cost and failure under noisy inputs limit our method. '),
            ('Related work','Prior work method dataset metric results ')]):
            chunks.append(Chunk(name+str(i),name,i+1,heading,content*30,'text','h'))
    current=replace(CaseContext.from_dict(case('E-01')),input='比较指定方法并设计验证')
    ledger,selected=idea_ledger(current,HybridRetriever(chunks),[])
    by_id={c.chunk_id:c for c in selected}
    assert all(e.quote in by_id[e.chunk_id].text for e in ledger)
    for name in ('one.pdf','two.pdf'):
        rows=[e for e in ledger if e.filename==name]
        assert {'method','data','metric','limitation'} <= {e.facet for e in rows}
        assert sum(len(e.quote) for e in rows)<=4200
        assert any('BenchmarkABC' in e.quote for e in rows)


def schedule_data():
    return dict(servers=1,setup_ms=0,horizon_ms=10,flow_weights={'A':1,'B':3},jobs=[
        dict(id='a',flow='A',arrival_ms=0,service_ms=2,deadline_ms=20),
        dict(id='b',flow='A',arrival_ms=0,service_ms=1,deadline_ms=20),
        dict(id='c',flow='B',arrival_ms=0,service_ms=1,deadline_ms=20)])


def trace_result(data,order):
    now=0;trace=[];served={f:0 for f in data['flow_weights']}
    for key in order:
        j=next(j for j in data['jobs'] if j['id']==key)
        trace.append(dict(j,server=0,dispatch_ms=now,setup_ms=0,start_ms=now,end_ms=now+j['service_ms'],wait_ms=now-j['arrival_ms']))
        now+=j['service_ms'];served[j['flow']]+=j['service_ms']
    return dict(trace=trace,dropped=[],served_ms=served)


def test_d05_replay_rejects_fifo_disguised_as_fair_candidate():
    data=schedule_data();fifo=trace_result(data,['a','b','c'])
    fair=trace_result(data,['a','c','b'])
    check_dispatch_policy(data,dict(baseline=fifo,candidate=fair))
    with pytest.raises(ValueError,match='dispatch policy'):
        check_dispatch_policy(data,dict(baseline=fifo,candidate=fifo))


def test_d05_replay_checks_drop_reason_and_exact_window():
    data=schedule_data();data['horizon_ms']=2
    output=trace_result(data,['a']);output['dropped']=[dict(id='b',reason='horizon'),dict(id='c',reason='horizon')]
    check_dispatch_policy(data,dict(baseline=output,candidate=None))
    output['dropped'][0]['reason']='deadline'
    with pytest.raises(ValueError,match='horizon'):
        check_dispatch_policy(data,dict(baseline=output,candidate=None))


def test_d02_broader_cases_check_non_degenerate_search(tmp_path):
    runner=LayeredDRunner(Settings(tmp_path,tmp_path/'out'),tmp_path/'work')
    source=runner.prepare_implementation('D-02')
    materials={key:(MATERIALS/'D-02'/name).read_text(encoding='utf-8') for key,name in
               [('method','method.md'),('protocol','protocol.json'),('sample','sample.json')]}
    report=run_public_sample(source,materials,'D-02',timeout=15)
    assert 'field_conflict_cutoffs_unequal_groups' in report['derived_public_checks']
    source.write_text(source.read_text(encoding='utf-8')+"\noriginal=run_experiment\ndef run_experiment(request):\n    result=original(request)\n    if request['phase']=='run' and len(request['data']['documents'])>10: result['details']['selected']={'k1':99,'b':0,'title_weight':0}\n    return result\n",encoding='utf-8')
    with pytest.raises(ValueError):
        run_public_sample(source,materials,'D-02',timeout=15)


def test_previous_real_model_busy_server_bug_is_detected():
    materials={key:(MATERIALS/'D-05'/name).read_text(encoding='utf-8') for key,name in
               [('method','method.md'),('protocol','protocol.json'),('sample','sample.json')]}
    with pytest.raises(ValueError,match='overlapping jobs'):
        run_public_sample(ROOT/'tests/data/d05_busy_server_generated_source.txt',materials,'D-05',timeout=15)


def test_d02_empty_document_lengths_are_not_inflated():
    materials={key:(MATERIALS/'D-02'/name).read_text(encoding='utf-8') for key,name in
               [('method','method.md'),('protocol','protocol.json'),('sample','sample.json')]}
    with pytest.raises(ValueError,match='BM25'):
        run_public_sample(ROOT/'tests/data/d02_empty_length_generated_source.txt',materials,'D-02',timeout=15)


def test_e_repairs_by_paragraph_id_and_preserves_complete_model_draft():
    from research_agent.workflows.tasks import TaskHandlers
    body='本方案拟议固定预算，使用对照和独立测试集验证新增机制。'*45+'[1, p.1]'
    stages=[]
    class Model:
        def json(self,stage,data,**kwargs):
            stages.append(stage)
            if stage=='report':
                return {'markdown':body,'used_evidence_ids':['ev-1']}
            if stage=='audit':
                complete=stages.count('audit')>1
                return dict(ok=complete,issues=[] if complete else ['需要补足设计链条。'],
                            coverage=[dict(key=key,status='supported' if complete else 'partial',
                                           excerpt='本方案拟议固定预算') for key in REQUIREMENTS])
            if stage=='repair':
                assert data['paragraphs'][0]['text']==body
                return {'patches':[{'paragraph_id':'p1','replacement':body+' 这里补充本题的验证设计。'}]}
            raise AssertionError(stage)
    handler=TaskHandlers(Model(),'test');handler._source_report=True
    handler.ledger=[Evidence('ev-1','We propose a method.','a.pdf',1,'a','We propose a method.',role='source_context')]
    result=handler.compose_report(CaseContext.from_dict(case('E-01')))
    assert stages==['report','audit','repair','audit']
    assert handler.audit['ok'] is True and body in result['markdown']


@pytest.mark.parametrize('bad_quote', [False,True])
def test_e_plan_only_accepts_verbatim_authorized_sources(bad_quote):
    from research_agent.workflows.idea_plan import prepare_plan
    from research_agent.workflows.tasks import TaskHandlers
    from dataclasses import asdict
    facts=[dict(evidence_id='ev-'+str(i),quote='We propose a different mechanism '+str(i)+'.',meaning='论文描述自身机制。') for i in (1,2)]
    class Model:
        def json(self,stage,data,**kwargs):
            assert stage=='extract_batch' and data['purpose']=='idea_plan'
            rows=copy.deepcopy(facts)
            if bad_quote:rows[0]['quote']='Invented claim not in the supplied source.'
            return dict(source_facts=rows,hypothesis='待验证假设',design='拟议接口与回退',experiment='拟议对照实验')
    handler=TaskHandlers(Model(),'test')
    handler.ledger=[Evidence(f['evidence_id'],f['quote'],str(i)+'.pdf',1,'c'+str(i),f['quote'],role='source_context') for i,f in enumerate(facts)]
    rows=[dict(asdict(e),ref=i+1) for i,e in enumerate(handler.ledger)]
    plan=prepare_plan(handler,CaseContext.from_dict(case('E-01')),rows)
    assert (plan is None)==bad_quote
    if bad_quote:assert handler.evidence_audit['idea_plan']['status']=='unavailable'
    else:assert handler.evidence_audit['idea_plan']['semantic_claims_verified'] is False


def test_e_plan_uses_existing_gateway_stage_and_specific_prompt(monkeypatch):
    from research_agent.integrations.llm import GatewayLLM
    from research_agent.integrations.idea_prompt import PLAN_INSTRUCTION
    llm=GatewayLLM();llm.base_url='https://example.invalid';llm.api_key='test';llm.model='test'
    data={'task':{'function':'Idea'},'purpose':'idea_plan','claims':[]}
    def request(system,prompt,requested,remaining):
        assert PLAN_INSTRUCTION in prompt
        return {'usage':{'total_tokens':10},'choices':[{'finish_reason':'stop','message':{'content':'{"source_facts":[]}'}}]}
    monkeypatch.setattr(llm,'_request_once',request)
    assert llm.json('extract_batch',data)['source_facts']==[]


def test_d02_wrong_tie_direction_reports_expected_and_actual_config():
    materials={key:(MATERIALS/'D-02'/name).read_text(encoding='utf-8') for key,name in
               [('method','method.md'),('protocol','protocol.json'),('sample','sample.json')]}
    with pytest.raises(ValueError,match='public expected=.*actual=.*MINIMIZE'):
        run_public_sample(ROOT/'tests/data/d02_tie_direction_generated_source.txt',materials,'D-02',timeout=15)


@pytest.mark.parametrize('key', ['p99',None,{'bad':'id'}])
def test_e_repair_rejects_unknown_or_malformed_paragraph_ids(key):
    from research_agent.workflows.idea_repair import resolve_patches
    from research_agent.evidence import EvidenceError
    with pytest.raises(EvidenceError):
        resolve_patches([{'paragraph_id':key,'replacement':'不能替换未知正文。'}],['原稿中的一个连续段落。'])


@pytest.mark.parametrize('override', [{'key':{}},{'status':{}},{'paragraph_ids':['p999']},{'paragraph_ids':'p1'}])
def test_e_bad_audit_fields_are_diagnostics_not_runtime_errors(override):
    from research_agent.integrations.idea_prompt import audit_gaps, REQUIREMENTS
    rows=[dict(key=key,status='supported',paragraph_ids=['p1']) for key in REQUIREMENTS]
    rows[0].update(override)
    assert audit_gaps({'coverage':rows},'当前模型正文中有具体的设计与验证步骤。')


def test_e_audit_can_locate_multiple_nonadjacent_paragraphs():
    from research_agent.integrations.idea_prompt import audit_gaps, REQUIREMENTS
    rows=[dict(key=key,status='supported',paragraph_ids=['p1','p3']) for key in REQUIREMENTS]
    assert audit_gaps({'coverage':rows},'具体依据。\n\n其他说明。\n\n对应设计及验证。')==[]


@pytest.mark.parametrize('cid,baseline,thinking', [('D-02',False,'enabled'),('D-02',True,'disabled'),('D-05',False,'disabled'),('D-01',False,'disabled')])
def test_d_reasoning_is_case_scoped_and_restored(cid,baseline,thinking):
    from research_agent.workflows.d_generation_policy import generation_window
    class Model:
        REQUEST_TIMEOUT_CAP=60
        RETRY_ATTEMPTS=2
    model=Model(); before=vars(model).copy()
    with generation_window(model,baseline=baseline,case_id=cid):
        assert model.thinking_override==thinking
    assert vars(model)==before


@pytest.mark.parametrize('function,expected',[('Survey','disabled'),('Idea','disabled')])
def test_e_reasoning_never_changes_c_or_shared_defaults(function,expected):
    from research_agent.workflows.reports import ReportWorkflow
    class Model: pass
    workflow=object.__new__(ReportWorkflow)
    workflow.llm=Model();workflow.llm.thinking_override='previous'
    workflow._report_function=function;workflow._source_report=True;workflow.last_draft=''
    with workflow._stage_time('audit'):
        assert workflow.llm.thinking_override==expected
    assert workflow.llm.thinking_override=='previous'


def test_d02_baseline_rescue_instructions_do_not_demand_grid_search():
    from research_agent.workflows.d02_policy import source_prompt
    materials={name:(MATERIALS/'D-02'/filename).read_text(encoding='utf-8') for name,filename in [('method','method.md'),('protocol','protocol.json'),('sample','sample.json')]}
    prompt=source_prompt(CaseContext.from_dict(case('D-02')),materials,baseline_only=True)
    assert '仅生成本次完整基线恢复源码' in prompt
    assert 'phase=rank仍必须返回且只返回{candidate:排名}' in prompt
    assert '每种配置只计算一次开发排名' not in prompt
