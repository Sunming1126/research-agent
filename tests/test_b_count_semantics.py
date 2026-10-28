"""Global output caps must not be inferred from per-paper language or lower bounds."""
from types import SimpleNamespace

import pytest

from research_agent.core.models import Chunk, RetrievalHit
from research_agent.rag.paper_selection import requested_paper_count
from research_agent.rag.retrieval import QueryPlan
from research_agent.workflows.tasks import TaskHandlers


@pytest.mark.parametrize('text,expected', [
    ('同一篇无需同时满足三个不同目标。', None),
    ('每一篇都必须有原文证据。', None),
    ('每个目标各一篇。', None),
    ('每个方向找两篇论文。', None),
    ('至少三篇相关论文。', None),
    ('不少于3篇论文。', None),
    ('找3篇以上的相关论文。', None),
    ('三篇或更多论文。', None),
    ('找三篇相关论文。', 3),
    ('每个方向各找一篇，合计三篇论文。', 3),
    ('至少三篇，最多五篇。', 5),
    ('总共8篇论文。', 8),
    ('查找不超过5篇论文。', 5),
    ('选择两篇论文。', 2),
    ('最多十二篇论文。', 12),
    ('找二十一篇论文。', 21),
    ('同一篇不必满足所有目标，请返回三篇相关论文。', 3),
    ('每篇一条理由。', None),
    ('查找3篇相关研究；每个方向至少一篇。', 3),
])
def test_requested_global_count_semantics(text, expected):
    assert requested_paper_count(text) == expected


def test_correctly_approved_independent_targets_are_not_cut_to_one():
    class Retriever:
        def top_documents(self, *args, **kwargs):
            return [(f'paper-{i}.pdf', .7, [RetrievalHit(Chunk(str(i), f'paper-{i}.pdf', 1,
                    'Methods', 'A verified independent method contribution.', 'text', 'fixture'), .7, ())]) for i in range(3)]
    class Reader:
        def json(self, stage, data, **kwargs):
            return {'papers': [{'filename': c['filename'], 'reason': 'The supplied method supports one requested independent target.',
                    'decision': 'include', 'chunk_ids': [c['chunks'][0]['chunk_id']]} for c in data['candidates']]}
    question = '分别检索三个独立目标。同一篇无需同时满足三个不同目标。'
    case = SimpleNamespace(case_id='B-90', input=question, to_dict=lambda: {'input': question})
    handler = TaskHandlers(Reader(), 'fixture')
    result = handler.retrieval(case, Retriever(), QueryPlan('independent method'))
    assert len(result['papers']) == 3
    assert handler.retrieval_trace['requested_limit'] == 10
