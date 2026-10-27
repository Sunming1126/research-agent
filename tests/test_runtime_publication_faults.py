"""Final wire shape and publication rollback boundaries."""
import asyncio
import json
import threading
import time

import pytest

from fixtures import FakeLLM, case, papers
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.session import CaseSession
from research_agent.integrations.http_runtime import ResearchRequestAdapter


@pytest.mark.parametrize('body', [[], None, {'status': 'success', 'metadata': [], 'output': None},
                                  {'status': 'error', 'metadata': {'trace_id': 'real-id'}, 'output': []}])
def test_malformed_upstream_sdk_envelope_is_explicit_502(body):
    async def upstream(scope, receive, send):
        await receive()
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': json.dumps(body).encode()})

    async def scenario():
        received = False
        async def receive():
            nonlocal received
            if received:
                return {'type': 'http.disconnect'}
            received = True
            return {'type': 'http.request', 'body': json.dumps(case('A-01')).encode()}
        messages = []
        async def send(message):
            messages.append(message)
        await ResearchRequestAdapter(upstream, lambda: 180)(
            {'type': 'http', 'path': '/solve', 'method': 'POST', 'headers': []}, receive, send)
        assert messages[0]['status'] == 502
        assert 'invalid research answer' in json.loads(messages[-1]['body'])['detail']
    asyncio.run(scenario())


def test_cancel_signal_never_waits_on_slow_publication_io():
    session = CaseSession('A-01', 10)
    entered, release = threading.Event(), threading.Event()
    failures = []
    def publisher():
        try:
            with session.publication():
                entered.set()
                release.wait(2)
        except TimeoutError:
            failures.append('cancelled')
    worker = threading.Thread(target=publisher)
    worker.start()
    assert entered.wait(1)
    started = time.monotonic()
    try:
        session.cancel()
        assert time.monotonic() - started < .2
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and failures == ['cancelled']
    assert session.publication_state == 'aborted'


def test_json_publish_failure_retracts_markdown_and_receipt(tmp_path, monkeypatch):
    import research_agent.core.engine as core
    library = tmp_path / 'papers'
    names = papers(library)
    engine = ResearchAgentEngine(Settings(tmp_path, tmp_path / 'output', 'runtime-directory', [library]), FakeLLM)
    original = core.os.replace
    fault = {'raised': False}
    def fail_once(source, target):
        if str(target).replace('\\', '/').endswith('/data/runtime-directory/C-01.json') and not fault['raised']:
            fault['raised'] = True
            raise OSError('Injected JSON publication failure')
        return original(source, target)
    monkeypatch.setattr(core.os, 'replace', fail_once)
    with pytest.raises(OSError, match='publication failure'):
        engine.solve_sync(case('C-01', names))
    assert fault['raised']
    assert not (engine.settings.output / 'data/runtime-directory/C-01.json').exists()
    assert not (engine.settings.output / 'data/expert/C-01-综述-runtime-directory.md').exists()
    assert not (engine.settings.output / 'data/runtime-directory/work/receipts/C-01.json').exists()
