"""Only retry identical owned staging writes, never generation/publication."""
import errno
import json

import pytest

from fixtures import FakeLLM
from test_ce_durable_staging import engine_for
from research_agent.core import utils


class CountedModel(FakeLLM):
    def __init__(self):
        self.stages = []
    def json(self, stage, data, **kwargs):
        self.stages.append(stage)
        return super().json(stage, data, **kwargs)


@pytest.mark.parametrize('kind', ['C', 'E'])
def test_transient_staging_failure_retries_same_answer_without_regeneration(kind, tmp_path, monkeypatch):
    engine, task = engine_for(kind, tmp_path)
    model = CountedModel()
    engine.llm_factory = lambda: model
    actual = utils.os.fsync
    calls = []
    def once(fd):
        calls.append(fd)
        if len(calls) == 1:
            raise OSError(errno.EIO, 'injected transient staging sync failure')
        actual(fd)
    monkeypatch.setattr(utils.os, 'fsync', once)
    result = engine.solve_sync(task)
    assert model.stages.count('report') == 1
    root = engine.settings.output / f'data/synthetic-workspace/work/{task["case_id"]}'
    records = list(root.glob('*/storage-recovery.json'))
    assert len(records) == 1
    recovery = json.loads(records[0].read_text(encoding='utf-8'))
    assert len(recovery['attempts']) == 1 and recovery['attempts'][0]['action'] == 'R3_retry_staging'
    assert len(recovery['attempts'][0]['value_sha256']) == 64
    assert result['markdown'] and model.stages.count('report') == 1


@pytest.mark.parametrize('kind', ['C', 'E'])
def test_permanent_staging_io_failure_stops_after_one_retry(kind, tmp_path, monkeypatch):
    engine, task = engine_for(kind, tmp_path)
    calls = []
    def fail(fd):
        calls.append(fd)
        raise OSError(errno.EIO, 'injected persistent staging sync failure')
    monkeypatch.setattr(utils.os, 'fsync', fail)
    with pytest.raises(OSError, match='persistent staging'):
        engine.solve_sync(task)
    assert len(calls) == 2
    assert not (engine.settings.output / f'data/synthetic-workspace/{task["case_id"]}.json').exists()


@pytest.mark.parametrize('kind', ['C', 'E'])
def test_permission_failure_is_not_retried(kind, tmp_path, monkeypatch):
    engine, task = engine_for(kind, tmp_path)
    calls = []
    def denied(fd):
        calls.append(fd)
        raise PermissionError(errno.EACCES, 'injected permission denial')
    monkeypatch.setattr(utils.os, 'fsync', denied)
    with pytest.raises(PermissionError):
        engine.solve_sync(task)
    assert len(calls) == 1
