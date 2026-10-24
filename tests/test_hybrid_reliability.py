"""回归本轮真实失败：可选补检、标准库兼容、截止顺序和进程树取消。"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from research_agent.core.models import Chunk
from research_agent.core.process import run_case_process
from research_agent.core.session import CaseSession
from research_agent.workflows.sample_worker import validate_source
from research_agent.workflows.tasks import TaskHandlers
from research_agent.core.config import Settings
from research_agent.workflows.layered_d_runner import LayeredDRunner
from research_agent.workflows.sample_validation import PublicSampleError, run_public_sample
from research_agent.workflows.public_probes import public_probes
from unittest.mock import patch
from research_agent.integrations.llm import ModelQuotaError
from research_agent.core.models import CaseContext
from research_agent.workflows.experiment_diagnostics import error_record


def worker(source):
    path = Path(__file__).resolve().parents[1] / 'src/research_agent/workflows/sample_worker.py'
    return subprocess.run([sys.executable, str(path)], input=json.dumps({
        'source': source, 'request': {'case_id': 'test', 'protocol': {'seed': 42}}, 'seconds': 2}),
        text=True, capture_output=True, timeout=4)


def test_literal_standard_import_and_public_getattr_run_without_source_rewrite():
    source = """def run_experiment(request):
    module = __import__('re')
    return {'baseline': [], 'candidate': None, 'details': {
        'tokens': module.findall('[a-z]+', 'safe code'),
        'case': getattr(request, 'get')('case_id'), 'public': hasattr(request, 'get')}}
"""
    result = worker(source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['result']['details'] == {
        'tokens': ['safe', 'code'], 'case': 'test', 'public': True}


@pytest.mark.parametrize('source', ["x = __import__('os')", "name='re';x=__import__(name)",
    "from random import _os", "x = (1).__class__", "import random; x=random._os"])
def test_compatibility_does_not_allow_runtime_or_private_module_escape(source):
    with pytest.raises(ValueError):
        validate_source(source)


def test_getattr_cannot_recover_private_runtime_attribute():
    result = worker("def run_experiment(request):\n    return getattr(request, '__class__')\n")
    assert result.returncode != 0
    assert 'public data attributes only' in result.stdout


def test_deadline_before_horizon_probe_rejects_wrong_drop_reason(tmp_path):
    runner = LayeredDRunner(Settings(tmp_path, tmp_path / 'out'), tmp_path / 'fixture')
    path = runner.prepare_implementation('D-05')
    source = path.read_text(encoding='utf-8')
    source = source.replace('return run_d05(request)', '''result = run_d05(request)
    if request['data']['horizon_ms'] == 6:
        for strategy in ('baseline', 'candidate'):
            for row in result[strategy]['dropped']:
                row['reason'] = 'horizon'
    return result''')
    path.write_text(source, encoding='utf-8')
    root = Path('科研助手赛题材料/x新/research-experiments-20261020-source-v1/D-05')
    materials = {k: (root / n).read_text(encoding='utf-8') for k, n in
                 [('protocol', 'protocol.json'), ('sample', 'sample.json')]}
    with pytest.raises(PublicSampleError) as error:
        run_public_sample(path, materials, 'D-05')
    assert error.value.feedback['probe'] == 'deadline_before_horizon'


@pytest.mark.parametrize('name', ['simultaneous_reservation', 'window_exact_and_pending'])
def test_new_probes_reject_original_unmodified_model_regression(name):
    root = Path('科研助手赛题材料/x新/research-experiments-20261020-source-v1/D-05')
    materials = {k: (root / n).read_text(encoding='utf-8') for k, n in
                 [('protocol', 'protocol.json'), ('sample', 'sample.json')]}
    request = json.loads(materials['sample']); request['protocol'] = json.loads(materials['protocol'])
    selected = next(row for row in public_probes('D-05', request) if row[0] == name)
    source = Path(__file__).parent / 'fixtures/d05-hybrid-v1-repeat.py-text'
    # 仅选择要核对的测试输入；历史模型源码原始字节不变，不作算法修补。
    with patch('research_agent.workflows.sample_validation.public_probes', return_value=[selected]):
        with pytest.raises(PublicSampleError) as error:
            run_public_sample(source, materials, 'D-05')
    assert error.value.feedback['probe'] == name


def test_single_flow_probe_does_not_assume_a_flow_switch():
    rows = public_probes('D-05', {'data': {'flow_weights': {'only': 1}, 'jobs': []}})
    assert 'deadline_before_horizon' not in {row[0] for row in rows}
    same_time = next(row for row in rows if row[0] == 'simultaneous_reservation')
    assert same_time[2]['trace_ids']['baseline'] == same_time[2]['trace_ids']['candidate']


def test_quota_does_not_trigger_three_full_source_repair_attempts(tmp_path):
    material = Path('科研助手赛题材料/x新/research-experiments-20261020-source-v1').resolve()
    runner = LayeredDRunner(Settings(tmp_path, tmp_path / 'out', material_roots=[material]), tmp_path / 'work')
    case = CaseContext.from_dict({'case_id': 'D-01', 'function': 'Reproduction', 'title': 'public',
        'input': 'current public method', 'materials': [], 'constraints': [],
        'expected_output': 'result', 'output_schema_ref': 'Reproduction'})
    with patch.object(runner, 'prepare_generated_implementation', side_effect=ModelQuotaError('quota exhausted')) as call:
        with pytest.raises(ModelQuotaError):
            runner.prepare_checked_implementation(case)
    assert call.call_count == 1
    history = json.loads((runner.work / 'public-selftest.json').read_text())['attempts']
    assert len(history) == 1 and history[0]['code'] == 'ACCOUNT_QUOTA'
    assert history[0]['retryable'] is False


def test_quota_is_classified_separately_from_source_admission_and_algorithm_errors():
    row = error_record(ModelQuotaError('quota exhausted'), 'generation')
    assert row['code'] == 'ACCOUNT_QUOTA'
    assert row['category'] == 'model_quota' and row['retryable'] is False


def test_optional_extraction_reuses_existing_coverage_and_stops_without_losing_claims():
    class LLM:
        remaining_tokens = 23000
        calls = 0

        def output_capacity(self, stage, data, reserve=0, maximum=2000):
            return max(0, min(maximum, self.remaining_tokens - reserve - 3500))

        @contextmanager
        def reserve(self, amount):
            yield

        def json(self, stage, data, **kwargs):
            self.calls += 1; self.remaining_tokens -= 3000
            chunk = data['chunks'][0]
            return {'claims': [{'claim': '经当前授权原文核验的事实', 'chunk_id': chunk['chunk_id'],
                                'quote': chunk['text']}]}

    chunks = [Chunk(str(i), f'paper-{i}.pdf', 1, 'Method', 'An evidence sentence for validation.', 'text', 'h')
              for i in range(5)]
    handler = TaskHandlers(LLM(), 'test')
    handler.ledger = [SimpleNamespace(filename=c.filename) for c in chunks]
    handler.selected_chunks = list(chunks)
    claims = handler.extract(SimpleNamespace(input='补取方法证据', function='Survey'), chunks)
    assert len(claims) == 1 and handler.llm.calls == 1
    assert len(handler.ledger) == 5
    assert len(handler.rejected_claims) == 4
    assert {r['reason'] for r in handler.rejected_claims} == {'optional_extraction_budget_reserved'}


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process group cleanup')
def test_cancel_kills_created_process_group_including_grandchild(tmp_path):
    late = tmp_path / 'late.txt'
    child = "import time,pathlib;time.sleep(1.2);pathlib.Path(" + repr(str(late)) + ").write_text('late')"
    parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c'," + repr(child) + "]);time.sleep(10)"
    event = threading.Event(); timer = threading.Timer(.35, event.set); timer.start()
    try:
        with CaseSession('D-05', 10, cancelled=event).activate(), pytest.raises(TimeoutError):
            run_case_process([sys.executable, '-c', parent], timeout=5, env=os.environ.copy(), reserve=0)
    finally:
        timer.cancel()
    time.sleep(1.3)
    assert not late.exists()
