"""未知领域论文也应召回实验和边界，不能只读首页与高频主题。"""
from research_agent.core.models import Chunk
from research_agent.rag.retrieval import HybridRetriever, QueryPlan
from research_agent.workflows.tasks import report_evidence_chunks


def test_report_recalls_experiments_and_limitations_beyond_topic_pages():
    chunks = [Chunk(f"topic-{i}", "unseen.pdf", i + 1, "Theory", "Quantum topology theory " * 40,
                    "text", f"h{i}", i * 1000) for i in range(7)]
    chunks += [Chunk("experiment", "unseen.pdf", 9, "Experiments",
                     "Experiment dataset benchmark evaluation results baseline " * 16, "text", "ex", 9000),
               Chunk("limitations", "unseen.pdf", 10, "Limitations",
                     "Limitations discussion assumptions failure computational cost " * 16, "text", "lim", 10000)]
    selected = report_evidence_chunks(HybridRetriever(chunks), QueryPlan("quantum topology theory"))
    ids = [c.chunk_id for c in selected]
    assert "experiment" in ids
    assert "limitations" in ids
    assert len(ids) == len(set(ids))
    assert len(ids) <= 8
