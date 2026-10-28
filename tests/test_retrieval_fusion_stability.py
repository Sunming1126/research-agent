"""Different query scales, independent targets and late model failures."""
import copy

import pytest

from fixtures import case
from research_agent.core.models import Chunk, RetrievalHit, CaseContext
from research_agent.rag.retrieval import QueryPlan, fuse_document_rankings, bounded_document_candidates
from research_agent.workflows.tasks import TaskHandlers
from research_agent.integrations.llm import ModelError, ModelQuotaError


def row(name, score, evidence_id=None):
    chunk = Chunk(evidence_id or name, name, 1, 'Methods', 'Real fixture evidence for a specific retrieval condition.',
                  'text', 'fixture-sha', 0, 60)
    return name, score, [RetrievalHit(chunk, score, ())]


def test_rrf_combines_agreement_without_comparing_query_score_scales():
    rankings = [[row('outlier.pdf', 99), row('supported.pdf', .3)],
                [row('supported.pdf', .01), row('another.pdf', .009)],
                [row('supported.pdf', .02), row('last.pdf', .01)]]
    original = copy.deepcopy(rankings)
    result, scores = fuse_document_rankings(rankings)
    assert result[0][0] == 'supported.pdf'
    assert scores['supported.pdf'] > scores['outlier.pdf']
    assert rankings == original, 'Fusion must not mutate cached score/evidence objects'


def test_query_fusion_keeps_distinct_evidence_from_each_channel():
    rows, _ = fuse_document_rankings([[row('paper.pdf', .8, 'method')], [row('paper.pdf', .7, 'evaluation')]])
    assert {h.chunk.chunk_id for h in rows[0][2]} == {'method', 'evaluation'}


def test_candidate_limit_reserves_independent_low_rank_topic():
    rows = [row(str(i) + '.pdf', .9 - i * .01) for i in range(20)]
    selected = bounded_document_candidates(rows, ['19.pdf'], 12)
    assert len(selected) == 12 and '19.pdf' in {r[0] for r in selected}
    assert '0.pdf' in {r[0] for r in selected}


class Retriever:
    def top_documents(self, plan, **kwargs):
        return [row(str(i) + '.pdf', .8 - i * .01) for i in range(18)]


@pytest.mark.parametrize('fault', ['malformed', 'quota', 'timeout'])
def test_later_batch_failure_retains_only_earlier_verified_papers(fault):
    class Model:
        calls = 0
        def json(self, stage, data, **kwargs):
            self.calls += 1
            if self.calls > 1:
                if fault == 'malformed':
                    return {'papers': 'not a list'}
                if fault == 'quota':
                    raise ModelQuotaError('Fixture quota exhaustion')
                raise TimeoutError('Fixture transport timeout')
            return {'papers': [{'filename': x['filename'], 'reason': 'Supported by this supplied method fragment.',
                                'chunk_ids': [x['chunks'][0]['chunk_id']]} for x in data['candidates']]}
    model = Model()
    handler = TaskHandlers(model, 'runtime-directory')
    result = handler.retrieval(CaseContext.from_dict(case('B-01')), Retriever(), QueryPlan('fixture topic'))
    assert len(result['papers']) == 8
    assert model.calls == 2
    assert handler.retrieval_trace['partial_error']['failed_batch'] == 1
    assert {p['filename'] for p in result['papers']} <= set(handler.retrieval_trace['candidates'][:8])


def test_first_batch_failure_is_not_claimed_as_successful_retrieval():
    class Model:
        def json(self, *args, **kwargs):
            raise ModelError('Fixture first-batch error')
    with pytest.raises(ModelError):
        TaskHandlers(Model(), 'runtime-directory').retrieval(CaseContext.from_dict(case('B-01')), Retriever(), QueryPlan('topic'))


def test_b_query_planner_receives_title_constraints_and_expected_output():
    payload = case('B-01')
    payload.update(title='Required named method', constraints=['Disallow the negative control'],
                   expected_output='Find the exact algorithm family')
    class Model:
        def json(self, stage, data, **kwargs):
            assert data == payload
            return {'topic': 'algorithm family'}
    handler = TaskHandlers(Model(), 'runtime-directory')
    assert handler._plan(CaseContext.from_dict(payload)).topic == 'algorithm family'
