import json
import os
from pathlib import Path

from research_agent.workflows.d_algorithms.d01 import run_d01
from research_agent.workflows.d_algorithms.d02 import run_d02
from research_agent.workflows.d_algorithms.d05 import run_d05


ROOT = Path(os.environ.get('PUBLIC_LAYERED_EXPERIMENT_ROOT',
            str(Path(__file__).resolve().parents[1] / '科研助手赛题材料/research-experiments-20261020-source-v1')))


def _sample(name):
    return json.loads((ROOT / name / "sample.json").read_text(encoding="utf-8"))


def test_d01_returns_reproducible_baseline_candidate_and_model_details():
    result = run_d01(_sample("D-01"))
    data = _sample("D-01")["data"]
    assert len(result["baseline"]) == len(data["texts"])
    assert len(result["candidate"]) == len(data["texts"])
    assert result["details"]["classes"] == sorted({row["label"] for row in data["train"]})
    assert len(result["details"]["weights"]) == len(result["details"]["classes"])


def test_d02_run_selects_config_and_rank_reuses_it():
    request = _sample("D-02")
    result = run_d02(request)
    assert set(result) == {"baseline", "candidate", "details"}
    selected = result["details"]["selected"]
    assert set(selected) == {"k1", "b", "title_weight"}
    assert len(result["candidate"]) == len(request["data"]["test_queries"])
    rank = run_d02({**request, "phase": "rank", "data": {**request["data"], "selected": selected}})
    assert set(rank) == {"candidate"}
    assert set(rank["candidate"]) == {row["id"] for row in request["data"]["test_queries"]}


def test_d05_conserves_jobs_and_applies_deadline_rules():
    request = {
        "case_id": "D-05",
        "phase": "run",
        "data": {
            "jobs": [
                {"id": "done", "flow": "A", "arrival_ms": 0, "service_ms": 2, "deadline_ms": 10},
                {"id": "expired", "flow": "B", "arrival_ms": 0, "service_ms": 4, "deadline_ms": 1},
            ],
            "flow_weights": {"A": 1, "B": 1},
            "servers": 1,
            "setup_ms": 0,
            "horizon_ms": 10,
        },
    }
    result = run_d05(request)
    for run in (result["baseline"], result["candidate"]):
        trace_ids = [row["id"] for row in run["trace"]]
        dropped_ids = [row["id"] for row in run["dropped"]]
        assert sorted(trace_ids + dropped_ids) == ["done", "expired"]
        assert len(set(trace_ids + dropped_ids)) == 2
        assert {row["id"] for row in run["dropped"]} == {"expired"}

