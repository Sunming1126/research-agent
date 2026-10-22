"""候选筛选后深入正文，授权材料和初始缓存保持完整。"""
from types import SimpleNamespace

import fitz

from research_agent.core.config import Settings
from research_agent.rag.materials import DocumentIndex
from research_agent.rag.retrieval import HybridRetriever, QueryPlan
from research_agent.workflows.tasks import TaskHandlers


def test_deepening_finds_late_evidence_without_losing_authorized_files(tmp_path):
    folder = tmp_path / "papers"
    folder.mkdir()
    with fitz.open() as doc:
        for page in range(10):
            p = doc.new_page()
            text = ("Adaptive encoder study and experimental design. " if page < 9 else
                    "Adaptive encoder noise measurements demonstrate parameter sensitivity with calibration errors.")
            p.insert_text((30, 50), text)
        doc.save(folder / "target.pdf")
    with fitz.open() as doc:
        doc.new_page().insert_text((30, 50), "A different topic of atmospheric observations.")
        doc.save(folder / "other.pdf")
    settings = Settings(tmp_path, tmp_path / "out", material_roots=[folder])
    index = DocumentIndex(settings, tmp_path / "cache")
    initial = index.build(["target.pdf", "other.pdf"], page_limit=4)

    class LLM:
        def json(self, stage, data, **kwargs):
            return {"papers": [{"filename": row["filename"], "decision": "include",
                                "reason": "Late-page sensitivity evidence supports the requested measurements.",
                                "chunk_ids": [row["chunks"][0]["chunk_id"]]}
                               for row in data["candidates"] if row["filename"] == "target.pdf"]}

    case = SimpleNamespace(case_id="B-99", input="Find adaptive encoder noise measurements", to_dict=lambda: {})
    handler = TaskHandlers(LLM(), "team")
    handler.document_expander = index.expand_documents
    result = handler.retrieval(case, HybridRetriever(initial), QueryPlan("adaptive encoder noise measurements"))
    assert [p["filename"] for p in result["papers"]] == ["target.pdf"]
    assert "target.pdf" in handler.retrieval_trace["fulltext_candidates"]
    assert {m.filename for m in index.materials} == {"target.pdf", "other.pdf"}
    assert max(c.page for c in index.chunks) == 4


def test_explicit_uncertainty_is_not_overridden_by_high_lexical_score():
    from research_agent.core.models import Chunk
    c = Chunk("c", "paper.pdf", 1, "Method", "A strong lexical match to an adaptive encoder.", "text", "h")

    class Retriever:
        def top_documents(self, *args, **kwargs):
            return [("paper.pdf", .99, [SimpleNamespace(chunk=c)])]

    class LLM:
        def json(self, *args, **kwargs):
            return {"papers": [{"filename": "paper.pdf", "decision": "uncertain", "chunk_ids": ["c"],
                                "reason": "Not enough evidence to establish the required contribution."}]}

    handler = TaskHandlers(LLM(), "team")
    case = SimpleNamespace(case_id="B-98", input="adaptive encoder", to_dict=lambda: {})
    result = handler.retrieval(case, Retriever(), QueryPlan("adaptive encoder", subqueries=["encoder"]))
    assert result["papers"] == []


def test_exact_filename_matches_even_without_body_keyword_overlap():
    from research_agent.core.models import Chunk
    c = Chunk("c", "2501.12345-Special.pdf", 1, "Abstract", "A study of previously unseen physical phenomena.", "text", "h")
    hits = HybridRetriever([c]).search("2501.12345-special.PDF")
    assert hits and hits[0].chunk.filename == "2501.12345-Special.pdf"
