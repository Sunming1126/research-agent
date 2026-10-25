"""C/D真实输入写法、引用恢复与客户端结果处理回归。"""
import json
from pathlib import Path
import pytest

from fixtures import FakeLLM, case, papers
from research_agent.core.config import Settings
from research_agent.core.models import CaseContext
from research_agent.core.engine import ResearchAgentEngine
from research_agent.rag.materials import DocumentIndex
from research_agent.workflows.tasks import TaskHandlers
from research_agent.workflows.experiments import parse_metric_specs, resolve_metric_specs

METRICS = [('Macro-F1', '分数'), ('Accuracy', '%'), ('运行时间', '秒')]


@pytest.mark.parametrize('text', [
    '核心指标：Macro-F1（分数）、Accuracy（%）、运行时间（秒）。',
    'name=Macro-F1, unit=分数；name=Accuracy, unit=%；name=运行时间, unit=秒',
    '"name":"Macro-F1","unit":"分数"; "name":"Accuracy","unit":"%"; "name":"运行时间","unit":"秒"',
    '|指标|单位|\n|---|---|\n|Macro-F1|分数|\n|Accuracy|%|\n|运行时间|秒|',
])
def test_metric_input_forms(text):
    assert parse_metric_specs(text) == METRICS


def test_d_protocol_order_and_identity_are_checked(tmp_path):
    settings = Settings(tmp_path / 'data', tmp_path / 'output')
    protocol = settings.workspace / 'experiments/D-01/protocol.json'
    protocol.parent.mkdir(parents=True)
    protocol.write_text(json.dumps({'case_id':'D-01', 'metrics':[dict(name=n,unit=u) for n,u in METRICS]}))
    payload = case('D-01')
    payload['input'] = '按当前protocol.json中的三项指标执行。'
    assert resolve_metric_specs(CaseContext.from_dict(payload), settings) == METRICS
    payload['input'] = 'name=Wrong,unit=分数；name=Accuracy,unit=%；name=运行时间,unit=秒'
    with pytest.raises(ValueError, match='conflict'):
        resolve_metric_specs(CaseContext.from_dict(payload), settings)


def test_d_natural_input_can_save_honest_missing_session(tmp_path, monkeypatch):
    monkeypatch.delenv('RESEARCH_EXPERIMENT_CASE', raising=False)
    settings = Settings(tmp_path, tmp_path / 'output', 'platform-id')
    payload = case('D-01')
    payload['input'] = '核心指标：Macro-F1（分数）、Accuracy（%）、运行时间（秒）。'
    result = ResearchAgentEngine(settings, FakeLLM).solve_sync(payload)
    assert result['status'] == 'materials_missing'
    assert [(row['name'],row['unit']) for row in result['metrics']] == METRICS
    assert all(row['observed'] is None for row in result['metrics'])
    assert json.loads((settings.output/'data/platform-id/D-01.json').read_text()) == result


def test_c_wrong_chunk_id_can_recover_only_exact_quote_in_same_paper(tmp_path):
    names = papers(tmp_path/'papers')
    settings = Settings(tmp_path, tmp_path/'out', material_roots=[tmp_path/'papers'])
    chunks = DocumentIndex(settings,tmp_path/'cache').build(names)
    chunks = [chunk for chunk in chunks if chunk.page == 1]
    class WrongId(FakeLLM):
        supports_batched_extraction = True
        def json(self, stage, data, **kwargs):
            assert stage == 'extract_batch'
            return {'papers':[{'filename':row['filename'], 'claims':[
                {'chunk_id':'missing-id', 'quote':row['chunks'][0]['text'], 'claim':'Quoted evidence from the current paper'}
            ]} for row in data['papers']]}
    handler = TaskHandlers(WrongId(), 'platform-id')
    evidence = handler.extract(CaseContext.from_dict(case('C-01', names)), chunks)
    assert len(evidence) == 5 and {e.filename for e in evidence} == set(names)
    assert all(e.chunk_id != 'missing-id' for e in evidence)


def test_c_model_stages_reserve_time_and_restore_deadline():
    import time
    from research_agent.core.session import CaseSession
    llm = FakeLLM()
    llm.deadline = time.monotonic() + 300
    original = llm.deadline
    handler = TaskHandlers(llm, 'platform-id')
    handler._report_function = 'Survey'
    with CaseSession('C-01', 90).activate() as session:
        with handler._stage_time('report'):
            assert llm.deadline <= session.deadline - 25
            assert llm.deadline <= time.monotonic() + 50
        assert llm.deadline == original
        with pytest.raises(RuntimeError):
            with handler._stage_time('audit'):
                raise RuntimeError('test')
        assert llm.deadline == original


def test_d_partial_measurements_require_exact_current_proof(tmp_path):
    from research_agent.contracts.validation import validate_result
    from research_agent.workflows.experiments import failed_result
    payload = CaseContext.from_dict(case('D-01'))
    result = failed_result('D-01', METRICS, 'failed')
    result['metrics'][0]['observed'] = .4
    proof = {'case_id':'D-01', 'submission':result, 'figure_hashes':{}}
    assert validate_result(payload, result, tmp_path, specs=METRICS, proof=proof) == result
    with pytest.raises(ValueError, match='proof'):
        validate_result(payload, result, tmp_path, specs=METRICS)


def test_d_client_partial_result_is_preserved(tmp_path, monkeypatch):
    import subprocess
    from research_agent.workflows.layered_d_runner import LayeredDRunner
    from research_agent.workflows.experiments import failed_result
    from research_agent.contracts.validation import validate_result
    from PIL import Image
    settings = Settings(tmp_path/'data', tmp_path/'out', 'platform-id')
    runner = LayeredDRunner(settings, tmp_path/'work')
    script = Path('/data/experiments/run_experiment.py')
    original_exists = Path.is_file
    monkeypatch.setattr(Path, 'is_file', lambda p: True if p == script else original_exists(p))
    monkeypatch.setenv('RESEARCH_EXPERIMENT_CASE','D-01')
    source = tmp_path/'generated.py'
    source.write_text('def run_experiment(request): return {}')
    monkeypatch.setattr(runner, 'prepare_checked_implementation', lambda c: source)
    relative = 'data/figure/platform-id/D-01-partial.png'
    figure = runner.work/'experiment'/relative
    figure.parent.mkdir(parents=True)
    Image.new('RGB', (4,4)).save(figure)
    result = failed_result('D-01', METRICS, 'failed')
    result['metrics'][0]['observed'] = .4
    result['figures'] = [dict(path=relative, caption='fixture partial', x_label='x', y_label='y', legend='baseline')]
    monkeypatch.setattr('research_agent.workflows.layered_d_runner.subprocess.run', lambda *a,**kw: subprocess.CompletedProcess([],0,json.dumps(result),''))
    monkeypatch.setattr('research_agent.workflows.layered_d_runner.sha256_file', lambda p: 'fixture-hash')
    got = runner.run(CaseContext.from_dict(case('D-01')),METRICS)
    assert got == result and (settings.output/relative).is_file()
    assert json.loads((runner.work/'platform-submission.json').read_text()) == result
    assert runner.proof['submission'] == result


def test_d_experiment_directory_is_not_parsed_as_paper(tmp_path, monkeypatch):
    root = tmp_path/'data/experiments/D-01'
    root.mkdir(parents=True)
    (root/'protocol.json').write_text(json.dumps({'case_id':'D-01','metrics':[dict(name=n,unit=u) for n,u in METRICS]}))
    (root/'sample.json').write_text('{}')
    settings = Settings(tmp_path/'data',tmp_path/'output', 'platform-id')
    monkeypatch.delenv('RESEARCH_EXPERIMENT_CASE',raising=False)
    monkeypatch.setattr(DocumentIndex, 'build', lambda *a,**k: pytest.fail('D must not build PDF index'))
    payload = case('D-01', ['data/experiments/D-01'])
    payload['input'] = '按当前protocol.json中的三项指标执行。'
    result = ResearchAgentEngine(settings,FakeLLM).solve_sync(payload)
    assert result['status'] == 'materials_missing'


def test_d_postprocessing_error_keeps_platform_result(tmp_path, monkeypatch):
    import subprocess
    from research_agent.workflows.layered_d_runner import LayeredDRunner
    from research_agent.workflows.experiments import failed_result
    runner = LayeredDRunner(Settings(tmp_path/'data',tmp_path/'output'),tmp_path/'work')
    monkeypatch.setenv('RESEARCH_EXPERIMENT_CASE','D-01')
    real = Path.is_file
    monkeypatch.setattr(Path,'is_file',lambda p: True if str(p)=='/data/experiments/run_experiment.py' else real(p))
    monkeypatch.setattr(runner,'prepare_checked_implementation',lambda c: tmp_path/'source.py')
    original = failed_result('D-01',METRICS,'failed')
    original['metrics'][0]['name'] = 'Wrong metric'
    monkeypatch.setattr('research_agent.workflows.layered_d_runner.subprocess.run',lambda *a,**kw: subprocess.CompletedProcess([],0,json.dumps(original),''))
    with pytest.raises(RuntimeError,match='postprocessing'):
        runner.run(CaseContext.from_dict(case('D-01')),METRICS)
    assert json.loads((runner.work/'platform-submission.json').read_text()) == original
    assert (runner.work/'experiment-error.json').is_file()
