import json
from pathlib import Path

import pytest

from research_agent.workflows.layered_d_runner import (
    build_layered_command,
    normalize_layered_submission,
    LayeredDRunner,
)
from research_agent.core.config import Settings
from research_agent.core.models import CaseContext


def test_layered_command_contains_case_and_implementation(tmp_path):
    command = build_layered_command("python", Path("/data/experiments/run_experiment.py"), "D-02", tmp_path / "implementation.py")
    assert command[-4:] == ["--case", "D-02", "--implementation", str(tmp_path / "implementation.py")]


def test_complete_rpc_status_is_normalized_to_completed():
    submission = {"case_id": "D-01", "status": "completed", "metrics": [], "figures": []}
    assert normalize_layered_submission({"status": "complete", "case_id": "D-01", "submission": submission}, "D-01") == submission


def test_direct_final_submission_from_client_is_accepted():
    submission = {"case_id": "D-01", "status": "completed", "metrics": [], "figures": []}
    assert normalize_layered_submission(submission, "D-01") == submission


def test_prepare_implementation_is_case_scoped(tmp_path):
    settings = Settings(workspace=tmp_path, output=tmp_path / "out", team="team")
    runner = LayeredDRunner(settings, tmp_path / "out" / "work")
    path = runner.prepare_implementation("D-05")
    assert path.parent == (tmp_path / "out" / "work")
    assert "def run_experiment(request)" in path.read_text(encoding="utf-8")


def test_prepare_generated_implementation_uses_gateway_response_verbatim(tmp_path):
    case_root = tmp_path / "D-01"
    case_root.mkdir()
    (case_root / "method.md").write_text("method", encoding="utf-8")
    (case_root / "protocol.json").write_text("{}", encoding="utf-8")
    (case_root / "sample.json").write_text("{}", encoding="utf-8")
    source = "def run_experiment(request):\n    return {'from_gateway': True}\n"

    class FakeLLM:
        def completion(self, stage, prompt, max_tokens):
            assert stage == "d_source"
            assert "method" in prompt and "protocol" in prompt and "sample" in prompt
            return {"choices": [{"finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "```python\n" + source + "```"}}]}

    settings = Settings(workspace=tmp_path, output=tmp_path / "out", team="team")
    runner = LayeredDRunner(settings, tmp_path / "out" / "work", llm=FakeLLM())
    path = runner.prepare_generated_implementation(_d_case())
    assert path.read_text(encoding="utf-8") == source
    assert (path.parent / "generation-response.json").is_file()


def _d_case():
    return CaseContext.from_dict({
        "case_id": "D-01", "function": "Reproduction", "title": "source",
        "input": "name=Macro-F1, unit=分数；name=Accuracy, unit=%；name=运行时间, unit=秒",
        "materials": [], "constraints": [], "expected_output": "v3",
        "output_schema_ref": "Reproduction",
    })

