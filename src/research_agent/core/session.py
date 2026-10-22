"""单题生命周期：统一截止时间、取消信号和阶段记录。"""
from __future__ import annotations

import contextvars
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field


_CURRENT = contextvars.ContextVar("research_case_session", default=None)


@dataclass
class CaseSession:
    case_id: str
    timeout: float
    cancelled: threading.Event = field(default_factory=threading.Event)
    started_at: float = field(default_factory=time.monotonic)
    stage: str = "received"
    stages: list[dict] = field(default_factory=list)

    @property
    def deadline(self):
        return self.started_at + self.timeout

    def remaining(self, reserve=0):
        self.checkpoint()
        value = self.deadline - time.monotonic() - reserve
        if value <= 0:
            raise TimeoutError("insufficient case time reserved for next stage")
        return value

    def checkpoint(self, stage=None):
        if self.cancelled.is_set():
            raise TimeoutError("case cancelled")
        if time.monotonic() >= self.deadline:
            raise TimeoutError("case deadline exceeded")
        if stage and stage != self.stage:
            self.stage = stage
            self.stages.append({"stage": stage, "elapsed_seconds": round(time.monotonic() - self.started_at, 3)})

    def snapshot(self):
        return {"case_id": self.case_id, "timeout_seconds": self.timeout,
                "elapsed_seconds": round(time.monotonic() - self.started_at, 3),
                "stage": self.stage, "stages": list(self.stages), "cancelled": self.cancelled.is_set()}

    @contextmanager
    def activate(self):
        token = _CURRENT.set(self)
        try:
            self.checkpoint()
            yield self
        finally:
            _CURRENT.reset(token)


def current_session():
    return _CURRENT.get()


def checkpoint(stage=None):
    session = current_session()
    if session:
        session.checkpoint(stage)


def remaining_timeout(cap, *, reserve=0):
    """子步骤只能使用本题剩余时间，不能重新获得一整份超时额度。"""
    session = current_session()
    return min(cap, session.remaining(reserve)) if session else cap
