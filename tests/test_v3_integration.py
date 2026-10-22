from pathlib import Path

from research_agent.core.config import Settings
from research_agent.core.models import CaseContext
from research_agent.workflows.experiments import ExperimentRunner


def _d_case():
    return CaseContext.from_dict({
        "case_id": "D-01", "function": "Reproduction", "title": "v3",
        "input": "name=Macro-F1, unit=分数；name=Accuracy, unit=%；name=运行时间, unit=秒",
        "materials": [], "constraints": [], "expected_output": "v3",
        "output_schema_ref": "Reproduction",
    })


def test_platform_reproduction_routes_layered_d_runner(tmp_path, monkeypatch):
    called = {}

    class StubLayeredRunner:
        def __init__(self, settings, work, llm=None):
            called["work"] = work
            self.proof = {"case_id": "D-01"}

        def run(self, case, specs):
            called["specs"] = specs
            return {"case_id": case.case_id, "status": "materials_missing",
                    "metrics": [{"name": name, "unit": unit, "reference": None,
                                  "observed": None, "relative_error": None} for name, unit in specs],
                    "figures": []}

    monkeypatch.setattr("research_agent.workflows.layered_d_runner.LayeredDRunner", StubLayeredRunner)
    settings = Settings(tmp_path, tmp_path / "out", "team", experiment_protocol="layered-d-v3")
    runner = ExperimentRunner(settings, tmp_path / "out" / "work")
    result = runner.run(_d_case(), [("Macro-F1", "分数"), ("Accuracy", "%"), ("运行时间", "秒")])
    assert result["status"] == "materials_missing"
    assert called["specs"][0] == ("Macro-F1", "分数")

