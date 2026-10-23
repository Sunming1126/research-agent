"""Exercise the installed SDK app, not the development HTTP fallback."""
import asyncio
from contextlib import contextmanager
import json

import pytest
from fastapi.testclient import TestClient
from agenthack.telemetry import Telemetry

from agent import ResearchAgent, create_runtime
from fixtures import case


class RecordedTelemetry(Telemetry):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.finished = []

    @contextmanager
    def session(self, *args, **kwargs):
        self.requests.append((args, kwargs))
        with super().session(*args, **kwargs) as session:
            try:
                yield session
            finally:
                self.finished.append(session)


class EngineFixture:
    def __init__(self, result=None, error=None):
        self.inputs = []
        self.result, self.error = result, error
        self.cancelled = False

    async def solve(self, payload):
        self.inputs.append(payload)
        if self.error == "slow":
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if self.error:
            raise RuntimeError("fixture execution failure")
        return self.result or {"case_id": payload["case_id"], "fixture": "not a model response"}


@pytest.fixture
def app_factory(monkeypatch):
    monkeypatch.setenv("AGENTHACK_TELEMETRY_REQUIRED", "false")
    monkeypatch.setenv("CHALLENGE_SESSION_ID", "fixture-platform-session")
    def make(**kwargs):
        engine = EngineFixture(**kwargs)
        telemetry = RecordedTelemetry()
        return create_runtime(ResearchAgent(engine), telemetry=telemetry), engine, telemetry
    return make


@pytest.mark.parametrize("cid", ["A-03", "B-01", "B-02", "C-03", "D-05", "E-02", "A-100"])
def test_platform_direct_case_uses_sdk_session_and_unwrapped_json(app_factory, cid):
    runtime, engine, telemetry = app_factory()
    payload = case(cid)
    headers = {"traceparent": "00-12345678901234567890123456789012-1234567890123456-01"}
    with TestClient(runtime.app) as client:
        response = client.post("/solve", json=payload, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"case_id": cid, "fixture": "not a model response"}
        assert engine.inputs == [payload]
        assert len(telemetry.requests) == len(telemetry.finished) == 1
        args, _ = telemetry.requests[0]
        assert args[:3] == (cid, payload["function"], "fixture-platform-session")
        assert args[4]["traceparent"] == headers["traceparent"]
        assert telemetry.finished[0].attributes["trace_id"] == "12345678901234567890123456789012"
        assert client.get("/health").status_code == 200


def test_sdk_envelope_still_returns_sdk_metadata(app_factory):
    runtime, engine, telemetry = app_factory()
    payload = {"task_id": "legacy-client-task", "session_id": "legacy-session", "input": case("A-03"),
               "constraints": {"timeout_ms": 1000}}
    with TestClient(runtime.app) as client:
        response = client.post("/solve", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success" and body["task_id"] == "legacy-client-task"
    assert json.loads(body["output"]["answer"])["case_id"] == "A-03"
    assert body["metadata"]["session_id"] == "legacy-session"
    assert engine.inputs == [payload["input"]]


@pytest.mark.parametrize("payload", [None, [], {}, "text", {"case_id": "A-03"},
    dict(case("A-03"), function="Survey"), dict(case("B-01"), materials="papers"),
    dict(case("A-03"), constraints={"timeout_ms": 180000}),
    {"task_id": "old", "input": {}, "constraints": {"timeout_ms": 0}}])
def test_bad_input_rejected_before_execution(app_factory, payload):
    runtime, engine, telemetry = app_factory()
    with TestClient(runtime.app) as client:
        response = client.post("/solve", content=json.dumps(payload), headers={"Content-Type": "application/json"})
        assert response.status_code == 422
        assert client.get("/health").status_code == 200
    assert not engine.inputs and not telemetry.requests


def test_invalid_json_rejected_before_execution(app_factory):
    runtime, engine, _ = app_factory()
    with TestClient(runtime.app) as client:
        assert client.post("/solve", content=b'{not json').status_code == 422
    assert not engine.inputs


def test_direct_failure_is_http_error_not_successful_answer(app_factory):
    runtime, _, telemetry = app_factory(error="fail")
    with TestClient(runtime.app) as client:
        response = client.post("/solve", json=case("A-03"))
    assert response.status_code == 500
    assert "case_id" not in response.json()
    assert len(telemetry.finished) == 1


@pytest.mark.parametrize("result", [{"case_id": "A-02"}, ["invalid"]])
def test_direct_result_cannot_change_case_identity(app_factory, result):
    runtime, _, _ = app_factory(result=result)
    with TestClient(runtime.app) as client:
        assert client.post("/solve", json=case("A-03")).status_code == 500


def test_sdk_deadline_still_cancels_execution(app_factory):
    runtime, engine, _ = app_factory(error="slow")
    with TestClient(runtime.app) as client:
        response = client.post("/solve", json={"task_id": "deadline", "input": case("A-03"),
                                               "constraints": {"timeout_ms": 20}})
        assert response.status_code == 200 and response.json()["status"] == "timeout"
        assert client.get("/health").status_code == 200
    assert engine.cancelled
