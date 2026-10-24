"""在官方运行时边界转换题目/答卷；执行和追踪仍由原生 SDK 完成。"""
from __future__ import annotations

import json
import os
import time
import uuid

from starlette.responses import JSONResponse

from ..core.models import CaseContext
from ..core.request_context import RequestContext, request_scope
from ..contracts.validation import validate_wire_fields


class ResearchRequestAdapter:
    """八字段请求走原生 SDK，成功答卷按赛题 Schema 返回，SDK 元数据放响应头。"""

    def __init__(self, app, timeout_supplier):
        self.app = app
        self.timeout_supplier = timeout_supplier

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/solve" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)

        received_at = time.monotonic()
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
            if headers.get("x-challenge-protocol", "research-assistant.v1") != "research-assistant.v1":
                return await JSONResponse({"detail": "unsupported research protocol"}, status_code=422)(scope, receive, send)
            timeout = min(self.timeout_supplier(), 180)
            # SDK 的 finally 中最多 flush 5 秒，另留 2 秒用于 JSON/文件回读与传输。
            execution_deadline = received_at + max(.001, timeout - 7)
            task_id = headers.get("x-research-task-id") or headers.get("idempotency-key")
            if (headers.get("x-research-task-id") and headers.get("idempotency-key")
                    and headers["x-research-task-id"] != headers["idempotency-key"]):
                return await JSONResponse({"detail": "conflicting platform task identifiers"}, status_code=422)(scope, receive, send)
            task_id = (task_id or headers.get("x-task-id") or headers.get("x-agenthack-task-id")
                       or os.getenv("AGENTHACK_TASK_ID") or os.getenv("TASK_ID") or str(uuid.uuid4()))
            envelope = {
                "task_id": task_id,
                "task_type": case.function,
                "input": case.to_dict(),
                "constraints": {"timeout_ms": max(1, int((execution_deadline - time.monotonic()) * 1000))},
            }
            session = (os.getenv("CHALLENGE_SESSION_ID") or headers.get("x-session-id") or headers.get("x-agenthack-session-id")
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

        # 原生路由完成业务、保存和 OTLP flush 后才产生响应。仅转换成功的答卷；
        # 错误保留 SDK 真实类型与 metadata，以非 200 表达；不能冒充合法答卷。
        start = None
        chunks = bytearray()

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
            try:
                envelope = json.loads(body)
                metadata = envelope.get("metadata", {})
                for name in ("trace_id", "session_id"):
                    value = metadata.get(name)
                    if value:
                        headers.append((f"x-agenthack-{name.replace('_', '-')}".encode(), str(value).encode("ascii")))
                headers.append((b"x-agenthack-metadata", json.dumps(metadata, ensure_ascii=True, separators=(",", ":")).encode("ascii")))
                if start["status"] == 200 and envelope.get("status") == "success":
                    result = json.loads(envelope["output"]["answer"])
                    validate_wire_fields(case, result)
                    body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
                elif start["status"] == 200:
                    status = envelope.get("status")
                    start = dict(start, status=504 if status == "timeout" else 500)
                    body = json.dumps({"case_id": case.case_id, "detail": "Agent execution " + str(status),
                                       "error_type": envelope.get("output", {}).get("error", "execution_failed")},
                                      ensure_ascii=True).encode("utf-8")
            except (ValueError, KeyError, TypeError, UnicodeError):
                # SDK 成功但无法转成合法答卷时显式失败，不能泄漏包装体作为成功输出。
                return await JSONResponse({"detail": "invalid research answer from runtime"}, status_code=502)(scope, adapted_receive, send)
            headers = [(key, value) for key, value in headers if key.lower() != b"content-length"]
            await send(dict(start, headers=headers + [(b"content-length", str(len(body)).encode("ascii"))]))
            await send({"type": "http.response.body", "body": body, "more_body": False})

        with request_scope(RequestContext(task_id, session, received_at, execution_deadline)):
            return await self.app(scope, adapted_receive, adapted_send)


def install_research_protocol(runtime):
    """为原生 AgentRuntime 注册协议边界转换；实例及其 run()/路由保持原样。"""
    runtime.app.add_middleware(ResearchRequestAdapter,
                               timeout_supplier=lambda: runtime.agent.engine.settings.case_timeout)
