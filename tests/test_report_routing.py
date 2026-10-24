"""不同报告类型使用独立约束，估算与实际请求保持一致。"""
from unittest.mock import patch

from research_agent.integrations.llm import GatewayLLM
from research_agent.integrations.prompts import instructions_for


def test_survey_and_idea_have_separate_report_instructions():
    survey = instructions_for("report", {"task": {"function": "Survey"}})
    idea = instructions_for("report", {"task": {"function": "Idea"}})
    assert "本题是Survey" in survey and "本题是Idea" not in survey
    assert "本题是Idea" in idea and "初始建议" in idea
    assert "不附加Idea" in survey


def test_budget_estimate_uses_the_same_prompt_as_actual_request():
    g = GatewayLLM(token_budget=10000)
    g.base_url, g.api_key, g.model = "https://example.invalid", "test", "test"
    data = {"task": {"function": "Survey"}, "claims": []}
    captured = []

    def request(system, prompt, requested, remaining):
        captured.append(system + prompt)
        return {"usage": {"total_tokens": 30}, "choices": [{"finish_reason": "stop",
            "message": {"content": '{"markdown":"text","used_evidence_ids":[]}'}}]}

    capacity = g.output_capacity("report", data, reserve=8500, maximum=5000)
    with patch.object(g, "_request_once", request):
        g.json("report", data, max_tokens=capacity)
    assert capacity == min(5000, 1500 - g.estimate_tokens(captured[0]))
