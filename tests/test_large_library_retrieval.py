"""百篇库中不能让单篇论文的许多片段挤掉其他相关论文。"""

import unittest
from types import SimpleNamespace

from research_agent.core.models import Chunk
from research_agent.rag.retrieval import HybridRetriever, QueryPlan
from research_agent.workflows.tasks import TaskHandlers
from research_agent.integrations.llm import ModelError


class LargeLibraryRetrievalTests(unittest.TestCase):
    def test_document_candidates_are_not_globally_chunk_capped(self):
        chunks = [Chunk(f"crowded-{i}", "A.pdf", i + 1, "retrieval",
                        "neural retrieval neural retrieval", "text", str(i)) for i in range(130)]
        chunks.append(Chunk("target", "B.pdf", 1, "retrieval",
                            "neural retrieval", "text", "target"))
        retriever = HybridRetriever(chunks)
        names = [name for name, _, _ in retriever.top_documents(QueryPlan("neural retrieval"),
                                                                 limit=2, excerpts=2)]
        self.assertEqual(set(names), {"A.pdf", "B.pdf"})

    def test_report_evidence_keeps_abstract_and_spreads_across_pages(self):
        chunks = [Chunk("abstract", "paper.pdf", 1, "Abstract",
                        "The proposed architecture uses a corpus of documents.", "text", "a")]
        chunks += [Chunk(f"method-{i}", "paper.pdf", 2, "Method",
                         "adaptive retrieval adaptive retrieval encoder trained on many questions and passages", "text", f"m{i}")
                   for i in range(12)]
        chunks.append(Chunk("evaluation", "paper.pdf", 3, "Evaluation",
                            "adaptive retrieval accuracy on held-out questions was compared with a sparse BM25 baseline", "text", "e"))
        selected = HybridRetriever(chunks).per_document(QueryPlan("adaptive retrieval"), maximum=4)
        self.assertIn("abstract", {chunk.chunk_id for chunk in selected})
        self.assertIn("evaluation", {chunk.chunk_id for chunk in selected})
        self.assertEqual(len(selected), 3)

    def test_report_evidence_selects_abstract_body_instead_of_heading(self):
        chunks = [
            Chunk("title", "paper.pdf", 1, "paper.pdf", "A new retrieval method", "text", "t", 0),
            Chunk("authors", "paper.pdf", 1, "paper.pdf", "Alice, Bob, Carol, Dan, Erin, Frank, Grace, Henry, Irene, John, Kate", "text", "u", 30),
            Chunk("label", "paper.pdf", 1, "Abstract", "Abstract", "text", "l", 100),
            Chunk("abstract-body", "paper.pdf", 1, "Abstract",
                  "We propose adaptive retrieval using a dual encoder and compare it with BM25 on open domain question answering. " * 4,
                  "text", "a", 120),
            Chunk("method", "paper.pdf", 2, "Method", "adaptive retrieval dual encoder", "text", "m", 200),
        ]
        selected = HybridRetriever(chunks).per_document(QueryPlan("adaptive retrieval"), maximum=3)
        self.assertIn("abstract-body", {chunk.chunk_id for chunk in selected})
        self.assertNotIn("label", {chunk.chunk_id for chunk in selected})

    def test_empty_model_query_falls_back_to_original_question(self):
        source = Chunk("hit", "target.pdf", 1, "Abstract", "Petri net signal hierarchy", "text", "h")

        class Retriever:
            def top_documents(self, plan, **kwargs):
                return [("target.pdf", 0.4, [SimpleNamespace(chunk=source)])] if plan.topic == "原始问题 Petri net" else []

        class LLM:
            def json(self, stage, data, **kwargs):
                return {"papers": [{"filename": "target.pdf", "reason": "原文讨论信号层级。", "chunk_ids": ["hit"]}]}

        case = SimpleNamespace(case_id="B-05", input="原始问题 Petri net", to_dict=lambda: {})
        result = TaskHandlers(LLM(), "demo").retrieval(case, Retriever(), QueryPlan("过窄中文改写"))
        self.assertEqual([row["filename"] for row in result["papers"]], ["target.pdf"])

    def test_original_question_keeps_targets_when_rewrite_has_other_candidates(self):
        target = Chunk("target", "target.pdf", 1, "Abstract", "relevant target", "text", "h1")
        distractor = Chunk("distractor", "other.pdf", 1, "Abstract", "other topic", "text", "h2")

        class Retriever:
            def top_documents(self, plan, **kwargs):
                return ([('other.pdf', .4, [SimpleNamespace(chunk=distractor)])]
                        if plan.topic == "narrow" else
                        [('target.pdf', .5, [SimpleNamespace(chunk=target)])])

        class LLM:
            def json(self, stage, data, **kwargs):
                return {"papers": [{"filename": row["filename"], "reason": "原文匹配。",
                                    "chunk_ids": [row["chunks"][0]["chunk_id"]]}
                                   for row in data["candidates"]]}

        case = SimpleNamespace(case_id="B-99", input="original target", to_dict=lambda: {})
        result = TaskHandlers(LLM(), "demo").retrieval(case, Retriever(), QueryPlan("narrow"))
        self.assertEqual({row["filename"] for row in result["papers"]}, {"other.pdf", "target.pdf"})

    def test_independent_subquery_rescues_compound_request(self):
        target = Chunk("selection", "selection.pdf", 1, "Abstract",
                       "Selection prior to biological evolution", "text", "h3")

        class Retriever:
            def top_documents(self, plan, **kwargs):
                if plan.topic == "selection prior biological evolution":
                    return [("selection.pdf", .9, [SimpleNamespace(chunk=target)])]
                return []

        class LLM:
            def json(self, stage, data, **kwargs):
                return {"papers": [{"filename": "selection.pdf", "reason": "原文对应子主题。",
                                    "chunk_ids": ["selection"]}]}

        case = SimpleNamespace(case_id="B-98", input="多个不同主题", to_dict=lambda: {})
        plan = QueryPlan("混合主题", subqueries=["selection prior biological evolution"])
        result = TaskHandlers(LLM(), "demo").retrieval(case, Retriever(), plan)
        self.assertEqual([row["filename"] for row in result["papers"]], ["selection.pdf"])

    def test_query_stage_retries_then_uses_original_question(self):
        class IncompleteLLM:
            def __init__(self):
                self.calls = []

            def json(self, stage, data, **kwargs):
                self.calls.append(kwargs["max_tokens"])
                raise ModelError("model response incomplete")

        llm = IncompleteLLM()
        handler = TaskHandlers(llm, "demo")
        case = SimpleNamespace(function="Literature_Retrieval", input="原始研究问题", constraints=[])
        plan = handler._plan(case)
        self.assertEqual(plan.topic, case.input)
        self.assertEqual(handler.query_plan_status, "original_fallback")
        self.assertEqual(llm.calls, [1800, 2800])

    def test_strong_title_cannot_override_missing_semantic_approval(self):
        target = Chunk("target", "target.pdf", 1, "Abstract",
                       "Dust destruction in bubbles driven by multiple supernovae explosions", "text", "h1")
        other = Chunk("other", "other.pdf", 1, "Abstract", "Single supernova dust", "text", "h2")

        class Retriever:
            def top_documents(self, plan, **kwargs):
                if plan.topic == "multiple supernova bubble dust":
                    return [("target.pdf", .85, [SimpleNamespace(chunk=target)]),
                            ("other.pdf", .60, [SimpleNamespace(chunk=other)])]
                return []

        class RejectingLLM:
            def json(self, stage, data, **kwargs):
                return {"papers": []}

        case = SimpleNamespace(case_id="B-97", input="多个超新星气泡中的尘埃破坏",
                               to_dict=lambda: {})
        handler = TaskHandlers(RejectingLLM(), "demo")
        plan = QueryPlan("mixed", subqueries=["multiple supernova bubble dust"])
        result = handler.retrieval(case, Retriever(), plan)
        self.assertEqual(result["papers"], [])
        self.assertEqual(handler.retrieval_trace["rule_added"], [])

    def test_subquery_replaces_lower_scored_duplicate(self):
        low = Chunk("low", "target.pdf", 2, "Body", "generic text", "text", "l")
        high = Chunk("high", "target.pdf", 1, "Abstract", "specific target abstract", "text", "h")

        class Retriever:
            def top_documents(self, plan, **kwargs):
                if plan.topic == "specific target":
                    return [("target.pdf", .85, [SimpleNamespace(chunk=high)])]
                return [("target.pdf", .10, [SimpleNamespace(chunk=low)])]

        class LLM:
            def json(self, stage, data, **kwargs):
                return {"papers": [{"filename": "target.pdf", "reason": "标题和摘要匹配。",
                                    "chunk_ids": ["high"]}]}

        case = SimpleNamespace(case_id="B-96", input="original target", to_dict=lambda: {})
        handler = TaskHandlers(LLM(), "demo")
        result = handler.retrieval(case, Retriever(), QueryPlan("mixed", subqueries=["specific target"]))
        self.assertEqual([row["filename"] for row in result["papers"]], ["target.pdf"])
        self.assertEqual(handler.retrieval_trace["candidates"], ["target.pdf"])

    def test_explicit_three_papers_discards_extra_model_approval(self):
        chunks = {name: Chunk(name, f"{name}.pdf", 1, "Abstract", f"specific {name} paper title",
                              "text", name) for name in ("one", "two", "three", "extra")}

        class Retriever:
            def top_documents(self, plan, **kwargs):
                if plan.topic in {"one", "two", "three"}:
                    name = plan.topic
                    return [(f"{name}.pdf", .9, [SimpleNamespace(chunk=chunks[name])])]
                return [("extra.pdf", .5, [SimpleNamespace(chunk=chunks["extra"])])]

        class LLM:
            def json(self, stage, data, **kwargs):
                return {"papers": [{"filename": row["filename"], "reason": "摘要相关。",
                                    "chunk_ids": [row["chunks"][0]["chunk_id"]]}
                                   for row in data["candidates"]]}

        case = SimpleNamespace(case_id="B-95", input="请找出三篇相关论文", to_dict=lambda: {})
        handler = TaskHandlers(LLM(), "demo")
        result = handler.retrieval(case, Retriever(), QueryPlan("mixed", subqueries=["one", "two", "three"]))
        self.assertEqual({row["filename"] for row in result["papers"]},
                         {"one.pdf", "two.pdf", "three.pdf"})
        self.assertTrue(handler.retrieval_trace["count_constrained"])

    def test_clear_subquery_peaks_remove_weak_model_approvals(self):
        chunks = {name: Chunk(name, f"{name}.pdf", 1, "Abstract", f"specific {name} title",
                              "text", name) for name in ("one", "two", "three", "extra")}

        class Retriever:
            def top_documents(self, plan, **kwargs):
                if plan.topic in {"one", "two", "three"}:
                    name = plan.topic
                    return [(f"{name}.pdf", .85, [SimpleNamespace(chunk=chunks[name])])]
                return [("extra.pdf", .4, [SimpleNamespace(chunk=chunks["extra"])])]

        class LLM:
            def json(self, stage, data, **kwargs):
                return {"papers": [{"filename": row["filename"], "reason": "摘要相关。",
                                    "chunk_ids": [row["chunks"][0]["chunk_id"]]}
                                   for row in data["candidates"]]}

        case = SimpleNamespace(case_id="B-94", input="寻找三个独立研究方向的论文", to_dict=lambda: {})
        handler = TaskHandlers(LLM(), "demo")
        result = handler.retrieval(case, Retriever(), QueryPlan("mixed", subqueries=["one", "two", "three"]))
        self.assertEqual({row["filename"] for row in result["papers"]},
                         {"one.pdf", "two.pdf", "three.pdf"})
        self.assertTrue(handler.retrieval_trace["tail_gap"])
        self.assertEqual(handler.retrieval_trace["tail_filtered"], ["extra.pdf"])

    def test_no_tail_pruning_when_score_gap_is_small(self):
        chunks = {name: Chunk(name, f"{name}.pdf", 1, "Abstract", f"specific {name} title",
                              "text", name) for name in ("one", "two", "extra")}

        class Retriever:
            def top_documents(self, plan, **kwargs):
                if plan.topic in {"one", "two"}:
                    name = plan.topic
                    return [(f"{name}.pdf", .8, [SimpleNamespace(chunk=chunks[name])])]
                return [("extra.pdf", .7, [SimpleNamespace(chunk=chunks["extra"])])]

        class LLM:
            def json(self, stage, data, **kwargs):
                return {"papers": [{"filename": row["filename"], "reason": "摘要相关。",
                                    "chunk_ids": [row["chunks"][0]["chunk_id"]]}
                                   for row in data["candidates"]]}

        case = SimpleNamespace(case_id="B-93", input="两个方向的论文", to_dict=lambda: {})
        handler = TaskHandlers(LLM(), "demo")
        result = handler.retrieval(case, Retriever(), QueryPlan("mixed", subqueries=["one", "two"]))
        self.assertEqual({row["filename"] for row in result["papers"]},
                         {"one.pdf", "two.pdf", "extra.pdf"})
        self.assertFalse(handler.retrieval_trace["tail_gap"])

    def test_invalid_model_approval_is_recorded_without_failing_whole_case(self):
        target = Chunk("hit", "target.pdf", 1, "Abstract", "target research paper", "text", "h")

        class Retriever:
            def top_documents(self, plan, **kwargs):
                return [("target.pdf", .8, [SimpleNamespace(chunk=target)])]

        class LLM:
            def json(self, stage, data, **kwargs):
                self_data = data["task"]
                assert "materials" not in self_data
                return {"papers": [{"filename": "outside.pdf", "reason": "wrong", "chunk_ids": ["hit"]},
                                   {"filename": "target.pdf", "reason": "supported", "chunk_ids": ["hit"]}]}

        case = SimpleNamespace(case_id="B-92", input="target", to_dict=lambda: {"materials": ["target.pdf"]})
        handler = TaskHandlers(LLM(), "demo")
        result = handler.retrieval(case, Retriever(), QueryPlan("target"))
        self.assertEqual([paper["filename"] for paper in result["papers"]], ["target.pdf"])
        self.assertEqual(handler.retrieval_trace["rejected_approvals"],
                         [{"batch": 0, "reason": "filename_not_in_batch"}])

    def test_high_ranked_wrong_chunk_id_gets_single_paper_recheck(self):
        target = Chunk("real", "target.pdf", 1, "Abstract", "harmonic locked optical laser transceiver",
                       "text", "h")

        class Retriever:
            def top_documents(self, plan, **kwargs):
                return [("target.pdf", .68, [SimpleNamespace(chunk=target)])]

        class LLM:
            calls = 0

            def json(self, stage, data, **kwargs):
                self.calls += 1
                return {"papers": [{"filename": "target.pdf", "reason": "标题与摘要匹配",
                                    "chunk_ids": ["wrong" if self.calls == 1 else "real"]}]}

        llm = LLM()
        case = SimpleNamespace(case_id="B-91", input="找一篇谐波锁定激光器论文",
                               to_dict=lambda: {"materials": ["target.pdf"]})
        handler = TaskHandlers(llm, "demo")
        result = handler.retrieval(case, Retriever(), QueryPlan("laser"))
        self.assertEqual([paper["filename"] for paper in result["papers"]], ["target.pdf"])
        self.assertEqual(llm.calls, 2)
        self.assertTrue(handler.retrieval_trace["rejected_approvals"][0]["recovered"])

    def test_long_abstract_is_kept_after_short_title_and_column_tail(self):
        chunks = [Chunk("title", "bio.pdf", 1, "title", "Biological Petri net formalism" * 5, "text", "t", 0),
                  Chunk("tail", "bio.pdf", 1, "Abstract", "lambda decision." * 14, "text", "l", 400),
                  Chunk("body", "bio.pdf", 1, "Abstract", "We introduce a unified biological Petri net formalism. " * 15,
                        "text", "b", 650),
                  Chunk("method", "bio.pdf", 2, "Method", "Signal hierarchy controls transitions. " * 12,
                        "text", "m", 2000)]
        selected = HybridRetriever(chunks).per_document(QueryPlan("Petri net"), maximum=3)
        self.assertIn("body", {chunk.chunk_id for chunk in selected})


if __name__ == "__main__":
    unittest.main()
