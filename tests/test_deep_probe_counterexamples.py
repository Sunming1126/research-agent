"""Unchanged end-to-end counterexamples on both versions; synthetic, not model accuracy."""
from types import SimpleNamespace

import fitz
import pytest

from research_agent.core.config import Settings
from research_agent.rag.materials import DocumentIndex
from research_agent.rag.retrieval import HybridRetriever, QueryPlan
from research_agent.workflows.tasks import TaskHandlers
from test_b_evidence_counterexamples import Reader


def make_pdf(path, pages, target=None, bibliography=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as doc:
        for i in range(pages):
            page = doc.new_page()
            text = 'Neutral background. The apparatus has standard operating procedures.'
            if i + 1 == target:
                text = ('References\n\n' if bibliography else '') + 'We measure xenocalibration under isotopeperturbation in our experiment.'
            page.insert_text((50, 80), text)
        doc.save(path)


@pytest.mark.parametrize('target', [5, 9, 13])
def test_late_condition_reaches_reader_before_candidate_expansion(tmp_path, target):
    papers = tmp_path / 'papers'
    make_pdf(papers / 'neutral.pdf', 14, target)
    index = DocumentIndex(Settings(tmp_path, tmp_path / 'out', 'fixture', material_roots=[papers]), tmp_path / 'cache')
    index.parser.extract_tables = False
    prefix = index.build(['neutral.pdf'], page_limit=2, tolerate_pdf_errors=True)
    original_materials, original_chunks = index.materials, index.chunks
    handler = TaskHandlers(Reader(['xenocalibration', 'isotopeperturbation']), 'fixture')
    handler.document_expander = index.expand_documents
    probe = getattr(index, 'probe_deep_pages', None)
    if callable(probe):
        handler.document_probe = lambda queries: probe(queries, 2)
    text = 'xenocalibration isotopeperturbation'
    task = SimpleNamespace(case_id='B-91', input=text, to_dict=lambda: {'input': text})
    answer = handler.retrieval(task, HybridRetriever(prefix), QueryPlan(text))
    assert [p['filename'] for p in answer['papers']] == ['neutral.pdf']
    assert index.materials is original_materials and index.chunks is original_chunks


@pytest.mark.parametrize('bibliography', [True, False])
def test_absence_or_bibliography_does_not_invent_own_work(tmp_path, bibliography):
    papers = tmp_path / 'papers'
    make_pdf(papers / 'neutral.pdf', 14, 5 if bibliography else None, bibliography)
    index = DocumentIndex(Settings(tmp_path, tmp_path / 'out', 'fixture', material_roots=[papers]), tmp_path / 'cache')
    index.parser.extract_tables = False
    prefix = index.build(['neutral.pdf'], page_limit=2, tolerate_pdf_errors=True)
    handler = TaskHandlers(Reader(['xenocalibration', 'isotopeperturbation']), 'fixture')
    handler.document_expander = index.expand_documents
    probe = getattr(index, 'probe_deep_pages', None)
    if callable(probe):
        handler.document_probe = lambda queries: probe(queries, 2)
    text = 'xenocalibration isotopeperturbation'
    task = SimpleNamespace(case_id='B-92', input=text, to_dict=lambda: {'input': text})
    assert handler.retrieval(task, HybridRetriever(prefix), QueryPlan(text))['papers'] == []
