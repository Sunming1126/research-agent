"""在自定义工具边界接入官方遥测。"""
import os

try:
    if os.environ.get("RESEARCH_RUNTIME") == "local":
        raise ModuleNotFoundError(name="agenthack")
    from agenthack import tool
except ModuleNotFoundError as exc:
    if exc.name != "agenthack":
        raise

    def tool(*, name):
        def decorate(function):
            return function
        return decorate

