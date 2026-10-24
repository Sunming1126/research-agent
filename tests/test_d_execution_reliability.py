"""用真实子进程与反例验证卡死检测、模型修复及取消清理。"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from research_agent.core.config import Settings
from research_agent.core.models import CaseContext
from research_agent.core.process import run_case_process
from research_agent.core.session import CaseSession
from research_agent.workflows.experiment_diagnostics import ExperimentProcessError, error_record
from research_agent.workflows.layered_d_runner import LayeredDRunner
from research_agent.workflows.sample_validation import PublicSampleError, run_public_sample
from research_agent.workflows.source_generation import extract_source


ROOT = Path("科研助手赛题材料/x新/research-experiments-20261020-source-v1")


def materials():
    return {key: (ROOT / "D-05" / name).read_text(encoding="utf-8") for key, name in (
        ("method", "method.md"), ("protocol", "protocol.json"), ("sample", "sample.json"))}


def offline_source(tmp_path):
    return LayeredDRunner(Settings(tmp_path, tmp_path / "out"), tmp_path / "fixture").prepare_implementation("D-05").read_text(encoding="utf-8")


def case():
    return CaseContext.from_dict({"case_id": "D-05", "function": "Reproduction", "title": "public",
        "input": "current public case", "materials": [], "constraints": [],
        "expected_output": "result", "output_schema_ref": "Reproduction"})


def idle_bug(source):
    # 只在离线测试夹具中注入错误，不修改模型响应或正式算法。
    return source.replace("def run_experiment(request):", "def run_experiment(request):\n"
        "    if any(j['arrival_ms'] == 5 for j in request['data']['jobs']):\n"
        "        while True:\n            pass\n")


def test_suite_detects_idle_gap_even_when_original_sample_passes(tmp_path):
    source = tmp_path / "bug.py"
    source.write_text(idle_bug(offline_source(tmp_path)), encoding="utf-8")
    started = time.monotonic()
    with pytest.raises(PublicSampleError) as error:
        run_public_sample(source, materials(), "D-05")
    assert error.value.feedback["probe"] == "idle_gap_and_switch"
    assert error.value.feedback["request"]["data"]["jobs"][1]["arrival_ms"] == 5
    assert time.monotonic() - started < 8


def test_repair_receives_failed_source_and_counterexample_and_keeps_both_versions(tmp_path):
    current = materials()
    root = tmp_path / "D-05"
    root.mkdir()
    for key, name in (("method", "method.md"), ("protocol", "protocol.json"), ("sample", "sample.json")):
        (root / name).write_text(current[key], encoding="utf-8")
    good = offline_source(tmp_path)
    sources = [idle_bug(good), good]

    class LLM:
        calls = 0

        def completion(self, stage, prompt, max_tokens):
            if self.calls:
                assert "previous_source" in prompt and "idle_gap_and_switch" in prompt
                assert "while True" in prompt and "arrival_ms" in prompt
            response = {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": sources[self.calls]}}]}
            self.calls += 1
            return response

    llm = LLM()
    runner = LayeredDRunner(Settings(tmp_path, tmp_path / "out"), tmp_path / "work", llm)
    final = runner.prepare_checked_implementation(case())
    assert llm.calls == 2 and final.read_text(encoding="utf-8") == good
    for attempt in (1, 2):
        response = json.loads((runner.work / f"source-attempt-{attempt}.json").read_text(encoding="utf-8"))
        assert (runner.work / f"implementation-attempt-{attempt}.py").read_text(encoding="utf-8") == extract_source(response)
    history = json.loads((runner.work / "public-selftest.json").read_text(encoding="utf-8"))["attempts"]
    assert [row["status"] for row in history] == ["failed", "passed"]
    assert history[1]["probe_count"] == 12
    assert {"retry_after_deadline_rejection", "retry_after_horizon_rejection",
            "retry_after_multiple_rejections"} <= {row["probe"] for row in history[1]["probes"]}


def test_cancel_kills_client_before_it_can_write_late_artifact(tmp_path):
    late = tmp_path / "late.txt"
    event = threading.Event()
    timer = threading.Timer(.25, event.set)
    timer.start()
    started = time.monotonic()
    try:
        with CaseSession("D-05", 10, cancelled=event).activate(), pytest.raises(TimeoutError, match="cancelled"):
            run_case_process([sys.executable, "-c", "import time,pathlib;time.sleep(1);pathlib.Path(__import__('sys').argv[1]).write_text('late')", str(late)],
                             timeout=5, env=os.environ.copy(), reserve=0)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 1
    time.sleep(1)
    assert not late.exists()


def test_client_uses_remaining_case_budget_and_preserves_timeout_output():
    with CaseSession("D-05", .6).activate():
        time.sleep(.2)
        started = time.monotonic()
        with pytest.raises((subprocess.TimeoutExpired, TimeoutError)) as error:
            run_case_process([sys.executable, "-c", "import time;print('started',flush=True);time.sleep(5)"],
                             timeout=5, env=os.environ.copy(), reserve=.05)
        assert time.monotonic() - started < .6
        assert "started" in error.value.stdout


@pytest.mark.parametrize("code,category", [("D_SOURCE_AUDIT_UNAVAILABLE", "platform_infrastructure"),
    ("D_SOURCE_NOT_GENERATED_THIS_SESSION", "source_admission")])
def test_platform_error_codes_are_preserved_without_changing_submission_schema(code, category):
    row = error_record(ExperimentProcessError(1, "RuntimeError: " + code), "formal_execution")
    assert row["code"] == code and row["category"] == category and row["returncode"] == 1
