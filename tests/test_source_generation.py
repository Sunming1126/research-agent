import pytest

from research_agent.core.models import CaseContext
from research_agent.workflows.source_generation import (
    build_source_prompt,
    extract_source,
    save_source,
)


def _response(content, finish_reason="stop"):
    return {"choices": [{"finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": content}}]}


def test_extracts_one_complete_source_without_rewriting_code():
    source = "def run_experiment(request):\n    return {'ok': True}\n"
    assert extract_source(_response("```python\n" + source + "```")) == source


def test_rejects_truncated_or_multiple_model_versions():
    source = "def run_experiment(request):\n    return {}\n"
    with pytest.raises(ValueError):
        extract_source(_response(source, finish_reason="length"))
    other = "def run_experiment(request):\n    return {'other': True}\n"
    with pytest.raises(ValueError):
        extract_source(_response("```python\n" + source + "```\n```python\n" + other + "```"))


def test_saves_utf8_source_and_hash(tmp_path):
    source = "def run_experiment(request):\n    return {'中文': 1}\n"
    target = tmp_path / "implementation.py"
    digest = save_source(_response(source), target)
    assert target.read_text(encoding="utf-8") == source
    assert len(digest) == 64


def test_prompt_contains_current_case_materials(tmp_path):
    case = CaseContext.from_dict({
        "case_id": "D-01", "function": "Reproduction", "title": "source",
        "input": "current input", "materials": [], "constraints": [],
        "expected_output": "Reproduction", "output_schema_ref": "Reproduction",
    })
    prompt = build_source_prompt(case, {"method": "METHOD", "protocol": "PROTOCOL", "sample": "SAMPLE"})
    assert all(value in prompt for value in ("D-01", "current input", "METHOD", "PROTOCOL", "SAMPLE"))
