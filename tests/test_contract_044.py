"""本次零分的边界回归：真正的外层答卷、新正文限制和材料授权。"""
import os
import json
from unittest.mock import patch

import pytest

from research_agent.contracts.validation import report_citations, validate_report_limits
from research_agent.core.config import Settings
from research_agent.rag.materials import MaterialResolver, MaterialError


@pytest.mark.parametrize("function,limit", [("Survey", 4500), ("Idea", 6000)])
def test_report_length_counts_spaces_and_markdown_but_excludes_references(function, limit):
    validate_report_limits("字" * (limit - 2) + " \n## 参考文献\n" + "x" * 10000, function)
    with pytest.raises(ValueError, match="characters"):
        validate_report_limits("字" * (limit - 2) + "  \n## 参考文献", function)


def test_survey_composite_and_repeated_citations_count_as_instances():
    assert report_citations("[1,2] [2, 3] [5, p.17] [111]") == ["1", "2", "2", "3", "5"]
    validate_report_limits("[1,2]" * 20, "Survey")
    with pytest.raises(ValueError, match="40 citation"):
        validate_report_limits("[1,2]" * 20 + "[1]", "Survey")


def test_output_mounted_library_is_selected_without_searching_team_directories(tmp_path):
    library = tmp_path / "out/data/论文库"
    library.mkdir(parents=True)
    (library / "selected.pdf").write_bytes(b"%PDF locator fixture")
    with patch.dict(os.environ, {"RESEARCH_WORKSPACE_ROOT": str(tmp_path / "data"),
                               "RESEARCH_RESULT_ROOT": str(tmp_path / "out")}, clear=True):
        settings = Settings.from_env()
    assert settings.material_roots == [library]
    resolver = MaterialResolver(settings)
    assert resolver.resolve(["data/论文库/selected.pdf"])[0].path == library / "selected.pdf"
    assert resolver.resolve([str(library / "selected.pdf")])[0].filename == "selected.pdf"
    assert resolver.resolve(["本地论文文件夹内的PDF"])[0].filename == "selected.pdf"
    with pytest.raises(MaterialError):
        resolver.resolve([str(tmp_path / "outside.pdf")])


def test_two_default_libraries_require_explicit_selection(tmp_path):
    (tmp_path / "data/论文库").mkdir(parents=True)
    (tmp_path / "out/data/论文库").mkdir(parents=True)
    with patch.dict(os.environ, {"RESEARCH_WORKSPACE_ROOT": str(tmp_path / "data"),
                               "RESEARCH_RESULT_ROOT": str(tmp_path / "out")}, clear=True):
        with pytest.raises(ValueError, match="multiple paper libraries"):
            Settings.from_env()


def test_absolute_authorized_file_still_rejects_symlinks_and_traversal(tmp_path):
    root = tmp_path / "papers"
    root.mkdir()
    external = tmp_path / "secret.pdf"
    external.write_bytes(b"secret")
    resolver = MaterialResolver(Settings(tmp_path, tmp_path / "out", material_roots=[root]))
    with pytest.raises((ValueError, MaterialError)):
        resolver.resolve([str(root / "../secret.pdf")])
    try:
        (root / "linked.pdf").symlink_to(external)
    except OSError:
        pytest.skip("symlink unavailable")
    with pytest.raises(ValueError, match="symlinks"):
        resolver.resolve([str(root / "linked.pdf")])


def test_d_current_case_directory_reaches_runner_without_authorizing_other_cases(tmp_path):
    from fixtures import case, FakeLLM
    from research_agent.core.engine import ResearchAgentEngine
    from research_agent.workflows.experiments import failed_result
    from research_agent.workflows.source_generation import read_generation_materials
    for case_id in ("D-01", "D-02"):
        directory = tmp_path / "data/experiments" / case_id
        directory.mkdir(parents=True)
        from research_agent.workflows.experiments import parse_metric_specs
        protocol = json.dumps({'case_id': case_id, 'metrics': [dict(name=n,unit=u) for n,u in parse_metric_specs(case(case_id)['input'])]})
        for name, text in (("method.md", "Current case public method"), ("protocol.json", protocol), ("sample.json", "{}")):
            (directory / name).write_text(text, encoding="utf-8")
    settings = Settings(tmp_path / "data", tmp_path / "out", material_roots=[])
    engine = ResearchAgentEngine(settings, FakeLLM)
    invoked = []

    def run(runner, current, specs):
        invoked.append(current.case_id)
        assert read_generation_materials(current.case_id, runner.settings.material_roots)["method"]
        return failed_result(current.case_id, specs, "failed")

    with patch("research_agent.core.engine.ExperimentRunner.run", run):
        result = engine.solve_sync(case("D-01", ["data/experiments/D-01/method.md"]))
        assert result["status"] == "failed" and invoked == ["D-01"]
        result = engine.solve_sync(case("D-01", ["data/experiments/D-02/method.md"]))
        assert result["status"] == "materials_missing" and invoked == ["D-01"]
    assert settings.material_roots == []  # 逐题副本不能扩大后续请求的授权。


def test_survey_global_citation_limit_requests_a_shorter_report_instead_of_full_document_patches():
    from types import SimpleNamespace
    from research_agent.core.models import Evidence
    from research_agent.workflows.tasks import TaskHandlers
    stages, limits = [], []

    class LLM:
        reports = 0

        def json(self, stage, data, **kwargs):
            stages.append(stage)
            if stage == "report":
                self.reports += 1
                limits.append(data["output_limits"])
                body = "比较应保留原文条件，给定证据不足以支持额外因果结论。" * 40
                citations = "".join(f"[{i}, p.1]" for i in range(1, 6))
                return {"markdown": body + citations * (10 if self.reports == 1 else 1),
                        "used_evidence_ids": [f"ev-{i}" for i in range(1, 6)]}
            if stage == "audit":
                return {"ok": True, "issues": []}
            raise AssertionError("Global limits must not use paragraph patches: " + stage)

    handler = TaskHandlers(LLM(), "test")
    handler.ledger = [Evidence(f"ev-{i}", "论文比较给定条件下的状态。", f"p{i}.pdf", 1, f"c{i}",
                               "The study compares states under fixed conditions.") for i in range(1, 6)]
    result = handler.compose_report(SimpleNamespace(case_id="C-99", function="Survey", input="比较五篇论文"))
    validate_report_limits(result["markdown"], "Survey")
    assert stages == ["report", "report", "audit"]
    assert limits[0]["model_citation_instances"] == 35
    assert limits[0]["model_body_characters"] < 4500


def test_supplemental_report_extraction_preserves_time_for_writing_and_audit():
    from types import SimpleNamespace
    from research_agent.core.session import CaseSession
    from research_agent.rag.retrieval import QueryPlan
    from research_agent.workflows.tasks import TaskHandlers
    handler = TaskHandlers(object(), "test")

    class Retriever:
        def search(self, *args, **kwargs):
            raise AssertionError("No supplemental search when writing time is needed")

    with CaseSession("C-99", 120).activate():
        handler._recover_evidence_gaps(SimpleNamespace(function="Survey"), Retriever(), QueryPlan("comparison"))
