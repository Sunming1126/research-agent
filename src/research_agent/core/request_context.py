"""HTTP 标识与全链路截止时间；asyncio.to_thread 会继承本题上下文。"""
import contextvars
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    task_id: str
    session_id: str | None
    received_at: float
    execution_deadline: float


_CURRENT = contextvars.ContextVar("research_http_request", default=None)


def current_request():
    return _CURRENT.get()


@contextmanager
def request_scope(value):
    token = _CURRENT.set(value)
    try:
        yield value
    finally:
        _CURRENT.reset(token)
