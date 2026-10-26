"""B类故障隔离与失败答卷；真实PDF、原生SDK，模型夹具只用于确定前置故障。"""
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip('agenthack')
from agenthack import AgentRuntime
from agenthack.telemetry import Telemetry
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent import ResearchAgent
from fixtures import FakeLLM, case
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.integrations.http_runtime import install_research_protocol


@pytest.fixture
def b_runtime(tmp_path):
    library = tmp_path / 'papers'
    library.mkdir()
    shutil.copyfile('/real-papers/2004.04906-DPR.pdf', library / 'valid.pdf')
    settings = Settings(tmp_path, tmp_path / 'output', 'platform-workspace', [library])
    settings.material_aliases = {'本地论文文件夹内的 PDF': ['papers']}
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    class Model(FakeLLM):
        stages = []

        def json(self, stage, data, **kwargs):
            self.stages.append(stage)
            return super().json(stage, data, **kwargs)

    model = Model()
    model.stages = []
    engine = ResearchAgentEngine(settings, lambda: model)
    runtime = AgentRuntime(ResearchAgent(engine), telemetry=Telemetry(provider))
    install_research_protocol(runtime)
    with TestClient(runtime.app) as client:
        yield client, settings, library, model, exporter
    provider.shutdown()


@pytest.mark.parametrize('fault', ['bad_pdf', 'bad_json', 'bad_text', 'duplicate_copy'])
def test_one_unusable_or_non_pdf_file_does_not_abort_b(b_runtime, fault):
    client, settings, library, model, _ = b_runtime
    if fault == 'bad_pdf':
        (library / '000-broken.pdf').write_bytes(b'not a PDF')
    elif fault == 'bad_json':
        (library / '000-index.json').write_bytes(b'{not valid json')
    elif fault == 'bad_text':
        (library / '000-notes.txt').write_bytes(b'\xff\xfe\x80bad')
    else:
        (library / 'copy').mkdir()
        shutil.copyfile(library / 'valid.pdf', library / 'copy/valid.pdf')
    response = client.post('/solve', json=case('B-01', ['papers']))
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {'case_id', 'papers'}
    assert model.stages, 'Healthy PDF must reach the actual retrieval workflow'
    assert json.loads((settings.output / 'data/platform-workspace/B-01.json').read_text(encoding='utf-8')) == body


def test_missing_material_keeps_b_schema_and_records_real_failure(b_runtime):
    client, settings, _, model, exporter = b_runtime
    response = client.post('/solve', json=case('B-02', ['missing.pdf']))
    assert response.status_code == 200, response.text
    assert response.json() == {'case_id': 'B-02', 'papers': []}
    assert not model.stages
    assert response.headers['x-research-outcome'] == 'failed'
    assert json.loads((settings.output / 'data/platform-workspace/B-02.json').read_text(encoding='utf-8')) == response.json()
    session = next(span for span in exporter.get_finished_spans() if span.name == 'agent.session')
    assert session.attributes['success'] is False, 'Failure trace must not be changed to success'


@pytest.mark.parametrize('scope', ['*.pdf', 'data/papers/*.pdf', '本地论文文件夹下的 PDF'])
def test_explicit_pdf_library_scopes_work(b_runtime, scope):
    client, settings, _, model, _ = b_runtime
    response = client.post('/solve', json=case('B-03', [scope]))
    assert response.status_code == 200, response.text
    assert set(response.json()) == {'case_id', 'papers'} and model.stages
    assert json.loads((settings.output / 'data/platform-workspace/B-03.json').read_text(encoding='utf-8')) == response.json()


@pytest.mark.parametrize('fault', ['all_bad', 'ambiguous', 'model_error', 'timeout', 'outside_scope'])
def test_b_errors_remain_visible_without_invalid_answer(b_runtime, monkeypatch, fault):
    client, settings, library, model, exporter = b_runtime
    materials = ['papers']
    if fault == 'all_bad':
        (library / 'valid.pdf').write_bytes(b'broken')
    elif fault == 'ambiguous':
        (library / 'copy').mkdir()
        (library / 'copy/valid.pdf').write_bytes(b'different content with same filename')
    elif fault in {'model_error', 'timeout'}:
        def fail(*args, **kwargs):
            raise TimeoutError('test deadline') if fault == 'timeout' else RuntimeError('test gateway failure')
        monkeypatch.setattr(model, 'json', fail)
    else:
        materials = ['../private.pdf']
    response = client.post('/solve', json=case('B-01', materials))
    assert response.status_code == 200, response.text
    assert response.json() == {'case_id': 'B-01', 'papers': []}
    assert response.headers['x-research-outcome'] == 'failed'
    assert response.headers['x-research-result-saved'] == 'true'
    assert json.loads((settings.output / 'data/platform-workspace/B-01.json').read_text()) == response.json()
    assert any(s.name == 'agent.session' and s.attributes['success'] is False
               for s in exporter.get_finished_spans())


def test_b_corrupt_cache_is_rebuilt_from_authorized_pdf(b_runtime):
    client, settings, _, model, _ = b_runtime
    payload = case('B-01', ['papers'])
    assert client.post('/solve', json=payload).status_code == 200
    for path in (settings.output / 'data/platform-workspace/work/document-cache').glob('*.json'):
        path.write_text('{broken', encoding='utf-8')
    model.stages.clear()
    response = client.post('/solve', json=payload)
    assert response.status_code == 200 and model.stages
    assert response.headers['x-research-outcome'] != 'failed'


def test_b_write_denied_does_not_claim_saved(b_runtime, monkeypatch):
    import research_agent.core.b_submission as submission
    client, _, _, _, _ = b_runtime
    def denied(*args, **kwargs):
        raise PermissionError('test read-only result directory')
    monkeypatch.setattr(submission, 'write_json', denied)
    response = client.post('/solve', json=case('B-02', ['missing.pdf']))
    assert response.status_code == 500
    assert response.json() == {'case_id': 'B-02', 'papers': []}
    assert response.headers['x-research-result-saved'] == 'false'


def test_b_invalid_assignment_cannot_write_empty_answer(b_runtime):
    client, settings, _, model, _ = b_runtime
    settings.verify_assigned = True  # 没有有效派题文件，不能当作材料缺失提交空答案。
    response = client.post('/solve', json=case('B-01', ['papers']))
    assert response.status_code == 500
    assert not model.stages
    assert not (settings.output / 'data/platform-workspace/B-01.json').exists()


def test_b_reads_real_pdf_text_without_expensive_table_detection(b_runtime, monkeypatch):
    import fitz
    client, _, _, model, _ = b_runtime
    def unexpected(*args, **kwargs):
        raise AssertionError('B should read table text blocks without reconstructing grids')
    monkeypatch.setattr(fitz.Page, 'find_tables', unexpected)
    response = client.post('/solve', json=case('B-01', ['papers']))
    assert response.status_code == 200 and model.stages
    assert response.headers['x-research-outcome'] != 'failed'
