from __future__ import annotations

from ..integrations.instrumentation import tool
from .dispatch_plan import DispatchPlan, load_dispatch_plan


@tool(name="read_dispatch")
def load_dispatch(settings):
    """读取当前 dispatch，返回已校验的单题 payload 列表。"""
    return list(load_dispatch_plan(settings).cases)

