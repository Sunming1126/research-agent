"""End-to-end evidence transfer counterexamples, usable unchanged on the frozen baseline.

The deterministic reader only approves supplied evidence; these are engineering
simulations, not estimates of real model accuracy or official retrieval scores.
"""
from types import SimpleNamespace

import pytest

from research_agent.core.models import Chunk, RetrievalHit
from research_agent.rag.retrieval import QueryPlan
from research_agent.workflows.tasks import TaskHandlers


def hit(identifier, text, score=.8, name='method.pdf', page=2):
    return RetrievalHit(Chunk(identifier, name, page, 'Methods', text, 'text', 'fixture', 0, len(text)), score, ())


class Reader:
    def __init__(self, required):
        self.required = required
        self.requests = []

    def json(self, stage, payload, **kwargs):
        self.requests.append(payload)
        papers = []
        for row in payload['candidates']:
            text = ' '.join(c['text'] for c in row['chunks']).casefold()
            if all(term.casefold() in text for term in self.required):
                papers.append({'filename': row['filename'], 'decision': 'include', 'relevant': True,
                               'chunk_ids': [c['chunk_id'] for c in row['chunks']],
                               'reason': 'The supplied method evidence supports the required conditions.'})
        return {'papers': papers}


def task(text):
    return SimpleNamespace(case_id='B-90', input=text, to_dict=lambda: {'input': text})


@pytest.mark.parametrize('index', range(8))
def test_independent_condition_evidence_survives_higher_scoring_overlap(index):
    anchor, condition = f'encoder{index}', f'calibration{index}'
    overlap = [hit(f'overlap-{j}', (anchor + ' method repeated description. ') * 12, .99 - j * .01) for j in range(3)]
    required = hit('condition', f'We use {anchor} and measure {condition} under controlled perturbations.', .41, page=9)
    class Retriever:
        def top_documents(self, plan, **kwargs):
            return [('method.pdf', .8, [required] if plan.topic == condition else overlap)]
    reader = Reader([anchor, condition])
    handler = TaskHandlers(reader, 'fixture')
    result = handler.retrieval(task(f'Find {anchor} with {condition}'), Retriever(),
                               QueryPlan(anchor, focus=condition, subqueries=[anchor, condition]))
    assert [p['filename'] for p in result['papers']] == ['method.pdf']
    sent = reader.requests[0]['candidates'][0]['chunks']
    assert 'condition' in {c['chunk_id'] for c in sent}


@pytest.mark.parametrize('index', range(8))
def test_query_match_after_prefix_is_visible_to_the_reader(index):
    phrase = f'isotope{index} calibration{index}'
    original = ('General experimental background and apparatus details. ' * 27) + phrase + ' demonstrates the required method.'
    evidence = hit('late-match', original, .8)
    class Retriever:
        def top_documents(self, *args, **kwargs):
            return [('method.pdf', .8, [evidence])]
    reader = Reader(phrase.split())
    result = TaskHandlers(reader, 'fixture').retrieval(task('Find ' + phrase), Retriever(), QueryPlan(phrase))
    assert [p['filename'] for p in result['papers']] == ['method.pdf']
    excerpt = reader.requests[0]['candidates'][0]['chunks'][0]['text']
    assert len(excerpt) <= 1100 and excerpt in original and phrase in excerpt


@pytest.mark.parametrize('count', [5, 6, 7, 8])
def test_short_evidence_does_not_require_an_extra_small_batch(count):
    class Retriever:
        def top_documents(self, *args, **kwargs):
            return [(f'paper-{i}.pdf', .7, [hit(str(i), 'An independently measured temperature effect.', name=f'paper-{i}.pdf')])
                    for i in range(count)]
    reader = Reader(['temperature'])
    result = TaskHandlers(reader, 'fixture').retrieval(task('Find temperature experiments'), Retriever(), QueryPlan('temperature'))
    assert len(result['papers']) == count
    assert len(reader.requests) == 1


@pytest.mark.parametrize('count', [1, 2, 3, 4])
def test_missing_condition_is_never_filled_from_rank_or_filename(count):
    class Retriever:
        def top_documents(self, *args, **kwargs):
            return [(f'calibration-{i}.pdf', .9, [hit(str(i), 'Encoder analysis without measurement evidence.', name=f'calibration-{i}.pdf')])
                    for i in range(count)]
    result = TaskHandlers(Reader(['calibration']), 'fixture').retrieval(task('Find calibration measurements'), Retriever(), QueryPlan('calibration'))
    assert result['papers'] == []
