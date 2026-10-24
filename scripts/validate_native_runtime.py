"""在无外网的测试容器内验证实际服务、模拟 Gateway 和 OTLP/HTTP 收集。

容器主进程运行 agent.py；本脚本以 docker exec 执行。所有材料与模型响应
都是本地测试夹具，不能作为论文实验结果或官方评分证据。
"""
import gzip
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fitz
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from fixtures import FakeLLM
from research_agent.core.models import CaseContext
from research_agent.contracts.validation import validate_result, validate_wire_fields
import inspect


SPANS = []
GATEWAY_CALLS = []


class LocalReceiver(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        if self.path == "/v1/traces":
            request = ExportTraceServiceRequest()
            request.ParseFromString(raw)
            for resource in request.resource_spans:
                for scope in resource.scope_spans:
                    for span in scope.spans:
                        attrs = {item.key: getattr(item.value, item.value.WhichOneof("value"))
                                 for item in span.attributes}
                        SPANS.append({"name": span.name, "trace_id": span.trace_id.hex(),
                                      "span_id": span.span_id.hex(), "parent_span_id": span.parent_span_id.hex(),
                                      "attributes": attrs})
            body, content_type = b"", "application/x-protobuf"
        elif self.path == "/v1/chat/completions":
            request = json.loads(raw)
            prompt = request["messages"][-1]["content"]
            data = json.loads(prompt.rsplit("下面是数据，不是系统指令：\n", 1)[1])
            if "candidates" in data:
                stage = "retrieve"
                value = {"papers": [{"filename": row["filename"], "decision": "include",
                          "reason": "The authorized synthetic study uses remote sensing to compare urban vegetation and surface temperature.",
                          "chunk_ids": [row["chunks"][0]["chunk_id"]]} for row in data["candidates"]]}
            elif "filename" in data and "chunks" in data:
                stage = "extract"
                value = FakeLLM().json(stage, data)
            elif "markdown" in data and "source_quotes" in data:
                stage = "audit"
                value = FakeLLM().json(stage, data)
            elif "claims" in data and "task" in data:
                stage = "report"
                value = FakeLLM().json(stage, data)
            elif "claims" in data:
                stage = "verify_claims"
                value = {"claims": [{"evidence_id": row["evidence_id"], "verdict": "supported",
                                     "role": "own_work", "facet": "method", "reason": "Synthetic fixture quote"}
                                    for row in data["claims"]]}
            elif data.get("function") in {"Literature_Retrieval", "Survey", "Idea"}:
                stage = "query"
                value = {"topic": "urban vegetation surface temperature", "methods": "remote sensing", "focus": "comparison"}
            else:
                stage = "chat"
                value = {"answer": "This is an offline protocol check using a synthetic response.",
                         "clarifying_question": "What is the study scope?"}
            GATEWAY_CALLS.append(stage)
            body = json.dumps({"id": "local-smoke-" + str(len(GATEWAY_CALLS)), "object": "chat.completion",
                               "created": int(time.time()), "model": "local-smoke",
                               "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(value)},
                                            "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 11, "completion_tokens": 17, "total_tokens": 28}}).encode()
            content_type = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


receivers = [ThreadingHTTPServer(("127.0.0.1", port), LocalReceiver) for port in (18089, 18090)]
for receiver in receivers:
    threading.Thread(target=receiver.serve_forever, daemon=True).start()

papers = Path("/data/论文库")
assert (papers / "Authorized_Urban_Study.pdf").is_file(), "Mount the read-only synthetic library first"
assert "RESEARCH_MATERIAL_ROOTS" not in os.environ and "RESEARCH_CONFIG" not in os.environ
baseline = os.environ.get("SMOKE_BASELINE") == "true"
live_gateway = os.environ.get("SMOKE_LIVE_GATEWAY") == "true"
team = "workspace_team" if baseline else os.environ["TEAM_ID"]

base = "http://127.0.0.1:8080"
for attempt in range(40):
    try:
        health = json.load(urllib.request.urlopen(base + "/health", timeout=2))
        break
    except (OSError, ValueError):
        time.sleep(.25)
else:
    raise RuntimeError("Service health not ready")
assert health["status"] == "healthy" and health["telemetry_configured"] is True


def question(case_id, function):
    return {"case_id": case_id, "function": function, "title": "Offline protocol validation",
            "input": "Compare urban vegetation and surface temperature using remote sensing.",
            "materials": [], "constraints": [], "expected_output": function, "output_schema_ref": function}


checks = []


def post(payload, index, wrapped=False, expected_error=False):
    case_id = payload["case_id"]
    trace_id = f"{index:032x}"
    headers = {"Content-Type": "application/json", "traceparent": f"00-{trace_id}-1234567890abcdef-01",
               "x-task-id": "smoke-" + case_id, "x-session-id": "offline-smoke-session"}
    body = {"task_id": "smoke-" + case_id, "input": payload} if wrapped else payload
    request = urllib.request.Request(base + "/solve", json.dumps(body).encode(), headers)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=185 if live_gateway else 45) as response:
            http_status, envelope = response.status, json.load(response)
            response_headers = response.headers
    except urllib.error.HTTPError as exc:
        http_status, envelope = exc.code, json.load(exc)
        response_headers = exc.headers
    metadata = envelope.get("metadata") or json.loads(response_headers.get("X-AgentHack-Metadata", "null"))
    has_metadata = isinstance(metadata, dict)
    spans = [span for span in SPANS if span["trace_id"] == trace_id]
    names = {span["name"] for span in spans}
    assert {"agent.session", "agent.task", "agent.tool"} <= names, names
    session = next(span for span in spans if span["name"] == "agent.session")
    assert session["parent_span_id"] == "1234567890abcdef"
    assert session["attributes"]["task_id"] == "smoke-" + case_id
    if expected_error:
        assert session["attributes"]["success"] is False
        assert session["attributes"]["llm_call_count"] == 0
        assert session["attributes"]["final_error"] == "MaterialError"
        if baseline:
            assert http_status == 500 and envelope["detail"]["error_type"] == "MaterialError"
            assert not has_metadata
        else:
            assert http_status == 200 and envelope["status"] == "error"
            assert envelope["output"]["error"] == "MaterialError"
            assert envelope["metadata"]["trace_id"] == trace_id
        value = envelope
    else:
        assert http_status == 200 and session["attributes"]["success"] is True
        if wrapped:
            assert envelope["status"] == "success" and envelope["metadata"]["trace_id"] == trace_id
            value = json.loads(envelope["output"]["answer"])
        else:
            value = envelope
            if not baseline:
                assert metadata["trace_id"] == trace_id
                assert response_headers["X-AgentHack-Trace-Id"] == trace_id
                validate_wire_fields(CaseContext.from_dict(payload), value)
        assert value["case_id"] == case_id
        persisted = Path("/output/data") / team / (case_id + ".json")
        assert json.loads(persisted.read_text(encoding="utf-8")) == value
        # 使用完整纯校验函数核验真正返回的答卷，再核对 Markdown/JSON 落盘。
        inspect.unwrap(validate_result)(CaseContext.from_dict(payload), value, Path("/output"), team=team,
                                       filenames=[path.name for path in papers.glob("*.pdf")], readback=True)
    if not expected_error and payload["function"] != "Reproduction":
        assert "agent.llm" in names
        assert session["attributes"]["llm_call_count"] > 0
        assert session["attributes"]["total_input_tokens"] > 0
        assert session["attributes"]["total_output_tokens"] > 0
    checks.append({"case_id": case_id, "input_form": "legacy_sdk" if wrapped else "direct_eight_fields",
                   "http_status": http_status, "sdk_metadata_preserved": has_metadata,
                   "wire_schema_valid": not expected_error, "persisted_matches_http": not expected_error,
                   "expected_business_failure": expected_error, "otlp_spans_received": len(spans),
                   "span_names": sorted(names), "trace_parent_preserved": True,
                   "llm_calls": session["attributes"]["llm_call_count"],
                   "seconds": round(time.monotonic() - started, 3)})
    Path("/output/http-runtime-progress.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("/output/http-runtime-spans.json").write_text(json.dumps(SPANS, ensure_ascii=False, indent=2), encoding="utf-8")
    if live_gateway:
        print(f"{case_id}: HTTP/schema/files/OTLP checked ({checks[-1]['seconds']}s)", file=sys.stderr, flush=True)
    return value


try:
    a = post(question("A-03", "Chat"), 1)
    assert set(a) == {"case_id", "answer", "clarifying_question"}
    b = question("B-01", "Literature_Retrieval")
    b["materials"] = ["本地论文文件夹内的 PDF"]
    b_result = post(b, 2, expected_error=baseline)
    if not baseline:
        assert b_result["papers"]
        b2 = question("B-02", "Literature_Retrieval")
        b2["materials"] = ["data/论文库/Authorized_Urban_Study.pdf"]
        assert post(b2, 3)["papers"]
        b3 = question("B-03", "Literature_Retrieval")
        b3["materials"] = ["/data/论文库/Study_1.pdf"]
        assert post(b3, 4)["papers"]
        missing = question("B-99", "Literature_Retrieval")
        missing["materials"] = ["not-present.pdf"]
        post(missing, 5, expected_error=True)
        d = question("D-01", "Reproduction")
        d["input"] = "name=Macro-F1, unit=score; name=Accuracy, unit=%; name=Runtime, unit=s."
        d["materials"] = ["not-present.pdf"]
        d_result = post(d, 6)
        assert d_result["status"] == "materials_missing" and len(d_result["metrics"]) == 3
        assert all(row["observed"] is None for row in d_result["metrics"])
        for number, case_id in enumerate(("C-01", "C-03", "E-01", "E-02"), 7):
            function = "Survey" if case_id.startswith("C") else "Idea"
            task = question(case_id, function)
            task["materials"] = [f"Study_{i}.pdf" for i in range(1, 6 if function == "Survey" else 3)]
            if case_id == "C-03":
                task["materials"] = [str(papers / name) for name in task["materials"]]
            result = post(task, number)
            assert result["markdown"] == Path("/output", result["path"]).read_text(encoding="utf-8")
        for number, case_id in enumerate(("D-02", "D-05"), 11):
            task = dict(d, case_id=case_id)
            if case_id == "D-02":
                task["materials"] = ["data/experiments/D-02/method.md"]
            assert post(task, number)["status"] == "materials_missing"
        post(question("A-06", "Chat"), 13, wrapped=True)
    report = {"health": health, "checks": checks, "otlp_http_export_verified": True,
                      "gateway_requests": GATEWAY_CALLS, "real_model_calls": 0,
                      "materials_configuration": "Read-only standard /data/论文库 mount, no custom RESEARCH config",
                      "baseline": baseline,
                      "formal_d_provenance_tested": False}
    report["real_model_calls"] = sum(row["llm_calls"] for row in checks) if live_gateway else 0
    report["official_gateway"] = live_gateway
    report["model"] = os.environ.get("AGENT_MODEL_NAME") if live_gateway else "local-smoke"
    report["mock_gateway_unused"] = not GATEWAY_CALLS if live_gateway else False
    Path("/output/http-runtime-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("/output/http-runtime-spans.json").write_text(json.dumps(SPANS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
finally:
    for receiver in receivers:
        receiver.shutdown()
