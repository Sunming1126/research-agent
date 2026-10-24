"""减少模型往返不能削弱跨论文隔离或跳过指定论文。"""
from types import SimpleNamespace

import pytest

from research_agent.core.models import Chunk
from research_agent.evidence import EvidenceError
from research_agent.workflows.tasks import TaskHandlers


def extract_model(*, corrupt=False, omit=False):
    class Model:
        supports_batch_extraction = True
        calls = []

        def json(self, stage, data, **kwargs):
            assert stage == "extract_batch"
            self.calls.append(data)
            rows = []
            for index, paper in enumerate(data["papers"]):
                chunk = paper["chunks"][0]
                if corrupt and len(data["papers"]) > 1 and index == 0:
                    chunk = data["papers"][1]["chunks"][0]
                rows.append(dict(filename=paper["filename"], missing_queries=[],
                                 claims=[dict(claim="保持原文条件的事实", quote=chunk["text"], chunk_id=chunk["chunk_id"])]))
            return {"papers": rows[1:] if omit else rows}
    return Model()


def chunks():
    return [Chunk(f"chunk-{i}", f"p{i}.pdf", 1, "Methods", f"Paper {i} studies condition {i} with fixed controls.",
                  "text", f"hash-{i}") for i in range(5)]


def test_five_papers_use_two_batches_and_keep_per_paper_provenance():
    model = extract_model()
    handler = TaskHandlers(model, "test")
    handler.selected_chunks = chunks()
    evidence = handler.extract(SimpleNamespace(input="Compare methods", function="Survey"), chunks())
    assert len(model.calls) == 2
    assert [len(call["papers"]) for call in model.calls] == [3, 2]
    assert {item.filename for item in evidence} == {f"p{i}.pdf" for i in range(5)}
    assert len({item.evidence_id for item in evidence}) == 5


def test_batch_cannot_attribute_a_foreign_paper_chunk_to_the_current_paper():
    handler = TaskHandlers(extract_model(corrupt=True), "test")
    handler.selected_chunks = chunks()
    evidence = handler.extract(SimpleNamespace(input="Compare methods", function="Survey"), chunks())
    assert "p0.pdf" not in {item.filename for item in evidence}
    assert handler.rejected_claims[0]["filename"] == "p0.pdf"


def test_missing_paper_is_a_failed_extraction_instead_of_silent_coverage():
    handler = TaskHandlers(extract_model(omit=True), "test")
    with pytest.raises(EvidenceError, match="omitted a paper"):
        handler.extract(SimpleNamespace(input="Compare methods", function="Survey"), chunks())
