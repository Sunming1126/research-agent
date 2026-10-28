"""在官方运行时边界转换题目/答卷；执行和追踪仍由原生 SDK 完成。"""
from __future__ import annotations

import json
import os
from urllib.parse import quote

from starlette.responses import JSONResponse

from ..core.models import CaseContext
from ..contracts.validation import validate_wire_fields
from ..core.b_submission import B_REQUEST, failure_submission
from ..core.utils import read_json, confined


class ResearchRequestAdapter:
    """八字段请求走原生 SDK，成功答卷按赛题 Schema 返回，SDK 元数据放响应头。"""

    def __init__(self, app, timeout_supplier, settings_supplier=None):
        self.app = app
        self.timeout_supplier = timeout_supplier
        self.settings_supplier = settings_supplier

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/solve" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 2_000_000:
                return await JSONResponse({"detail": "request body too large"}, status_code=413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        raw = bytes(body)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            payload = None  # 保留原始字节，由 SDK 自身报告 JSON 格式错误。

        case = None
        if isinstance(payload, dict) and ("case_id" in payload or "function" in payload):
            try:
                case = CaseContext.from_dict(payload)
            except ValueError as exc:
                return await JSONResponse({"detail": str(exc)}, status_code=422)(scope, receive, send)
            headers = {key.decode("latin-1").lower(): value.decode("latin-1")
                       for key, value in scope.get("headers", [])}
            timeout = min(self.timeout_supplier(), 180)
            envelope = {
                "task_id": (headers.get("x-research-task-id") or headers.get("idempotency-key")
                            or headers.get("x-task-id") or headers.get("x-agenthack-task-id")
                            or os.getenv("AGENTHACK_TASK_ID") or os.getenv("TASK_ID") or case.case_id),
                "task_type": case.function,
                "input": case.to_dict(),
                "constraints": {"timeout_ms": max(1, int(timeout * 1000))},
            }
            session = (os.getenv("CHALLENGE_SESSION_ID")
                       or headers.get("x-session-id") or headers.get("x-agenthack-session-id")
                       or os.getenv("AGENTHACK_SESSION_ID"))
            if session:
                envelope["session_id"] = session
            raw = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
            scope = dict(scope, headers=[(key, value) for key, value in scope.get("headers", [])
                                        if key.lower() != b"content-length"]
                         + [(b"content-length", str(len(raw)).encode("ascii"))])

        delivered = False

        async def adapted_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return await receive()

        if case is None:
            return await self.app(scope, adapted_receive, send)

        # 原生路由完成业务、保存和 OTLP flush 后才产生响应。
        # 成功解包；B 失败按题型保存空答卷并保留失败证据，其他类型保持错误映射。
        start = None
        chunks = bytearray()
        b_slot = {} if case.function == 'Literature_Retrieval' else None

        async def adapted_send(message):
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
                return
            if message["type"] != "http.response.body":
                return await send(message)
            chunks.extend(message.get("body", b""))
            if message.get("more_body", False):
                return
            body = bytes(chunks)
            headers = list(start.get("headers", []))
            status = start["status"]
            try:
                envelope = json.loads(body)
                if not isinstance(envelope, dict):
                    raise ValueError("runtime response must be a JSON object")
                metadata = envelope.get("metadata", {})
                if metadata is None:
                    metadata = {}
                if not isinstance(metadata, dict):
                    raise ValueError("runtime metadata must be a JSON object")
                if status == 200 and envelope.get("status") in {"success", "error", "timeout"} and not isinstance(envelope.get("output"), dict):
                    raise ValueError("runtime output must be a JSON object")
                if status == 200 and envelope.get("status") in {"success", "error", "timeout"}:
                    # 只复制 SDK 实际给出的值，不生成/冒充追踪标识。
                    for name in ("trace_id", "session_id"):
                        value = metadata.get(name)
                        if value:
                            headers.append((f"x-agenthack-{name.replace('_', '-')}".encode(), str(value).encode("ascii")))
                    headers.append((b"x-agenthack-metadata", json.dumps(metadata, ensure_ascii=True, separators=(",", ":")).encode("ascii")))
                if status == 200 and envelope.get("status") == "success":
                    result = json.loads(envelope["output"]["answer"])
                    validate_wire_fields(case, result)
                    body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
                elif status == 200 and envelope.get("status") in {"error", "timeout"}:
                    if b_slot is not None and self.settings_supplier:
                        error_type = envelope.get('output', {}).get('error', 'RuntimeError')
                        outcome = b_slot.get('outcome')
                        settings = self.settings_supplier()
                        if outcome is None:
                            outcome = failure_submission(settings, case, error_type)
                        result = outcome['result']
                        validate_wire_fields(case, result)
                        if outcome['result_saved']:
                            if read_json(confined(settings.output, outcome['path'])) != result:
                                raise ValueError('B failure response/file mismatch')
                        status = 200 if outcome['result_saved'] else 500
                        body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
                    else:
                        status = 504 if envelope["status"] == "timeout" else 500
                        body = json.dumps({"detail": "research task " + envelope["status"],
                                           "error_type": envelope.get("output", {}).get("error", "RuntimeError")}).encode("utf-8")
                if b_slot is not None and b_slot.get('outcome'):
                    outcome = b_slot['outcome']
                    headers.append((b'x-research-outcome', outcome['outcome'].encode('ascii')))
                    headers.append((b'x-research-result-saved', str(outcome['result_saved']).lower().encode('ascii')))
                    if outcome.get('error_type'):
                        headers.append((b'x-research-error-type', str(outcome['error_type']).encode('ascii')))
                    if outcome.get('diagnostic_path'):
                        headers.append((b'x-research-diagnostic-path', quote(outcome['diagnostic_path'], safe='/').encode('ascii')))
            except (ValueError, KeyError, TypeError, UnicodeError):
                # SDK 成功但无法转成合法答卷时显式失败，不能泄漏包装体作为成功输出。
                return await JSONResponse({"detail": "invalid research answer from runtime"}, status_code=502)(scope, adapted_receive, send)
            headers = [(key, value) for key, value in headers if key.lower() != b"content-length"]
            await send(dict(start, status=status, headers=headers + [(b"content-length", str(len(body)).encode("ascii"))]))
            await send({"type": "http.response.body", "body": body, "more_body": False})

        token = B_REQUEST.set(b_slot) if b_slot is not None else None
        try:
            return await self.app(scope, adapted_receive, adapted_send)
        finally:
            if token is not None:
                B_REQUEST.reset(token)


def install_research_protocol(runtime):
    """为原生 AgentRuntime 注册协议边界转换；实例及其 run()/路由保持原样。"""
    runtime.app.add_middleware(ResearchRequestAdapter,
                               timeout_supplier=lambda: runtime.agent.engine.settings.case_timeout,
                               settings_supplier=lambda: runtime.agent.engine.settings)
