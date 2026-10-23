from __future__ import annotations

import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from research_agent.core.engine import ResearchAgentEngine

try:
    from agenthack import AgentRuntime, BaseAgent
    from agenthack.runtime import TaskRequest
    from fastapi import HTTPException, Request
    from pydantic import ValidationError
except ModuleNotFoundError as exc:
    if exc.name != "agenthack":
        raise
    AgentRuntime, BaseAgent = None, object


class ResearchAgent(BaseAgent):
    def __init__(self, engine=None):
        self.engine = engine or ResearchAgentEngine()

    async def solve(self, task_input):
        result = await self.engine.solve(task_input)
        # SDK 会将普通字典转成 Python 表示形式；把赛题 JSON 放入 answer 才能保持合法 JSON。
        return {"answer": json.dumps(result, ensure_ascii=False, allow_nan=False)}


def create_runtime(agent=None, *, telemetry=None):
    """Expose the contest's direct case contract through the official SDK.

    Only adapt the HTTP boundary: the SDK still owns execution, cancellation,
    trace propagation, model interception, telemetry flushing and shutdown.
    Older SDK envelope clients keep their original response contract.
    """
    from research_agent.core.models import CaseContext

    agent = agent or ResearchAgent()
    runtime = AgentRuntime(agent, telemetry=telemetry)
    route = next(r for r in runtime.app.routes
                 if getattr(r, "path", None) == "/solve" and "POST" in getattr(r, "methods", ()))
    sdk_solve = route.endpoint
    runtime.app.router.routes.remove(route)

    @runtime.app.post("/solve")
    async def solve(http_request: Request):
        try:
            payload = await http_request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(422, "request must be a JSON object") from None
        if not isinstance(payload, dict):
            raise HTTPException(422, "request must be a JSON object")
        wrapped = "task_id" in payload and isinstance(payload.get("input"), dict)
        try:
            if wrapped:
                request = TaskRequest.model_validate(payload)
            else:
                case = CaseContext.from_dict(payload)
                request = TaskRequest(
                    task_id=case.case_id, task_type=case.function, input=case.to_dict(),
                    constraints={"timeout_ms": 180000},
                    session_id=os.environ.get("CHALLENGE_SESSION_ID") or None)
        except (ValueError, TypeError, ValidationError):
            # Avoid echoing request contents or credentials in validation errors.
            raise HTTPException(422, "expected an eight-field research case or SDK task envelope") from None
        response = await sdk_solve(request, http_request)
        if wrapped:
            return response
        if response.status != "success":
            raise HTTPException(504 if response.status == "timeout" else 500,
                                "case timed out" if response.status == "timeout" else "case execution failed")
        try:
            result = json.loads(response.output["answer"])
            if not isinstance(result, dict) or result.get("case_id") != case.case_id:
                raise ValueError("invalid case response")
        except (KeyError, ValueError, TypeError):
            raise HTTPException(500, "case execution returned an invalid result") from None
        return result

    return runtime


def serve_local(engine=None, host="127.0.0.1", port=8080):
    engine = engine or ResearchAgentEngine()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health":
                self.send_error(404)
                return
            self.send_json(200, {"status": "healthy", "runtime": "local-development"})

        def do_POST(self):
            if self.path != "/solve":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 2_000_000:
                    raise ValueError("invalid request size")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if "task_id" in payload and isinstance(payload.get("input"), dict):
                    result = engine.solve_sync(payload["input"])
                    result = {"task_id": payload["task_id"], "status": "success",
                              "output": {"answer": json.dumps(result, ensure_ascii=False)}}
                else:
                    result = engine.solve_sync(payload)
                self.send_json(200, result)
            except Exception as exc:
                self.send_json(400, {"error": type(exc).__name__, "message": str(exc)[:500]})

        def send_json(self, status, value):
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    if AgentRuntime is not None:
        create_runtime().run()
    else:
        serve_local(host=os.environ.get("AGENT_BIND", "127.0.0.1"),
                    port=int(os.environ.get("PORT", "8080"))).serve_forever()

