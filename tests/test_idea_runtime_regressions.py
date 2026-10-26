"""E类预留时间、修订题面边界和正式返回/文件，不把模型失败改成模板成功。"""
import json
import time
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from fixtures import FakeLLM, case, papers
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.session import CaseSession
from research_agent.integrations.llm import ModelError
from research_agent.integrations.prompts import instructions_for
from research_agent.workflows.tasks import TaskHandlers
from research_agent.workflows.report_context import repair_context, audit_request
from research_agent.core.models import Evidence
from research_agent.evidence import EvidenceError
from research_agent.evidence.reporting import validate_citations, repair_paragraphs
from research_agent.evidence.attribution import validate_named_attribution
from research_agent.contracts.validation import validate_result


@pytest.mark.parametrize('stage,reserve', [('extract_batch',90),('verify_claims',70),('report',35),('repair',10),('audit',5)])
def test_idea_stage_reserves_later_work_and_restores_deadline(stage,reserve):
    llm = FakeLLM()
    llm.deadline = time.monotonic()+300
    original = llm.deadline
    handler = TaskHandlers(llm,'platform-id')
    handler._report_function = 'Idea'
    with CaseSession('E-01',170).activate() as session:
        with pytest.raises(ModelError):
            with handler._stage_time(stage):
                assert llm.deadline <= session.deadline-reserve
                raise ModelError('unavailable')
        assert llm.deadline == original


def test_idea_writing_transport_cap_matches_stage_budget_and_restores():
    llm = FakeLLM()
    llm.deadline = time.monotonic() + 300
    llm.REQUEST_TIMEOUT_CAP = 60
    handler = TaskHandlers(llm, 'runtime-dir')
    handler._report_function = 'Idea'
    with CaseSession('E-01', 170).activate() as session:
        with pytest.raises(ModelError):
            with handler._stage_time('report'):
                assert llm.REQUEST_TIMEOUT_CAP == 85
                assert llm.deadline <= session.deadline - 35
                raise ModelError('transport failed')
        assert llm.REQUEST_TIMEOUT_CAP == 60


def test_idea_repair_and_audit_keep_input_observation_and_constraints():
    current = SimpleNamespace(function='Idea',input='模拟观察只包含热暴露，未测试替代路线。',constraints=['不能把模拟观察写成实测效果'])
    repair = repair_context('正文',['正文'],['修正效果来源'],[],case=current)
    audit = audit_request(current,'正文',[],[])
    assert repair['task'] == audit['task']
    assert repair['task']['input'] == current.input and repair['task']['constraints'] == current.constraints
    prompt = instructions_for('report',{'task':{'function':'Idea'}})
    assert '1200至1800' not in prompt
    assert '1800～3200' in prompt and '6000' in prompt
    assert '分子分母' in prompt and '回退' in prompt
    audit_prompt = instructions_for('audit', audit)
    assert '包括未带引用的代码可用性' in audit_prompt and 'ok=true必须且只能搭配issues=[]' in audit_prompt


def test_idea_reads_pdf_text_without_table_grid_reconstruction(tmp_path):
    import fitz
    names = papers(tmp_path/'papers')[:2]
    with fitz.open(tmp_path/'papers'/names[0]) as document:
        document[0].insert_text((40,700),'Table 1: A caption must not trigger grid reconstruction.')
        document.saveIncr()
    settings = Settings(tmp_path,tmp_path/'out','runtime-dir',[tmp_path/'papers'])
    with patch.object(fitz.Page,'find_tables',side_effect=AssertionError('grid reconstruction should be disabled')):
        result = ResearchAgentEngine(settings,FakeLLM).solve_sync(case('E-01',names))
    assert result['path']=='data/expert/E-01-灵感Idea报告-runtime-dir.md'
    assert json.loads((settings.output/'data/runtime-dir/E-01.json').read_text()) == result
    assert (settings.output/result['path']).read_text() == result['markdown']


def test_numeric_parameter_brackets_are_repaired_before_final_submission():
    bad = '拟议参数搜索：top-k候选数：[2,5,8]；验证集选参，不表示已完成实验。'
    fixed = bad.replace('[2,5,8]','（2、5、8）')
    stages = []
    class LLM(FakeLLM):
        def json(self,stage,data,**kwargs):
            stages.append(stage)
            if stage=='report':
                return {'markdown':'# 方案\n\n'+'建议固定实验条件并与基线比较，以独立验证判断假设是否受支持。'*36+'[1, p.1]\n\n'+bad,
                        'used_evidence_ids':['ev-1']}
            if stage=='repair':
                assert data['paragraphs']==[bad] and data['task']['input']=='比较本题给定方法'
                return {'patches':[{'original':bad,'replacement':fixed}]}
            assert stage=='audit' and '[2,5,8]' not in data['markdown']
            return {'ok':True,'issues':[]}
    current = SimpleNamespace(case_id='E-01',function='Idea',input='比较本题给定方法',constraints=[])
    handler = TaskHandlers(LLM(),'runtime-dir')
    handler.ledger = [Evidence('ev-1','论文比较了控制条件。','a.pdf',1,'chunk','The controlled conditions are compared.')]
    result = handler.compose_report(current)
    validate_result(current,result,team='runtime-dir',filenames=['a.pdf'])
    assert stages==['report','repair','audit'] and fixed in result['markdown']


def test_grouped_citations_survive_evidence_check_and_reference_generation():
    current = SimpleNamespace(case_id='E-02',function='Idea',input='比较方法',constraints=[])
    body = '建议比较固定条件下的不同机制，独立验证各自适用范围。'*40+'[1,2]'
    class LLM(FakeLLM):
        def json(self,stage,data,**kwargs):
            if stage=='report':
                return {'markdown':body,'used_evidence_ids':['ev-1','ev-2']}
            return {'ok':True,'issues':[]}
    handler = TaskHandlers(LLM(),'runtime-dir')
    handler.ledger = [Evidence('ev-1','比较控制条件。','a.pdf',1,'a','Controlled conditions.'),
                      Evidence('ev-2','比较机制条件。','b.pdf',2,'b','Mechanistic conditions.')]
    result = handler.compose_report(current)
    validate_result(current,result,team='runtime-dir',filenames=['a.pdf','b.pdf'])
    assert '[1] a.pdf' in result['markdown'] and '[2] b.pdf' in result['markdown']


@pytest.mark.parametrize('repair_succeeds', [True, False])
def test_contradictory_audit_requires_repair_and_keeps_unresolved_review_visible(repair_succeeds):
    bad = '论文代码未公开。'
    fixed = '代码可用性待核查；核查后决定复用实现或按方法自行实现。'
    stages = []
    class LLM(FakeLLM):
        def json(self, stage, data, **kwargs):
            stages.append(stage)
            if stage == 'report':
                return {'markdown': '建议固定实验条件并与基线比较，以独立验证判断假设是否受支持。'*36+'[1, p.1]\n\n'+bad,
                        'used_evidence_ids': ['ev-1']}
            if stage == 'repair':
                assert bad in data['paragraphs']
                return {'patches': [{'original': bad, 'replacement': fixed if repair_succeeds else '论文代码未公开，需自行实现。'}]}
            if repair_succeeds and stages.count('audit') > 1:
                return {'ok': True, 'issues': []}
            return {'ok': True, 'issues': [f'“{bad}”没有原文支持。']}
    current = SimpleNamespace(case_id='E-01', function='Idea', input='提出方案', constraints=[])
    handler = TaskHandlers(LLM(), 'runtime-dir')
    handler.ledger = [Evidence('ev-1', '比较控制条件。', 'a.pdf', 1, 'a', 'Controlled conditions.')]
    if repair_succeeds:
        result = handler.compose_report(current)
        assert fixed in result['markdown'] and bad not in result['markdown']
        assert handler.audit['ok'] is True and handler.audit['issues'] == []
    else:
        result = handler.compose_report(current)
        validate_result(current, result, team='runtime-dir', filenames=['a.pdf'])
        assert handler.audit['ok'] is False and handler.audit['mode'] == 'model_report_needs_review'
        assert '论文代码未公开，需自行实现。' in result['markdown']
    assert stages == ['report', 'audit', 'repair', 'audit']
    original = next(item['audit'] for item in handler.audit['history'] if item['stage'] == 'audit')
    assert original['ok'] is True and original['issues']


def test_math_interval_fix_still_allows_one_semantic_repair():
    interval = '- 输入输出：输出一致性分数s ∈ [0,1]，随后按阈值决策。'
    unrelated = '- 引文：已有机制说明。[1, p.1]'
    unsupported = '论文代码未公开。'
    corrected = '代码可用性待核查，不能由证据缺失推断尚未公开。'
    stages = []
    class LLM(FakeLLM):
        def json(self, stage, data, **kwargs):
            stages.append(stage)
            if stage == 'report':
                return {'markdown': '建议固定实验条件并与基线比较，以独立验证判断假设是否受支持。'*36+'[1, p.1]\n\n'+interval+'\n'+unrelated+'\n\n'+unsupported,
                        'used_evidence_ids': ['ev-1']}
            if stage == 'repair':
                if stages.count('repair') == 1:
                    assert data['paragraphs'] == [interval]
                    assert "'[0,1]'" in data['issues'][0]
                    return {'patches': [{'original': interval, 'replacement': interval.replace('[0,1]', '［0,1］')}]}
                return {'patches': [{'original': unsupported, 'replacement': corrected}]}
            if stages.count('audit') == 1:
                assert data['document_view'] == 'complete_document'
                return {'ok': False, 'issues': [f'“{unsupported}”没有原文支持。']}
            return {'ok': True, 'issues': []}
    current = SimpleNamespace(case_id='E-01', function='Idea', input='提出方案', constraints=[])
    handler = TaskHandlers(LLM(), 'runtime-dir')
    handler.ledger = [Evidence('ev-1', '比较控制条件。', 'a.pdf', 1, 'a', 'Controlled conditions.')]
    result = handler.compose_report(current)
    validate_result(current, result, team='runtime-dir', filenames=['a.pdf'])
    assert corrected in result['markdown'] and unrelated in result['markdown']
    assert '［0,1］' in result['markdown']
    assert stages == ['report', 'repair', 'audit', 'repair', 'audit']


def test_repair_localization_keeps_all_issues_despite_markdown_emphasis():
    first = '- **依据**：该文使用甲算法，但所给证据仅描述密集索引。'
    second = '- 数据集：采用命名基准，尚需核查版本。'
    third = '其他内容保持原样。'
    body = '\n\n'.join([first, second, third])
    issues = ['“依据：该文使用甲算法”超出证据范围。', '“采用命名基准，尚需核查版本”有问题。']
    assert repair_paragraphs(body, issues) == [first, second]
    assert repair_paragraphs(body, [issues[0], '此处未给出可定位的原句。']) == [first, second, third]


def test_multiple_wrong_attributions_are_reported_in_one_repair():
    first = 'ABC使用稠密向量。[2, p.2]'
    second = 'XYZ采用段落融合。[1, p.1]'
    evidence = [{'evidence_id': 'ev-1', 'filename': 'ABC.pdf', 'ref': 1, 'page': 1, 'claim': '稠密检索', 'quote': 'Dense retrieval'},
                {'evidence_id': 'ev-2', 'filename': 'XYZ.pdf', 'ref': 2, 'page': 2, 'claim': '段落融合', 'quote': 'Passage fusion'}]
    with pytest.raises(EvidenceError) as error:
        validate_named_attribution(first+'\n\n'+second, evidence, ['ev-1', 'ev-2'])
    assert 'ABC' in str(error.value) and 'XYZ' in str(error.value)
    assert error.value.passages == [first, second]


def test_revision_audit_supplies_evidence_for_visible_neighbor_paragraphs():
    current = SimpleNamespace(function='Idea', input='比较方法', constraints=[])
    ledger = [Evidence('ev-1', '稠密检索。', 'ABC.pdf', 1, 'a', 'Dense retrieval'),
              Evidence('ev-2', '段落融合。', 'XYZ.pdf', 2, 'b', 'Passage fusion')]
    from dataclasses import asdict
    rows = [{**asdict(e), 'ref': i+1} for i, e in enumerate(ledger)]
    body = '密集检索有条件限制。[1, p.1]\n\n相邻的段落融合机制。[2, p.2]'
    data = audit_request(current, body, rows, ledger,
        patches=[{'original': '密集检索没有限制。[1, p.1]', 'replacement': '密集检索有条件限制。[1, p.1]'}])
    assert data['document_view'] == 'changed_paragraphs_and_neighbors'
    assert '[2, p.2]' in data['full_document']
    assert {row['evidence_id'] for row in data['source_quotes']} == {'ev-1', 'ev-2'}


@pytest.mark.parametrize('invalid_citation', [False, True])
def test_advisory_audit_does_not_bypass_hard_citation_validation(invalid_citation):
    body = '建议固定实验条件并与基线比较，以独立验证判断假设是否受支持。'*36
    body += '[99, p.1]' if invalid_citation else '[1, p.1]'
    class LLM(FakeLLM):
        def json(self, stage, data, **kwargs):
            if stage == 'report':
                return {'markdown': body, 'used_evidence_ids': ['ev-1']}
            raise ModelError('model unavailable')
    current = SimpleNamespace(case_id='E-01', function='Idea', input='提出方案', constraints=[])
    handler = TaskHandlers(LLM(), 'runtime-dir')
    handler.ledger = [Evidence('ev-1', '比较控制条件。', 'a.pdf', 1, 'a', 'Controlled conditions.')]
    if invalid_citation:
        with pytest.raises(EvidenceError, match='Idea model report failed validation'):
            handler.compose_report(current)
        assert handler.audit['mode'] == 'model_report_failed'
    else:
        result = handler.compose_report(current)
        validate_result(current, result, team='runtime-dir', filenames=['a.pdf'])
        assert result['markdown'].startswith(body)
        assert handler.audit['ok'] is False and handler.audit['mode'] == 'model_report_needs_review'
        assert handler.audit['delivered_attempt'] == 1


def test_idea_native_runtime_saves_result_and_keeps_model_failure_visible(tmp_path):
    pytest.importorskip('agenthack')
    from agenthack import AgentRuntime
    from agenthack.telemetry import Telemetry
    from fastapi.testclient import TestClient
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from agent import ResearchAgent
    from research_agent.integrations.http_runtime import install_research_protocol
    names = papers(tmp_path/'papers')[:2]
    settings = Settings(tmp_path,tmp_path/'out','runtime-dir',[tmp_path/'papers'])
    class LLM(FakeLLM):
        broken = False
        def json(self,stage,data,**kwargs):
            if self.broken and stage=='report':
                raise ModelError('injected gateway failure')
            return super().json(stage,data,**kwargs)
    llm = LLM()
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime = AgentRuntime(ResearchAgent(ResearchAgentEngine(settings,lambda:llm)),telemetry=Telemetry(provider))
    install_research_protocol(runtime)
    with TestClient(runtime.app) as client:
        good = client.post('/solve',json=case('E-01',names))
        assert good.status_code==200 and set(good.json())=={'case_id','path','markdown'}
        body = good.json()
        assert (settings.output/body['path']).read_text()==body['markdown']
        assert json.loads((settings.output/'data/runtime-dir/E-01.json').read_text())==body
        llm.broken=True
        bad = client.post('/solve',json=case('E-02',names))
        assert bad.status_code==500
        assert not (settings.output/'data/runtime-dir/E-02.json').exists()
        assert not (settings.output/'data/expert/E-02-灵感Idea报告-runtime-dir.md').exists()
    sessions = [span for span in exporter.get_finished_spans() if span.name=='agent.session']
    assert [span.attributes['success'] for span in sessions]==[True,False]
    provider.shutdown()
