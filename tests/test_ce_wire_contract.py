"""Inject SDK-to-wire corruption; never substitutes a real model or trace."""
import asyncio
import json
import os
import re
from pathlib import Path

import pytest

from fixtures import case
from research_agent.integrations.http_runtime import ResearchRequestAdapter


@pytest.mark.parametrize('kind', ['C', 'E'])
@pytest.mark.parametrize('fault,code', [
    ('none', None), ('missing', 'missing_field'), ('extra', 'extra_field'),
    ('markdown_null', 'type_error'), ('path_array', 'type_error'),
    ('empty_markdown', 'content_constraint'), ('nested', 'nested_structure'),
    ('wrong_case', 'identity_mismatch'),
])
def test_ce_corrupt_wire_is_rejected_with_typed_diff_and_original_sdk_metadata(kind, fault, code, request):
    question = case(kind + '-01')
    report_kind = '综述' if kind == 'C' else '灵感Idea报告'
    result = {'case_id': question['case_id'],
              'path': f'data/expert/{question["case_id"]}-{report_kind}-synthetic-workspace.md',
              'markdown': 'Synthetic wire-only string; complete content is checked separately by the engine.'}
    if fault == 'missing':
        result.pop('markdown')
    elif fault == 'extra':
        result['output'] = {}
    elif fault == 'markdown_null':
        result['markdown'] = None
    elif fault == 'path_array':
        result['path'] = []
    elif fault == 'empty_markdown':
        result['markdown'] = '  '
    elif fault == 'nested':
        result = [result]
    elif fault == 'wrong_case':
        result['case_id'] = kind + '-99'

    async def scenario():
        # Deliberately synthetic SDK fixture identifiers, never native proof.
        metadata = {'trace_id': 'fixture-sdk-trace', 'session_id': 'fixture-sdk-session'}
        async def upstream(scope, receive, send):
            await receive()
            body = {'status': 'success', 'output': {'answer': json.dumps(result)}, 'metadata': metadata}
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': json.dumps(body).encode()})
        delivered = False
        async def receive():
            nonlocal delivered
            if delivered:
                return {'type': 'http.disconnect'}
            delivered = True
            return {'type': 'http.request', 'body': json.dumps(question).encode()}
        messages = []
        async def send(message):
            messages.append(message)
        await ResearchRequestAdapter(upstream, lambda: 180)(
            {'type': 'http', 'path': '/solve', 'method': 'POST', 'headers': []}, receive, send)
        headers = {k.decode(): v.decode() for k, v in messages[0]['headers']}
        body = json.loads(b''.join(m.get('body', b'') for m in messages))
        observed = {'kind': 'synthetic_SDK_wire_failure_injection', 'fault': fault,
                    'sdk_identifiers_are_fixture_values': True, 'target_model_calls': 0,
                    'http_status': messages[0]['status'], 'body': body, 'headers': headers}
        if os.environ.get('P0_CASE_EVIDENCE_DIR'):
            directory = Path(os.environ['P0_CASE_EVIDENCE_DIR'])
            directory.mkdir(parents=True, exist_ok=True)
            name = re.sub('[^a-zA-Z0-9_.-]', '_', request.node.name) + '.json'
            (directory / name).write_text(json.dumps(observed, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if fault == 'none':
            assert messages[0]['status'] == 200 and body == result
        else:
            assert messages[0]['status'] == 502
            issues = body.get('contract_errors', [])
            assert any(item['code'] == code for item in issues)
            assert all('pointer' in item and 'expected' in item for item in issues)
        assert headers.get('x-agenthack-trace-id') == metadata['trace_id']
        assert headers.get('x-agenthack-session-id') == metadata['session_id']
    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['C', 'E'])
def test_engine_keeps_structured_contract_diff_in_failure_evidence(kind, tmp_path, monkeypatch):
    from fixtures import FakeLLM, papers
    from research_agent.core.config import Settings
    from research_agent.core.engine import ResearchAgentEngine
    from research_agent.workflows.tasks import TaskHandlers
    library = tmp_path / 'papers'
    names = papers(library)
    question = case(kind + '-01', names)
    report_kind = '综述' if kind == 'C' else '灵感Idea报告'
    def corrupt_report(self, task, retriever):
        return {'case_id': task.case_id,
                'path': f'data/expert/{task.case_id}-{report_kind}-synthetic-workspace.md',
                'markdown': None}
    monkeypatch.setattr(TaskHandlers, 'handle', corrupt_report)
    output = tmp_path / 'output'
    engine = ResearchAgentEngine(Settings(tmp_path, output, 'synthetic-workspace', [library]), FakeLLM)
    with pytest.raises(ValueError):
        engine.solve_sync(question)
    files = list((output / f'data/synthetic-workspace/work/{question["case_id"]}').glob('*/error.json'))
    assert len(files) == 1
    evidence = json.loads(files[0].read_text(encoding='utf-8'))
    assert any(row['code'] == 'type_error' and row['pointer'] == '/markdown'
               for row in evidence.get('schema_diff', []))
    assert not (output / f'data/synthetic-workspace/{question["case_id"]}.json').exists()
