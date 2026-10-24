"""仅供离线回归的 HTTP 服务，不进入参赛镜像。"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from research_agent.core.engine import ResearchAgentEngine


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

