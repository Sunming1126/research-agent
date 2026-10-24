"""在无外网的测试容器内验证实际服务、模拟 Gateway 和 OTLP/HTTP 收集。

容器主进程运行 agent.py；本脚本以 docker exec 执行。所有材料与模型响应
都是本地测试夹具，不能作为论文实验结果或官方评分证据。
"""
import gzip
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fitz
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


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
            elif data.get("function") == "Literature_Retrieval":
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

papers = Path("/output/http-smoke/papers")
papers.mkdir(parents=True, exist_ok=True)
doc = fitz.open()
page = doc.new_page()
page.insert_textbox(fitz.Rect(40, 40, 550, 750),
    "Authorized Synthetic Urban Study\n\nRemote sensing observations compare urban vegetation "
    "and surface temperature in matched urban districts. This synthetic fixture only describes "
    "association, not causation. Shade and water availability are possible confounding variables.", fontsize=12)
doc.save(papers / "Authorized_Urban_Study.pdf")
doc.close()

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


def post(payload, index, wrapped=False):
    case_id = payload["case_id"]
    trace_id = f"{index:032x}"
    headers = {"Content-Type": "application/json", "traceparent": f"00-{trace_id}-1234567890abcdef-01",
               "x-task-id": "smoke-" + case_id, "x-session-id": "offline-smoke-session"}
    body = {"task_id": "smoke-" + case_id, "input": payload} if wrapped else payload
    request = urllib.request.Request(base + "/solve", json.dumps(body).encode(), headers)
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=45) as response:
        assert response.status == 200
        value = json.load(response)
    if wrapped:
        assert value["status"] == "success"
        value = json.loads(value["output"]["answer"])
    assert value["case_id"] == case_id
    persisted = Path("/output/data/http_smoke") / (case_id + ".json")
    assert json.loads(persisted.read_text(encoding="utf-8")) == value
    spans = [span for span in SPANS if span["trace_id"] == trace_id]
    names = {span["name"] for span in spans}
    assert {"agent.session", "agent.task", "agent.tool"} <= names, names
    session = next(span for span in spans if span["name"] == "agent.session")
    assert session["parent_span_id"] == "1234567890abcdef"
    assert session["attributes"]["task_id"] == "smoke-" + case_id
    assert session["attributes"]["success"] is True
    if payload["function"] != "Reproduction":
        assert "agent.llm" in names
        assert session["attributes"]["llm_call_count"] > 0
        assert session["attributes"]["total_input_tokens"] > 0
        assert session["attributes"]["total_output_tokens"] > 0
    checks.append({"case_id": case_id, "input_form": "legacy_sdk" if wrapped else "direct_eight_fields",
                   "http_status": 200, "persisted_matches_response": True, "otlp_spans_received": len(spans),
                   "span_names": sorted(names), "trace_parent_preserved": True,
                   "llm_calls": session["attributes"]["llm_call_count"],
                   "seconds": round(time.monotonic() - started, 3)})
    return value


try:
    a = post(question("A-05", "Chat"), 1)
    assert set(a) == {"case_id", "answer", "clarifying_question"}
    b = question("B-01", "Literature_Retrieval")
    b["materials"] = ["Authorized_Urban_Study.pdf"]
    b_result = post(b, 2)
    assert b_result["papers"] and b_result["papers"][0]["filename"] == "Authorized_Urban_Study.pdf"
    d = question("D-01", "Reproduction")
    d["input"] = "name=Macro-F1, unit=score; name=Accuracy, unit=%; name=Runtime, unit=s."
    d["materials"] = ["not-present.pdf"]
    d_result = post(d, 3)
    assert d_result["status"] == "materials_missing" and len(d_result["metrics"]) == 3
    assert all(row["observed"] is None for row in d_result["metrics"])
    post(question("A-06", "Chat"), 4, wrapped=True)
    report = {"health": health, "checks": checks, "otlp_http_export_verified": True,
                      "gateway_requests": GATEWAY_CALLS, "real_model_calls": 0,
                      "materials_configuration": "Explicit test-only /output/http-smoke/papers root",
                      "formal_d_provenance_tested": False}
    Path("/output/http-runtime-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("/output/http-runtime-spans.json").write_text(json.dumps(SPANS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
finally:
    for receiver in receivers:
        receiver.shutdown()
