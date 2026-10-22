"""架构审查中发现的真实失败路径，覆盖预算、章节和否定语义。"""
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from research_agent.core.models import MaterialRef
from research_agent.core.session import CaseSession, checkpoint, current_session
from research_agent.integrations.llm import GatewayLLM, ModelError
from research_agent.rag.materials import Chunker
from research_agent.rag.retrieval import HybridRetriever, QueryPlan
from research_agent.workflows.tasks import explicitly_excluded_paper


def gateway(**kwargs):
    g = GatewayLLM(**kwargs)
    g.base_url, g.api_key, g.model = "https://example.invalid", "test", "test"
    return g


@pytest.mark.parametrize("finish,content", [("length", "{}"), ("stop", "{bad")])
def test_rejected_responses_still_count_all_usage(finish, content):
    g = gateway(token_budget=10000)
    body = {"usage": {"total_tokens": 123}, "choices": [
        {"finish_reason": finish, "message": {"content": content}}]}
    with patch.object(g, "_request_once", return_value=body), patch("research_agent.integrations.llm.time.sleep"):
        with pytest.raises(ModelError):
            g.json("chat", {}, max_tokens=10)
    assert g.total_tokens == g.calls * 123
    assert g.known_tokens == g.total_tokens and len(g.usage) == g.calls


def test_retry_rechecks_tokens_after_invalid_response():
    g = gateway(token_budget=1100)
    body = {"usage": {"total_tokens": 1000}, "choices": [
        {"finish_reason": "stop", "message": {"content": "{bad"}}]}
    with patch.object(g, "_request_once", return_value=body):
        with pytest.raises(ModelError, match="budget"):
            g.json("chat", {}, max_tokens=10)
    assert g.calls == 1 and g.total_tokens == 1000


def test_all_eight_distinct_targets_survive_query_plan():
    targets = [f"method {i}" for i in range(8)]
    assert QueryPlan.from_model({"subqueries": targets + targets}, "fallback").subqueries == targets


def test_appendix_after_references_is_searchable():
    material = MaterialRef(Path("paper.pdf"), "paper.pdf", "pdf", "a" * 64)
    chunks = Chunker().chunk(material, [(1, "References\n\nA citation", ""),
        (2, "Appendix A\n\nThe sensitivity experiment compares robust parameter settings.", "")])
    assert any(c.kind == "references" for c in chunks if c.page == 1)
    hits = HybridRetriever(chunks).search("sensitivity experiment")
    assert hits and hits[0].chunk.page == 2


@pytest.mark.parametrize("reason", ["This paper is not explicitly excluded and is relevant.",
    "该文不属于题目明确排除项，符合研究范围。", "该方法不应排除，与要求直接相关。"])
def test_negating_exclusion_does_not_reject_paper(reason):
    assert not explicitly_excluded_paper({"filename": "paper.pdf", "reason": reason}, [])


def test_session_cancellation_and_context_are_isolated():
    event = threading.Event()
    first = CaseSession("A-01", 10, cancelled=event)
    with first.activate():
        assert current_session() is first
        event.set()
        with pytest.raises(TimeoutError, match="cancelled"):
            checkpoint()
    assert current_session() is None
    with CaseSession("A-02", 10).activate():
        checkpoint("retrieval")


def test_session_counts_time_before_execution_and_stops_model_call():
    g = gateway()
    session = CaseSession("A-01", 1, started_at=time.monotonic() - 2)
    with patch.object(g, "_request_once") as call:
        with pytest.raises(TimeoutError):
            with session.activate():
                g.json("chat", {})
    call.assert_not_called()


def test_sdk_and_http_fallback_share_thinking_and_system_constraints():
    import sys
    g = gateway()
    payloads = []
    result = {"usage": {"total_tokens": 20}, "choices": [{"finish_reason": "stop",
               "message": {"content": '{"answer":"ok"}'}}]}

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps(result).encode()

    def request(req, **kwargs):
        payloads.append(json.loads(req.data))
        return Response()

    with patch.dict(sys.modules, {"openai": None}), \
         patch.dict("os.environ", {"RESEARCH_LLM_THINKING": "disabled"}), \
         patch("urllib.request.urlopen", request):
        assert g.json("chat", {}, max_tokens=10)["answer"] == "ok"
    assert payloads[0]["thinking"] == {"type": "disabled"}
    assert payloads[0]["messages"][0]["content"] == GatewayLLM.SYSTEM
    assert payloads[0]["stream"] is False and g.total_tokens == 20


@pytest.mark.parametrize("choices", [None, "invalid", [{"finish_reason": "stop", "message": None}]])
def test_malformed_gateway_envelope_is_bounded_and_accounted(choices):
    g = gateway(token_budget=10000)
    with patch.object(g, "_request_once", return_value={"choices": choices, "usage": {"total_tokens": 15}}), \
         patch("research_agent.integrations.llm.time.sleep"):
        with pytest.raises(ModelError):
            g.json("chat", {}, max_tokens=10)
    assert g.calls == 2 and g.total_tokens == 30
