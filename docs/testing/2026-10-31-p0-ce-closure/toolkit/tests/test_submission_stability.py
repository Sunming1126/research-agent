"""Cancellation, retry and partial-publication faults; no network or real model calls."""
import asyncio
import json
import threading
import time

import pytest

from fixtures import FakeLLM, case, papers
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.session import CaseSession
from research_agent.core.utils import write_json


def engine_for(tmp_path, model=FakeLLM):
    return ResearchAgentEngine(Settings(tmp_path, tmp_path / 'output', 'runtime-directory', []), model)


def test_cancelled_queued_b_request_does_not_overwrite_existing_answer(tmp_path):
    engine = engine_for(tmp_path)
    target = engine.settings.output / 'data/runtime-directory/B-01.json'
    prior = {'case_id': 'B-01', 'papers': [{'filename': 'prior.pdf', 'reason': 'Earlier committed evidence'}]}
    write_json(target, prior)
    engine.lock.acquire()

    async def scenario():
        task = asyncio.create_task(engine.solve(case('B-01')))
        await asyncio.sleep(.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        engine.lock.release()
        await asyncio.sleep(.16)

    try:
        asyncio.run(scenario())
    finally:
        if engine.lock.locked():
            engine.lock.release()
    assert json.loads(target.read_text(encoding='utf-8')) == prior


def test_receipt_write_failure_does_not_leave_published_success(tmp_path, monkeypatch):
    import research_agent.core.engine as core
    engine = engine_for(tmp_path)
    original = core.write_json

    def fail_receipt(path, value, **kwargs):
        if path.name == 'receipt.json':
            raise OSError('Injected receipt storage failure')
        return original(path, value, **kwargs)

    monkeypatch.setattr(core, 'write_json', fail_receipt)
    with pytest.raises(OSError, match='receipt storage'):
        engine.solve_sync(case('A-01'))
    assert not (engine.settings.output / 'data/runtime-directory/A-01.json').exists()
    assert not (engine.settings.output / 'data/runtime-directory/work/receipts/A-01.json').exists()


def test_failed_retry_does_not_keep_previous_receipt_pointer(tmp_path):
    engine = engine_for(tmp_path)
    engine.solve_sync(case('A-01'))
    pointer = engine.settings.output / 'data/runtime-directory/work/receipts/A-01.json'
    assert pointer.exists()

    class Broken(FakeLLM):
        def json(self, *args, **kwargs):
            raise RuntimeError('Injected gateway failure')

    engine.llm_factory = Broken
    with pytest.raises(RuntimeError, match='gateway failure'):
        engine.solve_sync(case('A-01'))
    assert not pointer.exists(), 'Retry must not advertise the previous successful receipt'


def test_b_auxiliary_diagnostic_failure_does_not_destroy_valid_answer(tmp_path, monkeypatch):
    import research_agent.core.engine as core
    library = tmp_path / 'papers'
    names = papers(library)
    settings = Settings(tmp_path, tmp_path / 'output', 'runtime-directory', [library])
    engine = ResearchAgentEngine(settings, FakeLLM)
    original = core.write_json

    def fail_auxiliary(path, value, **kwargs):
        if path.name == 'b-outcome.json':
            raise OSError('Injected auxiliary diagnostic failure')
        return original(path, value, **kwargs)

    monkeypatch.setattr(core, 'write_json', fail_auxiliary)
    result = engine.solve_sync(case('B-01', names))
    assert result['papers'], 'A committed nonempty retrieval must survive an auxiliary logging error'
    target = settings.output / 'data/runtime-directory/B-01.json'
    assert json.loads(target.read_text(encoding='utf-8')) == result


def test_cancellation_during_markdown_write_leaves_no_public_partial_answer(tmp_path, monkeypatch):
    import research_agent.core.engine as core
    library = tmp_path / 'papers'
    names = papers(library)
    engine = ResearchAgentEngine(Settings(tmp_path, tmp_path / 'output', 'runtime-directory', [library]), FakeLLM)
    cancelled = threading.Event()
    original = core.atomic_text

    def cancel_after_markdown(path, value, **kwargs):
        original(path, value, **kwargs)
        if path.suffix == '.md':
            cancelled.set()

    monkeypatch.setattr(core, 'atomic_text', cancel_after_markdown)
    with pytest.raises(TimeoutError):
        engine.solve_sync(case('C-01', names), cancelled)
    assert not (engine.settings.output / 'data/runtime-directory/C-01.json').exists()
    assert not (engine.settings.output / 'data/expert/C-01-综述-runtime-directory.md').exists()


def test_b_failure_writer_cannot_replace_a_committed_current_request(tmp_path):
    from research_agent.core.b_submission import B_REQUEST, failure_submission
    settings = Settings(tmp_path, tmp_path / 'output', 'runtime-directory', [])
    target = settings.output / 'data/runtime-directory/B-01.json'
    result = {'case_id': 'B-01', 'papers': [{'filename': 'valid.pdf', 'reason': 'Verified original evidence'}]}
    write_json(target, result)
    token = B_REQUEST.set({'outcome': {'result': result, 'result_saved': True, 'outcome': 'matched',
                                     'case_id': 'B-01', 'path': 'data/runtime-directory/B-01.json'}})
    try:
        outcome = failure_submission(settings, type('Case', (), {'case_id': 'B-01'})(), 'LateDiagnosticError')
    finally:
        B_REQUEST.reset(token)
    assert outcome['result'] == result
    assert json.loads(target.read_text(encoding='utf-8')) == result
