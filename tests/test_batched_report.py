"""报告批量提取仍逐篇绑定连续原文，不允许混用论文或漏掉指定论文。"""
import pytest

from fixtures import FakeLLM, case, papers
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.models import CaseContext
from research_agent.evidence import EvidenceError
from research_agent.rag.materials import DocumentIndex
from research_agent.workflows.tasks import TaskHandlers


class BatchedLLM(FakeLLM):
    supports_batched_extraction = True

    def __init__(self):
        super().__init__()
        self.stages = []

    def json(self, stage, data, **kwargs):
        self.stages.append(stage)
        if stage == 'extract_batch':
            return {'papers': [{'filename': row['filename'], 'claims': super(BatchedLLM, self).json(
                'extract', {'chunks': row['chunks']})['claims']} for row in data['papers']]}
        return super().json(stage, data, **kwargs)


def test_five_paper_report_uses_one_batch_and_persists_valid_result(tmp_path):
    names = papers(tmp_path / 'papers')
    llm = BatchedLLM()
    settings = Settings(tmp_path, tmp_path / 'output', 'platform-dir', [tmp_path / 'papers'])
    result = ResearchAgentEngine(settings, lambda: llm).solve_sync(case('C-01', names))
    assert result['case_id'] == 'C-01'
    assert llm.stages.count('extract_batch') == 1
    assert 'query' not in llm.stages and 'extract' not in llm.stages
    assert (settings.output / 'data/platform-dir/C-01.json').is_file()
    assert (settings.output / result['path']).read_text(encoding='utf-8') == result['markdown']


def test_batch_cannot_omit_a_paper(tmp_path):
    names = papers(tmp_path / 'papers')
    settings = Settings(tmp_path, tmp_path / 'output', material_roots=[tmp_path / 'papers'])
    chunks = DocumentIndex(settings, tmp_path / 'cache').build(names)

    class Missing(BatchedLLM):
        def json(self, stage, data, **kwargs):
            value = super().json(stage, data, **kwargs)
            if stage == 'extract_batch':
                value['papers'].pop()
            return value

    with pytest.raises(EvidenceError, match='omitted'):
        TaskHandlers(Missing(), 'team').extract(CaseContext.from_dict(case('C-01', names)), chunks)


def test_batch_rejects_a_quote_bound_to_another_paper(tmp_path):
    names = papers(tmp_path / 'papers')[:2]
    settings = Settings(tmp_path, tmp_path / 'output', material_roots=[tmp_path / 'papers'])
    chunks = DocumentIndex(settings, tmp_path / 'cache').build(names)
    foreign = next(chunk for chunk in chunks if chunk.filename == names[1] and chunk.page == 1)

    class Mixed(BatchedLLM):
        def json(self, stage, data, **kwargs):
            value = super().json(stage, data, **kwargs)
            if stage == 'extract_batch':
                value['papers'][0]['claims'] = [{'claim': 'This is a foreign paper claim',
                    'chunk_id': foreign.chunk_id, 'quote': foreign.text[:120]}]
            return value

    handler = TaskHandlers(Mixed(), 'team')
    ledger = handler.extract(CaseContext.from_dict(case('C-01', names)), chunks)
    assert names[0] not in {e.filename for e in ledger}
    assert any(row['filename'] == names[0] for row in handler.rejected_claims)
