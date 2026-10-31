"""C/E staging must sync bytes before any public artifact is installed."""
import errno
import json

import pytest

from fixtures import FakeLLM, case, papers
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core import utils


def engine_for(kind, tmp_path):
    library = tmp_path / 'papers'
    names = papers(library)
    settings = Settings(tmp_path, tmp_path / 'output', 'synthetic-workspace', [library])
    return ResearchAgentEngine(settings, FakeLLM), case(kind + '-01', names)


@pytest.mark.parametrize('kind', ['C', 'E'])
def test_report_staging_syncs_all_submission_files(kind, tmp_path, monkeypatch):
    engine, task = engine_for(kind, tmp_path)
    actual_sync = utils.os.fsync
    calls = []
    def counted(fd):
        actual_sync(fd)
        calls.append(fd)
    monkeypatch.setattr(utils.os, 'fsync', counted)
    result = engine.solve_sync(task)
    assert len(calls) >= 5, 'MD, JSON, evidence, receipt and pointer must be synced before publication'
    stored = engine.settings.output / f'data/synthetic-workspace/{task["case_id"]}.json'
    assert json.loads(stored.read_text(encoding='utf-8')) == result


@pytest.mark.parametrize('kind', ['C', 'E'])
def test_staging_sync_failure_cannot_publish_a_success(kind, tmp_path, monkeypatch):
    engine, task = engine_for(kind, tmp_path)
    def fail(fd):
        raise OSError(errno.EIO, 'injected staging fsync failure')
    monkeypatch.setattr(utils.os, 'fsync', fail)
    with pytest.raises(OSError, match='injected staging fsync failure'):
        engine.solve_sync(task)
    output = engine.settings.output
    assert not (output / f'data/synthetic-workspace/{task["case_id"]}.json').exists()
    kind_name = '综述' if kind == 'C' else '灵感Idea报告'
    assert not (output / f'data/expert/{task["case_id"]}-{kind_name}-synthetic-workspace.md').exists()
    assert not (output / f'data/synthetic-workspace/work/receipts/{task["case_id"]}.json').exists()
    errors = list((output / f'data/synthetic-workspace/work/{task["case_id"]}').glob('*/error.json'))
    assert len(errors) == 1 and 'injected staging fsync failure' in errors[0].read_text(encoding='utf-8')


def test_default_atomic_writer_retains_its_previous_behavior(tmp_path, monkeypatch):
    def unexpected(fd):
        raise AssertionError('Only explicitly durable report staging should call fsync')
    monkeypatch.setattr(utils.os, 'fsync', unexpected)
    path = tmp_path / 'ordinary.txt'
    utils.atomic_text(path, 'unchanged default writer')
    assert path.read_text(encoding='utf-8') == 'unchanged default writer'
