from __future__ import annotations

import json

from research_agent.core.engine import ResearchAgentEngine

try:
    from agenthack import AgentRuntime, BaseAgent
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


if __name__ == "__main__":
    if AgentRuntime is None:
        raise RuntimeError("Official AgentHack SDK is required to start the Agent service")
    from research_agent.integrations.http_runtime import install_research_protocol
    runtime = AgentRuntime(ResearchAgent())
    install_research_protocol(runtime)
    runtime.run()

