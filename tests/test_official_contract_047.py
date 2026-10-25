"""按组委会对外契约验证入口、授权和长文边界；不访问真实网关。"""
import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.models import Evidence
from research_agent.contracts.validation import validate_report_limits
from research_agent.evidence.reporting import deterministic_survey
from research_agent.integrations.llm import GatewayLLM


def question():
    return dict(case_id="A-01", function="Chat", title="Authorized question", input="Explain the scope.",
                materials=[], constraints=[], expected_output="Answer and question", output_schema_ref="Chat")


def assigned_environment(tmp_path):
    settings = Settings(tmp_path / "data", tmp_path / "out", team="contract-team")
    payload = question()
    base = settings.output / "data" / settings.team
    (base / "assigned").mkdir(parents=True)
    raw = json.dumps(payload, ensure_ascii=False).encode()
    (base / "assigned/A-01.json").write_bytes(raw)
    ids = ["A-01", "B-01", "B-02", "B-03", "C-01", "C-03", "D-01", "D-02", "D-05", "E-01", "E-02"]
    functions = dict(zip("ABCDE", ["Chat", "Literature_Retrieval", "Survey", "Reproduction", "Idea"]))
    rows = [dict(case_id=cid, function=functions[cid[0]], title=payload["title"],
                 question_file=f"data/{settings.team}/assigned/{cid}.json",
                 question_sha256=hashlib.sha256(raw).hexdigest() if cid == "A-01" else "0" * 64,
                 **({"difficulty": "easy"} if cid.startswith("D-") else {})) for cid in ids]
    manifest = dict(team=settings.team, schema_version=3, rules_version="research-assistant-objective-v3",
                    cases_per_category=dict(zip("ABCDE", [1, 3, 2, 3, 2])), case_ids=ids, cases=rows)
    (base / "dispatch.json").write_text(json.dumps(manifest), encoding="utf-8")
    return settings, payload, base


def test_single_checks_current_assigned_hash_and_exact_request_before_execution(tmp_path, monkeypatch):
    monkeypatch.delenv("RESEARCH_RUNTIME", raising=False)
    settings, payload, base = assigned_environment(tmp_path)
    engine = ResearchAgentEngine(settings)
    executed = []
    engine._execute_one = lambda case: executed.append(case.case_id) or {"case_id": case.case_id}
    # Other assigned files deliberately do not exist: Single must only read this question.
    assert engine.solve_sync(payload) == {"case_id": "A-01"}
    with pytest.raises(ValueError):
        engine.solve_sync(dict(payload, input="A different, unassigned question"))
    (base / "assigned/A-01.json").write_text(json.dumps(dict(payload, title="tampered")), encoding="utf-8")
    with pytest.raises(ValueError):
        engine.solve_sync(payload)
    assert executed == ["A-01"]


def test_recommended_gateway_environment_works_without_legacy_variables(monkeypatch):
    with patch.dict(os.environ, {"CHALLENGE_LLM_BASE_URL": "http://gateway.example/api/v1",
                               "CHALLENGE_LLM_API_KEY": "fixture-placeholder",
                               "CHALLENGE_LLM_MODEL": "fixture-model",
                               "CHALLENGE_SESSION_ID": "challenge-session"}, clear=True):
        gateway = GatewayLLM()
        assert gateway.available
        assert gateway.base_url == "http://gateway.example/api/v1"


def test_gateway_model_request_sends_challenge_session_header():
    pytest.importorskip("openai")
    env = {"PLATFORM_LLM_URL": "http://gateway.example/api/v1", "PLATFORM_API_KEY": "fixture-placeholder",
           "AGENT_MODEL_NAME": "fixture-model", "CHALLENGE_SESSION_ID": "challenge-session"}
    with patch.dict(os.environ, env, clear=True), patch("openai.OpenAI") as client:
        client.return_value.__enter__.return_value.chat.completions.create.return_value.model_dump.return_value = {"choices": []}
        GatewayLLM()._request_once("System", "Prompt", 20, 10)
        assert client.call_args.kwargs.get("default_headers", {}).get("X-Session-ID") == "challenge-session"


def test_bounded_survey_fallback_keeps_whole_verified_facts_and_all_papers():
    ledger = [Evidence(f"ev-{paper}-{row}", f"论文{paper}事实{row}：" + "保留研究条件与适用边界。" * 9,
                       f"paper-{paper}.pdf", 1, f"chunk-{paper}-{row}", "An exact authorized quote.",
                       facet=["method", "result", "limitation"][row % 3], verification="semantic_supported")
              for paper in range(5) for row in range(6)]
    markdown, used = deterministic_survey("比较方法和局限", ledger)
    validate_report_limits(markdown, "Survey")
    assert len("".join(markdown.split())) >= 800
    assert all(f"paper-{paper}.pdf" in markdown for paper in range(5))
    assert used and len(used) == len(set(used))
    assert all(item.claim in markdown for item in ledger if item.evidence_id in used)


@pytest.fixture
def official_client():
    pytest.importorskip("agenthack")
    from agenthack import AgentRuntime
    from agenthack.telemetry import Telemetry
    from fastapi.testclient import TestClient
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from agent import ResearchAgent
    from research_agent.integrations.http_runtime import install_research_protocol

    class Engine:
        settings = SimpleNamespace(case_timeout=180)
        failure = None

        async def solve(self, value):
            if self.failure:
                raise self.failure
            return dict(case_id=value["case_id"], answer="Answer", clarifying_question="Scope?")

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    engine = Engine()
    runtime = AgentRuntime(ResearchAgent(engine), telemetry=Telemetry(provider))
    install_research_protocol(runtime)
    with TestClient(runtime.app) as client:
        yield client, engine, exporter
    provider.shutdown()


def test_platform_transport_uuid_and_model_session_are_distinct(official_client, monkeypatch):
    client, _, exporter = official_client
    monkeypatch.setenv("CHALLENGE_SESSION_ID", "model-session")
    task_id = "a3b8f0f8-1a72-4537-bf6b-99420856537c"
    response = client.post("/solve", json=question(), headers={"X-Research-Task-ID": task_id, "Idempotency-Key": task_id})
    assert response.status_code == 200
    session = next(s for s in exporter.get_finished_spans() if s.name == "agent.session")
    assert session.attributes["task_id"] == task_id
    assert session.attributes["session_id"] == "model-session"
    assert response.json()["case_id"] == "A-01"


def test_failed_business_call_is_not_a_successful_200_wrapper(official_client):
    client, engine, _ = official_client
    engine.failure = FileNotFoundError("Private path and details")
    response = client.post("/solve", json=question())
    assert response.status_code == 500
    assert response.headers["x-agenthack-trace-id"]
    assert "Private path" not in response.text
    assert "output" not in response.json()


def test_papers_mount_and_team_name_fallback_do_not_use_telemetry_team_id(tmp_path):
    (tmp_path / "data/papers").mkdir(parents=True)
    with patch.dict(os.environ, {"RESEARCH_WORKSPACE_ROOT": str(tmp_path / "data"),
                               "RESEARCH_RESULT_ROOT": str(tmp_path / "out"),
                               "TEAM_NAME": "workspace-team", "TEAM_ID": "telemetry-only-id"}, clear=True):
        settings = Settings.from_env()
    assert settings.team == "workspace-team"
    assert settings.material_roots == [tmp_path / "data/papers"]
