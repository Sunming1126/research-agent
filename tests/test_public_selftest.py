"""公开自测执行真实独立进程，并验证修复必须来自第二次完整模型回复。"""
import json
from pathlib import Path

import pytest

from research_agent.core.config import Settings
from research_agent.core.models import CaseContext
from research_agent.workflows.layered_d_runner import LayeredDRunner
from research_agent.workflows.sample_validation import run_public_sample
from research_agent.workflows.sample_worker import validate_source


ROOT = Path("科研助手赛题材料/research-experiments-20261020-source-v1")


@pytest.mark.parametrize("case_id", ["D-01", "D-02", "D-05"])
def test_public_samples_run_in_separate_process(tmp_path, case_id):
    settings = Settings(workspace=tmp_path, output=tmp_path / "out")
    path = LayeredDRunner(settings, tmp_path / "work").prepare_implementation(case_id)
    materials = {key: (ROOT / case_id / name).read_text(encoding="utf-8")
                 for key, name in (("protocol", "protocol.json"), ("sample", "sample.json"))}
    report = run_public_sample(path, materials, case_id)
    assert report["status"] == "passed" and report["candidate_present"]


@pytest.mark.parametrize("source", ["import socket", "import os", "x = open('secret')",
    "x = (1).__class__", "import math\nx = math.__dict__"])
def test_selftest_rejects_runtime_and_io_access(source):
    with pytest.raises(ValueError):
        validate_source(source)


def test_model_repairs_complete_source_after_sample_failure(tmp_path):
    case_root = tmp_path / "D-01"
    case_root.mkdir()
    for name, text in {"method.md": "public method", "protocol.json": "{}", "sample.json": "{}"}.items():
        (case_root / name).write_text(text, encoding="utf-8")
    sources = ["def run_experiment(request):\n    return {}\n",
               "def run_experiment(request):\n    return {'repaired': True}\n"]

    class LLM:
        calls = 0

        def completion(self, stage, prompt, max_tokens):
            if self.calls:
                assert "missing baseline" in prompt
            content = sources[self.calls]
            self.calls += 1
            return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}]}

    def tester(path, materials, case_id):
        if "repaired" not in path.read_text(encoding="utf-8"):
            raise ValueError("missing baseline")
        return {"status": "passed"}

    case = CaseContext.from_dict({"case_id": "D-01", "function": "Reproduction", "title": "public",
        "input": "current case", "materials": [], "constraints": [], "expected_output": "result",
        "output_schema_ref": "Reproduction"})
    runner = LayeredDRunner(Settings(tmp_path, tmp_path / "out"), tmp_path / "work", llm=LLM())
    final = runner.prepare_checked_implementation(case, tester=tester)
    assert final.read_text(encoding="utf-8") == sources[1]
    history = json.loads((tmp_path / "work/public-selftest.json").read_text(encoding="utf-8"))["attempts"]
    assert [r["status"] for r in history] == ["failed", "passed"]
    assert (tmp_path / "work/source-attempt-1.json").exists()
    assert (tmp_path / "work/source-attempt-2.json").exists()
