"""Resource, cancellation and material-scope boundaries for additional page reads."""
import pytest

from research_agent.core.config import Settings
from research_agent.core.models import MaterialRef
from research_agent.core.session import CaseSession
from research_agent.core.utils import sha256_file
from research_agent.rag.materials import DocumentIndex
from test_deep_probe_counterexamples import make_pdf


def index_at(tmp_path):
    papers = tmp_path / 'papers'
    make_pdf(papers / 'authorized.pdf', 16, 12)
    make_pdf(papers / 'not-assigned.pdf', 16, 12)
    index = DocumentIndex(Settings(tmp_path, tmp_path / 'out', 'fixture', material_roots=[papers]), tmp_path / 'cache')
    index.parser.extract_tables = False
    index.build(['authorized.pdf'], page_limit=2)
    return index


def test_probe_never_opens_unassigned_papers(tmp_path):
    index = index_at(tmp_path)
    chunks, trace = index.probe_deep_pages(['xenocalibration isotopeperturbation'], 2)
    assert chunks and {c.filename for c in chunks} == {'authorized.pdf'}
    assert trace['authorized_documents'] == 1 and trace['documents_opened'] == 1
    assert not trace['coverage_limited']
    assert {c.document_sha256 for c in chunks} == {sha256_file(tmp_path / 'papers/authorized.pdf')}
    assert {c.page for c in chunks} == {12}


def test_page_limit_samples_beyond_immediate_prefix(tmp_path):
    index = index_at(tmp_path)
    _, trace = index.probe_deep_pages(['xenocalibration'], 2, max_pages=3)
    assert trace['scanned_pages'] <= 3
    assert trace['coverage_limited']


@pytest.mark.parametrize('limits', [{'max_pages': 0}, {'max_seconds': 0}])
def test_zero_probe_budget_preserves_initial_index(tmp_path, limits):
    index = index_at(tmp_path)
    original = index.chunks
    chunks, trace = index.probe_deep_pages(['xenocalibration'], 2, **limits)
    assert chunks == [] and trace['scanned_pages'] == 0 and trace['coverage_limited']
    assert index.chunks is original


def test_model_time_reserve_skips_extra_reads(tmp_path):
    index = index_at(tmp_path)
    with CaseSession('B-93', 90).activate():
        chunks, trace = index.probe_deep_pages(['xenocalibration'], 2)
    assert not chunks and trace['stop_reason'] == 'model_time_reserved'
    assert trace['coverage_limited'] and trace['documents_opened'] == 0


def test_cancellation_is_never_recorded_as_a_broken_pdf(tmp_path, monkeypatch):
    index = index_at(tmp_path)
    import research_agent.rag.deep_probe as module
    def cancel(*args):
        raise TimeoutError('case cancelled')
    monkeypatch.setattr(index.chunker, 'chunk', cancel)
    with pytest.raises(TimeoutError):
        index.probe_deep_pages(['xenocalibration'], 2)
    assert not index.errors


def test_broken_assigned_pdf_is_isolated_and_coverage_reported(tmp_path):
    index = index_at(tmp_path)
    bad = tmp_path / 'broken.pdf'
    bad.write_bytes(b'%PDF-invalid')
    index.materials.append(MaterialRef(bad, bad.name, 'pdf', sha256_file(bad)))
    chunks, trace = index.probe_deep_pages(['xenocalibration'], 2)
    assert chunks and trace['coverage_limited']
    assert any(row['filename'] == bad.name and row['stage'] == 'deep_page_probe' for row in index.errors)


def test_approved_deep_evidence_is_retained_for_audit(tmp_path):
    from types import SimpleNamespace
    from research_agent.rag.retrieval import HybridRetriever, QueryPlan
    from research_agent.workflows.tasks import TaskHandlers
    from test_b_evidence_counterexamples import Reader
    index = index_at(tmp_path)
    handler = TaskHandlers(Reader(['xenocalibration', 'isotopeperturbation']), 'fixture')
    handler.document_probe = lambda queries: index.probe_deep_pages(queries, 2)
    text = 'xenocalibration isotopeperturbation'
    task = SimpleNamespace(case_id='B-94', input=text, to_dict=lambda: {'input': text})
    answer = handler.retrieval(task, HybridRetriever(index.chunks), QueryPlan(text))
    assert answer['papers']
    assert handler.selected_chunks and {c.page for c in handler.selected_chunks} == {12}
    assert {c.chunk_id for c in handler.selected_chunks} == {r['chunk_id'] for r in handler.retrieval_trace['accepted_evidence']}
