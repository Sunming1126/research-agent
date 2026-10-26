"""以真实 SDK 验证直接题目输入、追踪关联、超时和错误路径。"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("agenthack")
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from agenthack.telemetry import Telemetry
from agenthack import AgentRuntime

from agent import ResearchAgent
from research_agent.integrations.http_runtime import install_research_protocol


def question():
    return {"case_id": "A-05", "function": "Chat", "title": "HTTP regression",
            "input": "Explain the question and clarify scope.", "materials": [],
            "constraints": ["Return a single clarifying question"],
            "expected_output": "Answer and question", "output_schema_ref": "Chat"}


class Engine:
    def __init__(self):
        self.settings = SimpleNamespace(case_timeout=180)
        self.inputs = []
        self.failure = None

    async def solve(self, value):
        self.inputs.append(value)
        if self.failure:
            raise self.failure
        return {"case_id": value["case_id"], "answer": "Answer", "clarifying_question": "Scope?"}


@pytest.fixture
def runtime():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    engine = Engine()
    instance = AgentRuntime(ResearchAgent(engine), telemetry=Telemetry(provider))
    original_routes = [(route.path, id(route.endpoint)) for route in instance.app.routes]
    install_research_protocol(instance)
    assert type(instance) is AgentRuntime
    assert original_routes == [(route.path, id(route.endpoint)) for route in instance.app.routes]
    with TestClient(instance.app) as client:
        yield client, engine, exporter
    provider.shutdown()


def test_direct_question_returns_exact_case_schema_and_preserves_metadata_in_headers(runtime):
    client, engine, exporter = runtime
    payload = question()
    response = client.post("/solve", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"case_id", "answer", "clarifying_question"}
    metadata = json.loads(response.headers["x-agenthack-metadata"])
    assert body == {"case_id": "A-05", "answer": "Answer", "clarifying_question": "Scope?"}
    assert engine.inputs == [payload]
    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {"agent.session", "agent.task"}
    session = next(span for span in spans if span.name == "agent.session")
    assert session.attributes["task_id"] == "A-05"
    assert session.attributes["task_type"] == "Chat"
    assert session.attributes["success"] is True
    assert (body.get("metadata") or json.loads(response.headers["x-agenthack-metadata"]))["trace_id"] == f"{session.context.trace_id:032x}"
    assert metadata["session_id"] == session.attributes["session_id"]


def test_platform_trace_parent_task_and_session_headers_are_preserved(runtime):
    client, _, exporter = runtime
    trace_id, parent_id = "1234567890abcdef1234567890abcdef", "1234567890abcdef"
    response = client.post("/solve", json=question(), headers={
        "traceparent": f"00-{trace_id}-{parent_id}-01",
        "x-task-id": "platform-task-a05", "x-session-id": "platform-session",
    })
    assert response.status_code == 200
    session = next(span for span in exporter.get_finished_spans() if span.name == "agent.session")
    assert session.context.trace_id == int(trace_id, 16)
    assert session.parent.span_id == int(parent_id, 16)
    assert session.attributes["task_id"] == "platform-task-a05"
    assert session.attributes["session_id"] == "platform-session"
    assert response.headers["x-agenthack-trace-id"] == trace_id


def test_official_transport_id_and_challenge_session_are_distinct_from_case_and_team(runtime):
    client, _, exporter = runtime
    with patch.dict("os.environ", {"CHALLENGE_SESSION_ID": "gateway-session", "TEAM_ID": "telemetry-team"}):
        response = client.post("/solve", json=question(), headers={
            "X-Research-Task-ID": "transport-uuid", "Idempotency-Key": "transport-uuid",
            "X-Task-ID": "obsolete", "X-Challenge-Protocol": "research-assistant.v1"})
    assert response.status_code == 200
    assert response.json()["case_id"] == "A-05"
    session = next(span for span in exporter.get_finished_spans() if span.name == "agent.session")
    assert session.attributes["task_id"] == "transport-uuid"
    assert session.attributes["session_id"] == "gateway-session"


def test_direct_timeout_uses_case_budget_instead_of_sdk_thirty_seconds(runtime):
    import asyncio
    client, engine, _ = runtime
    original = asyncio.wait_for
    timeouts = []

    async def wait_for(awaitable, timeout):
        timeouts.append(timeout)
        return await original(awaitable, timeout)

    engine.settings.case_timeout = 75
    with patch("agenthack.runtime.asyncio.wait_for", side_effect=wait_for):
        assert client.post("/solve", json=question()).status_code == 200
        engine.settings.case_timeout = 600
        assert client.post("/solve", json=question()).status_code == 200
    assert timeouts == [75, 180]


def test_legacy_sdk_envelope_still_returns_metadata(runtime):
    client, engine, exporter = runtime
    response = client.post("/solve", json={"task_id": "legacy-task", "input": question()})
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == "legacy-task" and body["status"] == "success"
    assert json.loads(body["output"]["answer"])["case_id"] == "A-05"
    assert body["metadata"]["trace_id"]
    assert len(engine.inputs) == 1
    assert any(span.name == "agent.task" for span in exporter.get_finished_spans())


def test_business_failure_preserves_sdk_error_metadata_and_trace(runtime):
    client, engine, exporter = runtime
    engine.failure = FileNotFoundError("private filesystem details")
    response = client.post("/solve", json=question())
    assert response.status_code == 500
    body = response.json()
    assert body["error_type"] == "FileNotFoundError"
    assert "private filesystem details" not in response.text
    session = next(span for span in exporter.get_finished_spans() if span.name == "agent.session")
    assert session.attributes["success"] is False
    assert session.attributes["final_error"] == "FileNotFoundError"
    assert (body.get("metadata") or json.loads(response.headers["x-agenthack-metadata"]))["trace_id"] == f"{session.context.trace_id:032x}"


def test_invalid_question_is_rejected_before_running_agent(runtime):
    client, engine, exporter = runtime
    payload = question()
    payload["constraints"] = {"timeout_ms": 1000}
    response = client.post("/solve", json=payload)
    assert response.status_code == 422
    assert not engine.inputs and not exporter.get_finished_spans()


def test_timeout_preserves_sdk_timeout_response_and_trace(runtime):
    client, engine, exporter = runtime
    engine.failure = TimeoutError("expired")
    response = client.post("/solve", json=question())
    assert response.status_code == 504
    assert response.json()["detail"] == "research task timeout"
    assert response.headers["x-agenthack-trace-id"]
    session = next(span for span in exporter.get_finished_spans() if span.name == "agent.session")
    assert session.attributes["task_status"] == "timeout"


def test_sdk_routes_stay_owned_by_official_runtime(runtime):
    client, _, _ = runtime
    endpoints = {route.path: route.endpoint for route in client.app.routes if route.path in {"/health", "/solve"}}
    assert all(endpoint.__module__ == "agenthack.runtime" for endpoint in endpoints.values())
    assert client.get("/health").json()["status"] == "healthy"


def test_malformed_json_uses_native_sdk_validation(runtime):
    client, engine, _ = runtime
    response = client.post("/solve", content=b'{"input":', headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "json_invalid"
    assert not engine.inputs


@pytest.mark.parametrize("function,fields", [
    ("Chat", {"answer": "Answer", "clarifying_question": "Scope?"}),
    ("Literature_Retrieval", {"papers": [{"filename": "p.pdf", "reason": "Relevant evidence"}]}),
    ("Survey", {"path": "data/expert/C-99-综述-test.md", "markdown": "fixture"}),
    ("Idea", {"path": "data/expert/E-99-灵感Idea报告-test.md", "markdown": "fixture"}),
    ("Reproduction", {"status": "materials_missing", "metrics": [], "figures": []}),
])
def test_final_http_schema_has_only_case_fields_for_all_five_categories(runtime, function, fields):
    # 这里特意断言最外层；报告内容/指标由引擎与落盘集成测试独立校验。
    client, engine, exporter = runtime
    prefix = {"Chat": "A", "Literature_Retrieval": "B", "Survey": "C", "Idea": "E", "Reproduction": "D"}[function]
    payload = dict(question(), case_id=prefix + "-99", function=function, output_schema_ref=function)
    expected = {"case_id": payload["case_id"], **fields}

    async def solve(value):
        return expected

    engine.solve = solve
    response = client.post("/solve", json=payload)
    assert response.status_code == 200
    assert response.json() == expected
    assert int(response.headers["content-length"]) == len(response.content)
    trace = next(span for span in exporter.get_finished_spans() if span.name == "agent.session")
    assert response.headers["x-agenthack-trace-id"] == f"{trace.context.trace_id:032x}"


def test_invalid_success_wrapper_cannot_be_returned_as_a_valid_case(runtime):
    client, engine, _ = runtime

    async def solve(value):
        return {"case_id": "A-05", "answer": "Answer", "clarifying_question": "Scope?", "extra": "invalid"}

    engine.solve = solve
    response = client.post("/solve", json=question())
    assert response.status_code == 502


def test_real_b_workflow_writes_platform_directory_and_matches_http(tmp_path):
    """使用真实引擎/SDK和合成 PDF，模型夹具只检查接入及路径，不代表比赛得分。"""
    import hashlib
    from fixtures import FakeLLM, case, papers
    from test_dispatch_v3 import _write_v3_dispatch
    from research_agent.core.config import Settings
    from research_agent.core.engine import ResearchAgentEngine

    output = tmp_path / "output"
    _write_v3_dispatch(output)
    names = papers(tmp_path / "data/papers")
    payload = case("B-01", names)
    assigned = output / "data/team/assigned/B-01.json"
    assigned.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    dispatch_path = output / "data/team/dispatch.json"
    dispatch = json.loads(dispatch_path.read_text())
    row = next(row for row in dispatch["cases"] if row["case_id"] == "B-01")
    row.update(title=payload["title"], question_sha256=hashlib.sha256(assigned.read_bytes()).hexdigest())
    dispatch_path.write_text(json.dumps(dispatch), encoding="utf-8")

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    env = {"RESEARCH_WORKSPACE_ROOT": str(tmp_path / "data"), "RESEARCH_RESULT_ROOT": str(output),
           "RESEARCH_WORKSPACE_TEAM": "team", "TEAM_ID": "telemetry-uuid", "TEAM_NAME": "最初一舞",
           "CHALLENGE_SESSION_ID": "gateway-session"}
    try:
        with patch.dict("os.environ", env, clear=True):
            engine = ResearchAgentEngine(Settings.from_env(), FakeLLM)
            instance = AgentRuntime(ResearchAgent(engine), telemetry=Telemetry(provider))
            install_research_protocol(instance)
            with TestClient(instance.app) as client:
                response = client.post("/solve", json=payload, headers={"X-Research-Task-ID": "transport-uuid"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"case_id", "papers"} and body["papers"]
        assert all(set(paper) == {"filename", "reason"} and paper["filename"] in names for paper in body["papers"])
        assert json.loads((output / "data/team/B-01.json").read_text(encoding="utf-8")) == body
        assert not (output / "data/最初一舞").exists()
        assert not (output / "data/telemetry-uuid").exists()
        session = next(span for span in exporter.get_finished_spans() if span.name == "agent.session")
        assert session.attributes["task_id"] == "transport-uuid"
        assert session.attributes["session_id"] == "gateway-session"
    finally:
        provider.shutdown()
