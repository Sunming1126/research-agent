"""Behavior regressions for reference context, appendix headings and model-time reserve.

Synthetic PDF ground truth is defined here. The deterministic reader merely
checks delivered text; it does not estimate real model precision or recall.
"""
from types import SimpleNamespace

import fitz
import pytest

from research_agent.core.config import Settings
from research_agent.rag.materials import DocumentIndex
from research_agent.rag.retrieval import HybridRetriever,QueryPlan
from research_agent.workflows.tasks import TaskHandlers

NEEDLE='xenocalibration isotopeperturbation'


def material(path,*,reference_page=None,target_page=None,appendix_page=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    with fitz.open() as document:
        for number in range(1,10):
            lines=[]
            if number==reference_page:lines.append('References')
            if number==appendix_page:lines.append('Appendix A')
            lines.append('Neutral laboratory apparatus and operating conditions.')
            if number==target_page:lines.append('We measure '+NEEDLE+' under controlled conditions.')
            document.new_page().insert_text((40,80),'\n\n'.join(lines))
        document.save(path)


class Reader:
    def json(self,stage,payload,**kwargs):
        assert stage=='retrieve'
        return {'papers':[{'filename':p['filename'],'decision':'include','relevant':True,
            'chunk_ids':[c['chunk_id'] for c in p['chunks'] if NEEDLE in c['text']],
            'reason':'The required literal method statement is present in supplied body evidence.'}
            for p in payload['candidates'] if any(NEEDLE in c['text'] for c in p['chunks'])]}


def retrieve(tmp_path,**pdf):
    papers=tmp_path/'papers';material(papers/'scope.pdf',**pdf)
    index=DocumentIndex(Settings(tmp_path,tmp_path/'out','fixture',material_roots=[papers]),tmp_path/'cache')
    index.parser.extract_tables=False
    prefix=index.build(['scope.pdf'],page_limit=4)
    handler=TaskHandlers(Reader(),'fixture')
    handler.document_probe=lambda queries:index.probe_deep_pages(queries,4)
    question='Find the paper with its own body method '+NEEDLE+'; bibliography mentions do not qualify.'
    case=SimpleNamespace(case_id='B-95',input=question,to_dict=lambda:{'input':question})
    answer=handler.retrieval(case,HybridRetriever(prefix),QueryPlan(NEEDLE))
    return answer,handler,index


def test_reference_title_in_prefix_does_not_authorize_continuation_as_body(tmp_path):
    answer,handler,index=retrieve(tmp_path,reference_page=4,target_page=5)
    assert any(c.kind=='references' and c.page==4 for c in index.chunks)
    assert answer['papers']==[]


def test_reference_title_on_prior_prefix_page_keeps_context_across_blankish_page(tmp_path):
    answer,_,_=retrieve(tmp_path,reference_page=3,target_page=5)
    assert answer['papers']==[]


def test_appendix_after_prefix_reference_restores_eligible_body(tmp_path):
    answer,handler,_=retrieve(tmp_path,reference_page=4,appendix_page=7,target_page=7)
    assert [p['filename'] for p in answer['papers']]==['scope.pdf']
    assert any(c.page==7 for c in handler.selected_chunks)


def test_reference_title_in_unread_range_still_excludes_same_page(tmp_path):
    answer,_,_=retrieve(tmp_path,reference_page=5,target_page=5)
    assert answer['papers']==[]


def test_plain_body_positive_is_preserved(tmp_path):
    answer,_,_=retrieve(tmp_path,target_page=7)
    assert [p['filename'] for p in answer['papers']]==['scope.pdf']


def test_missing_phrase_does_not_return_a_paper(tmp_path):
    answer,_,_=retrieve(tmp_path)
    assert answer['papers']==[]


"""Additional behavior regressions exposed by actual-paper appendix controls."""
from dataclasses import asdict
import hashlib
import json
from types import SimpleNamespace

import fitz
import pytest

from research_agent.core.config import Settings
from research_agent.core.utils import sha256_bytes
from research_agent.rag.materials import DocumentIndex
from research_agent.rag.retrieval import HybridRetriever,QueryPlan
from research_agent.workflows.tasks import TaskHandlers


def make_appendix_index(tmp_path,heading='A\nAppendix'):
    papers=tmp_path/'papers';papers.mkdir()
    with fitz.open() as document:
        for page in range(1,10):
            text='Neutral laboratory apparatus and operating conditions.'
            if page==4:text='References\n\n'+text
            if page==7:text=heading+'\n\nWe measure xenocalibration isotopeperturbation in our own appendix.'
            document.new_page().insert_text((40,80),text)
        document.save(papers/'scope.pdf')
    index=DocumentIndex(Settings(tmp_path,tmp_path/'out','fixture',material_roots=[papers]),tmp_path/'cache')
    index.parser.extract_tables=False
    index.build(['scope.pdf'],page_limit=4)
    return index


class AppendixReader:
    def json(self,stage,payload,**kwargs):
        assert stage=='retrieve'
        return {'papers':[{'filename':p['filename'],'decision':'include','relevant':True,
            'chunk_ids':[c['chunk_id'] for c in p['chunks'] if 'xenocalibration isotopeperturbation' in c['text']],
            'reason':'The literal original method evidence is supplied.'}
            for p in payload['candidates'] if any('xenocalibration isotopeperturbation' in c['text'] for c in p['chunks'])]}


@pytest.mark.parametrize('heading',['A\nAppendix','1\nAppendix'])
def test_full_document_expansion_preserves_numbered_appendix(tmp_path,heading):
    index=make_appendix_index(tmp_path,heading)
    handler=TaskHandlers(AppendixReader(),'fixture')
    handler.document_probe=lambda queries:index.probe_deep_pages(queries,4)
    handler.document_expander=index.expand_documents
    text='Find the own appendix method xenocalibration isotopeperturbation'
    case=SimpleNamespace(case_id='B-96',input=text,to_dict=lambda:{'input':text})
    result=handler.retrieval(case,HybridRetriever(index.chunks),QueryPlan(text))
    assert [p['filename'] for p in result['papers']]==['scope.pdf']
    assert any(c.page==7 for c in handler.selected_chunks)


def test_legacy_cache_with_old_appendix_classification_is_not_reused(tmp_path):
    index=make_appendix_index(tmp_path)
    material=index.materials[0]
    rows=[asdict(c) for c in index.chunker.chunk(material,index.parser.pages(material))]
    for row in rows:
        if row['page']>=7:row.update(kind='references',heading='References')
    legacy_identity=f'{material.filename}|{material.sha256}|{index.parser.VERSION}|850|110|{index.parser.ocr}|text-blocks-only'
    legacy=index.cache/(sha256_bytes(legacy_identity.encode())+'.json')
    legacy.write_text(json.dumps({'chunks':rows}),encoding='utf-8')
    before=hashlib.sha256(legacy.read_bytes()).hexdigest()
    rebuilt=index.build(['scope.pdf'])
    assert any(c.page==7 and c.kind!='references' and 'xenocalibration' in c.text for c in rebuilt)
    assert legacy.is_file() and hashlib.sha256(legacy.read_bytes()).hexdigest()==before


def test_larger_probe_cap_still_reserves_model_time_during_scan(tmp_path,monkeypatch):
    import research_agent.rag.deep_probe as module
    index=make_appendix_index(tmp_path)
    remaining=iter([110.,100.,94.])
    monkeypatch.setattr(module,'current_session',lambda:SimpleNamespace(remaining=lambda:next(remaining)))
    _,trace=index.probe_deep_pages(['xenocalibration'],4)
    assert trace['scanned_pages']==1 and trace['stop_reason']=='model_time_reserved'
    assert trace['coverage_limited']
