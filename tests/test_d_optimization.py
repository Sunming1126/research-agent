"""D提分回归：错误模型、错误统计和表面守恒的错误调度不能通过自检。"""
import copy
import json
import time
from pathlib import Path

import pytest

from research_agent.core.config import Settings
from research_agent.core.models import CaseContext
from research_agent.core.session import CaseSession
from research_agent.workflows.d_algorithms.d01 import run_d01
from research_agent.workflows.d_algorithms.d02 import run_d02
from research_agent.workflows.d_algorithms.d05 import run_d05
from research_agent.workflows.d_public_cases import derived_public_cases
from research_agent.workflows.d_public_metrics import validate_search_metrics, validate_ranking_order
from research_agent.workflows.layered_d_runner import LayeredDRunner
from research_agent.workflows.sample_validation import validate_sample_output
from research_agent.workflows.source_generation import _retrieval_prompt_materials

ROOT = Path("科研助手赛题材料/research-experiments-20261020-source-v1")


def sample(case_id):
    result = json.loads((ROOT / case_id / "sample.json").read_text(encoding="utf-8"))
    result["protocol"] = json.loads((ROOT / case_id / "protocol.json").read_text(encoding="utf-8"))
    return result


@pytest.mark.parametrize("case_id,algorithm", [("D-01", run_d01), ("D-02", run_d02), ("D-05", run_d05)])
def test_public_and_derived_conditions_accept_valid_algorithms(case_id, algorithm):
    request = sample(case_id)
    for label, case in [("original", request), *derived_public_cases(case_id, request)]:
        assert validate_sample_output(case_id, case, algorithm(case))["candidate_present"], label


def test_d01_rejects_consistent_predictions_from_wrong_idf():
    request = sample("D-01")
    result = run_d01(request)
    result["details"]["idf"] = [x * 2 for x in result["details"]["idf"]]
    # 整体缩放IDF后L2向量不变，仅检查预测一致性无法发现训练公式错误。
    with pytest.raises(ValueError, match="IDF"):
        validate_sample_output("D-01", request, result)


def test_d01_rejects_test_vocabulary_leakage():
    request = sample("D-01")
    result = run_d01(request)
    result["details"]["vocabulary"].append("zzzztestonly")
    result["details"]["idf"].append(1)
    for weights in result["details"]["weights"]:
        weights.insert(-1, 0)
    with pytest.raises(ValueError, match="training texts"):
        validate_sample_output("D-01", request, result)


def test_d02_group_macro_is_not_query_average():
    queries = [dict(id="q1", relevant=["a"], group="small"),
               dict(id="q2", relevant=["b"], group="large"),
               dict(id="q3", relevant=["b"], group="large")]
    row = {"rankings": {q["id"]: [{"id": "a", "score": 2}, {"id": "b", "score": 1}] for q in queries},
           "recall_at_5_percent": 100, "mrr_at_10": 2 / 3,
           "group_metrics": {"groups": {"small": {"recall_at_5_percent": 100, "mrr_at_10": 1},
                                        "large": {"recall_at_5_percent": 100, "mrr_at_10": .5}},
                             "worst_mrr_at_10": .5, "macro_mrr_at_10": .75, "macro_recall_at_5_percent": 100}}
    validate_search_metrics(row, queries, {"a", "b"})
    row["group_metrics"]["macro_mrr_at_10"] = 2 / 3
    with pytest.raises(ValueError, match="macro MRR"):
        validate_search_metrics(row, queries, {"a", "b"})


def test_d02_tolerates_numeric_ties_but_rejects_wrong_order():
    validate_ranking_order([{"id": "a", "score": 1}, {"id": "b", "score": 1 + 1e-13}])
    with pytest.raises(ValueError, match="descending"):
        validate_ranking_order([{"id": "a", "score": 1}, {"id": "b", "score": 1.1}])


def test_d02_rejects_query_independent_scores_even_if_sorted_and_metrics_consistent():
    request = sample("D-02")
    result = run_d02(request)
    for query in request["data"]["test_queries"]:
        query["text"] = "UNKNOWNZZ9981"
    with pytest.raises(ValueError, match="QUERY terms"):
        validate_sample_output("D-02", request, result)


def test_d02_accepts_positive_global_score_scaling():
    request = sample("D-02")
    result = run_d02(request)
    for key in ("baseline", "candidate"):
        for ranking in result[key].values():
            for row in ranking:
                row["score"] *= 7
    validate_sample_output("D-02", request, result)


def test_d05_fifo_cannot_pass_as_fair_candidate():
    request = dict(derived_public_cases("D-05", sample("D-05")))["three_servers_reserved_service"]
    result = run_d05(request)
    result["candidate"] = copy.deepcopy(result["baseline"])
    with pytest.raises(ValueError, match="reserved service"):
        validate_sample_output("D-05", request, result)


def test_d05_after_window_expiry_is_horizon_drop():
    request = dict(derived_public_cases("D-05", sample("D-05")))["idle_setup_rejection_retry_window"]
    result = run_d05(request)
    for value in (result["baseline"], result["candidate"]):
        assert {row["id"]: row["reason"] for row in value["dropped"]}["late"] == "horizon"
    next(row for row in result["candidate"]["dropped"] if row["id"] == "late")["reason"] = "deadline"
    with pytest.raises(ValueError, match="unarrived"):
        validate_sample_output("D-05", request, result)


def test_first_model_call_requests_candidate_and_preserves_complete_response(tmp_path):
    root = tmp_path / "D-01"
    root.mkdir()
    for name in ("method.md", "protocol.json", "sample.json"):
        (root / name).write_bytes((ROOT / "D-01" / name).read_bytes())
    source = "def run_experiment(request):\n    return {}\n"
    response = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": source}}]}
    class Model:
        calls = 0
        def completion(self, stage, prompt, max_tokens):
            self.calls += 1
            assert "candidate必须为null" not in prompt
            assert "首次就完成baseline、candidate" in prompt
            return response
    model = Model()
    runner = LayeredDRunner(Settings(tmp_path, tmp_path / "out"), tmp_path / "work", model)
    case = CaseContext.from_dict(dict(case_id="D-01", function="Reproduction", title="public", input="public",
                                      materials=[], constraints=[], expected_output="result", output_schema_ref="Reproduction"))
    with CaseSession("D-01", 170).activate():
        result = runner.prepare_checked_implementation(case, tester=lambda *args: {"status": "passed", "candidate_present": True})
    assert model.calls == 1 and result.read_text() == source
    assert (runner.work / "source-attempt-1.py").read_text() == source
    assert json.loads((runner.work / "generation-response.json").read_text()) == response


def test_compact_retrieval_prompt_keeps_grid_schema_and_valid_example_relevance():
    protocol = (ROOT / "D-02/protocol.json").read_text(encoding="utf-8")
    sample_text = (ROOT / "D-02/sample.json").read_text(encoding="utf-8")
    projected, example = _retrieval_prompt_materials(protocol, sample_text)
    parsed = json.loads(projected)
    for key in ("grid", "default_config", "response_schema", "equivalence", "phase_values"):
        assert parsed[key] == json.loads(protocol)[key]
    data = json.loads(example)["data"]
    ids = {d["id"] for d in data["documents"]}
    assert all(set(q["relevant"]) <= ids for q in data["dev_queries"])
    assert len(example) < len(sample_text)


def test_d02_generates_verified_baseline_before_candidate_upgrade(tmp_path):
    root = tmp_path / 'D-02'
    root.mkdir()
    for name in ('method.md', 'protocol.json', 'sample.json'):
        (root / name).write_bytes((ROOT / 'D-02' / name).read_bytes())
    class Model:
        calls = 0
        def completion(self, stage, prompt, max_tokens):
            baseline = '本次先完成可执行基线' in prompt
            assert baseline == (self.calls == 0)
            assert max_tokens == (1800 if baseline else 3500)
            self.calls += 1
            source = 'def run_experiment(request):\n    return ' + repr({'attempt': self.calls}) + '\n'
            return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': source}}]}
    def tester(path, *args):
        return {'status': 'passed', 'candidate_present': "'attempt': 2" in path.read_text()}
    case = CaseContext.from_dict(dict(case_id='D-02', function='Reproduction', title='public', input='public',
                                      materials=[], constraints=[], expected_output='result', output_schema_ref='Reproduction'))
    model = Model()
    runner = LayeredDRunner(Settings(tmp_path, tmp_path / 'out'), tmp_path / 'work', model)
    with CaseSession('D-02', 170).activate():
        result = runner.prepare_checked_implementation(case, tester=tester)
    assert model.calls == 2 and "'attempt': 2" in result.read_text()


@pytest.mark.parametrize("case_id,cap", [("D-01", 100), ("D-02", 125), ("D-05", 100)])
def test_generation_retains_formal_budget_and_restores_transport_controls(tmp_path, case_id, cap):
    root = tmp_path / case_id
    root.mkdir()
    for name in ("method.md", "protocol.json", "sample.json"):
        (root / name).write_bytes((ROOT / case_id / name).read_bytes())
    class Model:
        deadline = time.monotonic() + 300
        REQUEST_TIMEOUT_CAP = 60
        RETRY_ATTEMPTS = 2
        thinking_override = "enabled"
        def completion(self, *args, **kwargs):
            assert self.REQUEST_TIMEOUT_CAP == cap
            assert self.deadline <= session.deadline - 40
            assert self.RETRY_ATTEMPTS == 1
            return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant",
                         "content": "def run_experiment(request):\n    return {}\n"}}]}
    model = Model()
    original = model.deadline
    case = CaseContext.from_dict(dict(case_id=case_id, function="Reproduction", title="public", input="public",
                                      materials=[], constraints=[], expected_output="result", output_schema_ref="Reproduction"))
    runner = LayeredDRunner(Settings(tmp_path, tmp_path / "out"), tmp_path / "work", model)
    with CaseSession(case_id, 170).activate() as session:
        runner.prepare_generated_implementation(case)
    assert model.deadline == original and model.REQUEST_TIMEOUT_CAP == 60
    assert model.RETRY_ATTEMPTS == 2 and model.thinking_override == "enabled"
