"""官方专用论文库默认配置、显式覆盖和授权边界。"""
import json
import os
from unittest.mock import patch

import pytest

from research_agent.core.config import Settings
from research_agent.rag.materials import MaterialError, MaterialResolver


def load(workspace, **env):
    with patch.dict(os.environ, {"RESEARCH_WORKSPACE_ROOT": str(workspace),
                                "RESEARCH_RESULT_ROOT": str(workspace / "out"), **env}, clear=True):
        return Settings.from_env()


@pytest.mark.parametrize("data_root", [False, True])
def test_standard_library_and_public_description_work_without_custom_config(tmp_path, data_root):
    library = tmp_path / "data/论文库"
    library.mkdir(parents=True)
    (library / "allowed.pdf").write_bytes(b"%PDF synthetic locator fixture")
    (tmp_path / "private.pdf").write_bytes(b"do not read")
    settings = load(tmp_path / "data" if data_root else tmp_path, TEAM_ID="telemetry-only", RESEARCH_WORKSPACE_TEAM="platform-team")
    assert settings.team == "platform-team"
    assert settings.material_roots == [library]
    resolver = MaterialResolver(settings)
    assert resolver.resolve(["本地论文文件夹内的 PDF"])[0].filename == "allowed.pdf"
    assert resolver.resolve(["data/论文库/allowed.pdf"])[0].path == library / "allowed.pdf"
    with pytest.raises(MaterialError):
        resolver.resolve(["private.pdf"])


def test_explicit_config_overrides_library_team_and_aliases(tmp_path):
    custom = tmp_path / "custom-papers"
    custom.mkdir()
    (custom / "selected.pdf").write_bytes(b"%PDF fixture")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"team": "configured-team", "material_roots": [str(custom)],
                                 "material_aliases": {"本地论文文件夹内的 PDF": ["selected.pdf"]}}), encoding="utf-8")
    settings = load(tmp_path, TEAM_ID="platform-team", RESEARCH_CONFIG=str(config))
    assert settings.team == "configured-team" and settings.material_roots == [custom]
    assert [item.filename for item in MaterialResolver(settings).resolve(["本地论文文件夹内的 PDF"])] == ["selected.pdf"]


def test_environment_overrides_config_and_empty_roots_are_respected(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"team": "configured", "material_roots": [str(tmp_path / "configured")]}), encoding="utf-8")
    settings = load(tmp_path, TEAM_ID="platform", RESEARCH_WORKSPACE_TEAM="explicit",
                    RESEARCH_CONFIG=str(config), RESEARCH_MATERIAL_ROOTS=str(tmp_path / "env-papers"))
    assert settings.team == "explicit" and settings.material_roots == [tmp_path / "env-papers"]
    empty = load(tmp_path, RESEARCH_MATERIAL_ROOTS="")
    assert empty.material_roots == [] and not empty.material_aliases
    config.write_text('{"material_roots": []}', encoding="utf-8")
    assert load(tmp_path, RESEARCH_CONFIG=str(config)).material_roots == []


def test_default_library_does_not_infer_survey_specific_material_descriptions(tmp_path):
    settings = load(tmp_path)
    with pytest.raises(MaterialError):
        MaterialResolver(settings).resolve(["本地论文文件夹内指定的电池回收 PDF"])


def test_missing_material_is_an_error_instead_of_an_empty_result(tmp_path):
    with pytest.raises(MaterialError, match="authorized material not found"):
        MaterialResolver(load(tmp_path)).resolve(["missing.pdf"])


def test_workspace_team_is_not_a_real_team_name_or_telemetry_id(tmp_path):
    assert load(tmp_path, TEAM_ID="telemetry-uuid").team == "workspace_team"
    assert load(tmp_path, RESEARCH_WORKSPACE_TEAM="", TEAM_NAME="local-dir").team == "local-dir"
    assert load(tmp_path).team == "workspace_team"
    settings = load(tmp_path, RESEARCH_WORKSPACE_TEAM="platform-dir", TEAM_NAME="local-dir", TEAM_ID="telemetry-uuid")
    assert settings.team == "platform-dir" and settings.verify_assigned



def test_default_library_rejects_symlink_escape(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    external = tmp_path / "outside"
    external.mkdir()
    try:
        (data / "论文库").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="symlinks"):
        load(tmp_path)
