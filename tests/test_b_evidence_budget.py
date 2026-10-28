"""Budget and provenance invariants for the new B evidence policy."""
import copy
import json

from research_agent.rag.evidence_selection import query_window, evidence_batches, select_evidence
from research_agent.core.models import Chunk, RetrievalHit


def test_contiguous_window_preserves_verbatim_unicode_and_negation():
    text = '背景说明。' * 260 + 'This method does not support causal identification.' + '补充说明。' * 100
    excerpt = query_window(text, ['causal identification'])
    assert excerpt in text and len(excerpt) <= 1100
    assert 'does not support causal identification' in excerpt


def test_batches_respect_serialized_input_budget_and_preserve_all_candidates():
    class Estimator:
        def input_tokens(self, stage, data):
            return len(json.dumps(data))
    candidates = [{'filename': str(i), 'chunks': [{'text': 'evidence ' * 150}]} for i in range(15)]
    batches = list(evidence_batches(candidates, {'input': 'test'}, Estimator(), max_input_tokens=3500))
    assert [row for batch in batches for row in batch] == candidates
    assert all(len(batch) <= 8 for batch in batches)
    assert all(Estimator().input_tokens('retrieve', {'task': {'input': 'test'}, 'candidates': batch}) <= 3500 for batch in batches)


def test_empty_query_falls_back_and_evidence_selection_does_not_mutate():
    assert query_window('x' * 1400, []) == 'x' * 1100
    hits = [RetrievalHit(Chunk(str(i), 'paper.pdf', i + 1, 'Methods', text, 'text', 'hash'), .8, ())
            for i, text in enumerate(['encoder method', 'encoder method', 'calibration measurement'])]
    original = copy.deepcopy(hits)
    selected = select_evidence(hits, ['encoder', 'calibration'], 2)
    assert {h.chunk.chunk_id for h in selected} == {'0', '2'}
    assert hits == original
