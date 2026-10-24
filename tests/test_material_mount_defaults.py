"""Image-only startup must find dedicated mounts without widening explicit scope."""
import json
from pathlib import Path

import pytest

from research_agent.core.config import Settings
from research_agent.rag.materials import MaterialError, MaterialResolver

def environment(monkeypatch, tmp_path):
    monkeypatch.delenv('RESEARCH_CONFIG', raising=False)
    monkeypatch.delenv('RESEARCH_MATERIAL_ROOTS', raising=False)
    monkeypatch.setenv('RESEARCH_WORKSPACE_ROOT', str(tmp_path))
    monkeypatch.setenv('RESEARCH_RESULT_ROOT', str(tmp_path / 'output'))
    papers=tmp_path/'papers';papers.mkdir()
    (papers/'assigned.pdf').write_bytes(b'public assigned fixture')
    (papers/'unassigned.pdf').write_bytes(b'public unassigned fixture')
    return papers

def test_no_custom_config_discovers_dedicated_mount_and_only_assigned_file(monkeypatch,tmp_path):
    papers=environment(monkeypatch,tmp_path)
    settings=Settings.from_env()
    assert papers.resolve() in settings.material_roots
    assert tmp_path.resolve() not in settings.material_roots
    refs=MaterialResolver(settings).resolve(['assigned.pdf'])
    assert [r.filename for r in refs]==['assigned.pdf']
    assert settings.material_aliases['本地论文文件夹内的 PDF']==['papers']

def test_explicit_empty_material_roots_does_not_fall_back_to_discovered_mounts(monkeypatch,tmp_path):
    environment(monkeypatch,tmp_path)
    config=tmp_path/'settings.json';config.write_text(json.dumps({'material_roots':[]}),encoding='utf-8')
    monkeypatch.setenv('RESEARCH_CONFIG',str(config))
    settings=Settings.from_env()
    assert settings.material_roots==[]
    with pytest.raises(MaterialError):MaterialResolver(settings).resolve(['assigned.pdf'])

def test_explicit_empty_environment_overrides_configured_material_roots(monkeypatch,tmp_path):
    papers=environment(monkeypatch,tmp_path)
    config=tmp_path/'settings.json';config.write_text(json.dumps({'material_roots':[str(papers)]}),encoding='utf-8')
    monkeypatch.setenv('RESEARCH_CONFIG',str(config))
    monkeypatch.setenv('RESEARCH_MATERIAL_ROOTS','')
    assert Settings.from_env().material_roots==[]
