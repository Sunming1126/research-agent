import hashlib
import json
from pathlib import Path

import pytest

from research_agent.core.dispatch import load_dispatch
from research_agent.core.models import CaseContext
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine


def _case(case_id):
    prefix = case_id[0]
    function = {
        "A": "Chat", "B": "Literature_Retrieval", "C": "Survey",
        "D": "Reproduction", "E": "Idea",
    }[prefix]
    return {
        "case_id": case_id,
        "function": function,
        "title": "v3 test",
        "input": "test input",
        "materials": [],
        "constraints": [],
        "expected_output": "test output",
        "output_schema_ref": function,
    }


def _write_v3_dispatch(root: Path):
    team = "team"
    assigned = root / "data" / team / "assigned"
    assigned.mkdir(parents=True)
    ids = ["A-100", "B-01", "B-02", "B-03", "C-01", "C-03",
           "D-01", "D-02", "D-05", "E-01", "E-02"]
    cases = []
    for case_id in ids:
        path = assigned / f"{case_id}.json"
        path.write_text(json.dumps(_case(case_id), ensure_ascii=False), encoding="utf-8")
        cases.append({
            "case_id": case_id,
            "function": _case(case_id)["function"],
            "title": "v3 test",
            "question_file": f"data/{team}/assigned/{case_id}.json",
            "question_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            **({"difficulty": {"D-01": "easy", "D-02": "medium", "D-05": "hard"}[case_id]} if case_id.startswith("D-") else {}),
        })
    payload = {
        "dispatch_id": "v3-test",
        "team": team,
        "schema_version": 3,
        "selection_mode": "random_a_fixed_bcde",
        "dispatch_policy_version": "research-assistant-dispatch-v3",
        "cases_per_category": {"A": 1, "B": 3, "C": 2, "D": 3, "E": 2},
        "rules_version": "research-assistant-objective-v3",
        "case_ids": ids,
        "cases": cases,
    }
    (root / "data" / team / "dispatch.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return Settings(workspace=root, output=root / "output", team=team)


def test_load_dispatch_v3_accepts_11_cases(tmp_path):
    settings = _write_v3_dispatch(tmp_path)
    cases = load_dispatch(settings)
    assert len(cases) == 11
    assert cases[0]["case_id"] == "A-100"


def test_load_dispatch_v3_accepts_three_digit_case_id(tmp_path):
    settings = _write_v3_dispatch(tmp_path)
    cases = load_dispatch(settings)
    assert CaseContext.from_dict(cases[0]).case_id == "A-100"


def test_run_dispatch_uses_runtime_case_count(tmp_path, monkeypatch):
    settings = _write_v3_dispatch(tmp_path)
    engine = ResearchAgentEngine(settings, llm_factory=lambda: None)
    monkeypatch.setattr(engine, "_solve_one", lambda payload, *args: {"case_id": payload["case_id"]})
    summary = engine.run_dispatch()
    assert summary["complete"] is True
    assert len(summary["completed"]) == 11

