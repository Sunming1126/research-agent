"""Unchanged evidence-routing and source-provenance counterexamples on both versions."""
from types import SimpleNamespace

import pytest

from research_agent.core.models import Chunk, Evidence
from research_agent.evidence.reporting import resolve_quote_source
from research_agent.workflows.report_context import audit_request, cited_evidence, repair_context
from research_agent.evidence.reporting import validate_citations
from research_agent.evidence.attribution import validate_named_attribution
from research_agent.evidence import EvidenceError


def evidence_rows():
    return [{'evidence_id': f'ev-{i}', 'ref': i, 'page': i+2, 'filename': f'{i}.pdf',
             'claim': f'Independently supported observation {i}.'} for i in range(1, 4)]


@pytest.mark.parametrize('citation', ['[1,2]', '[1, 2]', '[1,2, p.4]'])
def test_grouped_references_route_all_sources(citation):
    rows = evidence_rows()
    expected = {'ev-2'} if 'p.' in citation else {'ev-1', 'ev-2'}
    assert {e['evidence_id'] for e in cited_evidence('比较证据' + citation, rows)} == expected


def test_audit_does_not_silently_drop_group_member_when_used_list_is_incomplete():
    rows = evidence_rows()
    ledger = [Evidence(r['evidence_id'], r['claim'], r['filename'], r['page'], str(r['ref']),
                       'A traceable original passage.') for r in rows]
    case = SimpleNamespace(function='Idea')
    data = audit_request(case, '两篇的联合比较 [1,2]，另外一篇事实 [3, p.5]。', rows, ledger, used=['ev-3'])
    assert {e['evidence_id'] for e in data['source_quotes']} == {'ev-1', 'ev-2', 'ev-3'}


def test_repair_context_keeps_group_members_without_unrelated_papers():
    rows = evidence_rows()
    data = repair_context('正文', ['组合条件应保留 [1,2]'], [], rows)
    assert {e['evidence_id'] for e in data['claims']} == {'ev-1', 'ev-2'}


def test_numeric_fact_with_grouped_page_citation_is_not_a_missing_page():
    rows = [dict(r, page=4) for r in evidence_rows()[:2]]
    body = '研究边界及比较条件需要逐项核对。'*60 + '\n\n论文报告准确率为90% [1,2, p.4]。'
    validate_citations(body, rows, ['ev-1','ev-2'])


def test_grouped_wrong_paper_attribution_is_checked():
    rows = [{'evidence_id':'ev-1','ref':1,'page':2,'filename':'2401.00001-ALPHA.pdf','quote':'We introduce ALPHA.'},
            {'evidence_id':'ev-2','ref':2,'page':3,'filename':'other.pdf','quote':'We introduce another independent model.'},
            {'evidence_id':'ev-3','ref':3,'page':4,'filename':'third.pdf','quote':'A separate evaluation protocol.'}]
    with pytest.raises(EvidenceError):
        validate_named_attribution('ALPHA采用这里的新机制 [2,3]。',rows,['ev-2','ev-3'])


def chunk(identifier, text, start, document='source-A'):
    return Chunk(identifier, 'paper.pdf', 4, 'Methods', text, 'text', identifier,
                 start, start+len(text), document)


@pytest.mark.parametrize('gap', [25, 80, 300])
def test_missing_same_page_text_cannot_be_skipped_to_manufacture_a_quote(gap):
    first = chunk('a', 'We observed calibrated signal', 0)
    second = chunk('b', 'in all conditions without exception.', first.end+gap)
    assert resolve_quote_source(first, 'calibrated signal in all conditions without exception', [first, second]) is None


def test_same_filename_and_page_do_not_override_different_document_hashes():
    first = chunk('a', 'We observed calibrated signal', 0)
    second = chunk('b', 'in all conditions without exception.', first.end+1, 'source-B')
    assert resolve_quote_source(first, 'calibrated signal in all conditions without exception', [first, second]) is None


def test_overlapping_chunks_cannot_duplicate_words_into_a_quote():
    original = 'We observed calibrated signal and stable responses.'
    first = chunk('a', original[:33], 0)
    second = chunk('b', original[23:], 23)
    manufactured = first.text[-10:] + ' ' + second.text[:10]
    assert resolve_quote_source(first, manufactured, [first, second]) is None


def test_real_cross_overlap_quote_is_recovered_without_repeating_overlap():
    original = 'We observed calibrated signal and stable responses.'
    first = chunk('a', original[:33], 0)
    second = chunk('b', original[23:], 23)
    result = resolve_quote_source(first, original[12:47], [first, second])
    assert result is not None and result.text == original


@pytest.mark.xfail(strict=True, reason='Known quota underutilization remains: full-page reallocation prototype did not pass timed retrieval gate; retained as an open defect, not a passed test')
def test_default_page_budget_is_redistributed_from_short_to_long_authorized_papers(tmp_path):
    import fitz
    from research_agent.core.config import Settings
    from research_agent.rag.materials import DocumentIndex
    papers = tmp_path/'papers'
    papers.mkdir()
    for index in range(60):
        with fitz.open() as document:
            for page in range(65 if index == 59 else 5):
                text = 'Neutral laboratory apparatus and background observations.'
                if index == 59 and page == 7:
                    text = 'We independently measure xenocalibration under controlled isotopeperturbation.'
                document.new_page().insert_text((40,80),text)
            document.save(papers/f'{index:02d}.pdf')
    index = DocumentIndex(Settings(tmp_path,tmp_path/'out','fixture',material_roots=[papers]),tmp_path/'cache')
    index.parser.extract_tables = False
    index.build(['papers'],page_limit=4)
    chunks,trace=index.probe_deep_pages(['xenocalibration isotopeperturbation'],4,max_seconds=30)
    assert trace['scanned_pages'] == 120 and not trace['coverage_limited']
    assert any(c.filename == '59.pdf' and c.page == 8 for c in chunks)
