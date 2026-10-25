"""组委会接入确认：目录标识、papers、派题与模型会话均使用各自的运行值。"""
import json
import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from research_agent.core.config import Settings
from research_agent.core.dispatch_plan import load_dispatch_plan, verify_current_assignment
from research_agent.core.models import CaseContext
from research_agent.integrations.llm import GatewayLLM
from research_agent.rag.materials import MaterialResolver
from test_dispatch_v3 import _write_v3_dispatch, _case


def test_official_papers_mount_and_explicit_papers_path(tmp_path):
    data = tmp_path / "data"
    for name in ("papers", "论文库"):
        (data / name).mkdir(parents=True)
        (data / name / "allowed.pdf").write_bytes(b"%PDF locator fixture")
    env = {"RESEARCH_WORKSPACE_ROOT": str(data), "RESEARCH_RESULT_ROOT": str(tmp_path / "output"),
           "RESEARCH_WORKSPACE_TEAM": "platform-dir", "TEAM_ID": "telemetry-id"}
    with patch.dict(os.environ, env, clear=True):
        settings = Settings.from_env()
        assert settings.team == "platform-dir"
        assert settings.material_roots == [data / "papers"]
        assert MaterialResolver(settings).resolve(["本地论文文件夹内的 PDF"])[0].path == data / "papers/allowed.pdf"
        assert MaterialResolver(settings).resolve(["data/papers/allowed.pdf"])[0].path == data / "papers/allowed.pdf"
        os.environ["PAPERS_PATH"] = str(data / "论文库")
        assert Settings.from_env().material_roots == [data / "论文库"]
        os.environ["RESEARCH_MATERIAL_ROOTS"] = ""
        assert Settings.from_env().material_roots == []


def test_single_reads_current_assignment_only_in_output(tmp_path):
    legacy = _write_v3_dispatch(tmp_path / "output")
    settings = replace(legacy, workspace=tmp_path / "data", output=tmp_path / "output", verify_assigned=True)
    assert load_dispatch_plan(settings).expected_count == 11
    # 删除其他题，只验证当前 B-01；Single 不得读取其他 assigned。
    for path in (settings.output / "data/team/assigned").glob("*.json"):
        if path.stem != "B-01":
            path.unlink()
    case = CaseContext.from_dict(_case("B-01"))
    verify_current_assignment(settings, case)
    with pytest.raises(ValueError, match="differs from assigned"):
        verify_current_assignment(settings, CaseContext.from_dict(dict(_case("B-01"), input="changed request")))
    assigned = settings.output / "data/team/assigned/B-01.json"
    assigned.write_text(assigned.read_text() + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_current_assignment(settings, case)


def test_challenge_gateway_values_override_compat_and_carry_session():
    env = {"CHALLENGE_LLM_BASE_URL": "http://gateway.invalid/session-route/v1",
           "CHALLENGE_LLM_API_KEY": "test-runtime-key", "CHALLENGE_LLM_MODEL": "runtime-model",
           "CHALLENGE_SESSION_ID": "challenge-session", "TEAM_ID": "not-a-model-session",
           "PLATFORM_LLM_URL": "http://wrong.invalid/v1", "PLATFORM_API_KEY": "wrong-key",
           "PLATFORM_LLM_MODEL": "wrong-model"}
    seen = {}

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.chat = SimpleNamespace(completions=self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **kwargs):
            seen["payload"] = kwargs
            return SimpleNamespace(model_dump=lambda: {"choices": []})

    with patch.dict(os.environ, env, clear=True), patch("openai.OpenAI", Client):
        gateway = GatewayLLM()
        gateway._request_once("system", "prompt", 10, 20)
    assert seen["base_url"] == env["CHALLENGE_LLM_BASE_URL"]
    assert seen["api_key"] == env["CHALLENGE_LLM_API_KEY"]
    assert seen["payload"]["model"] == "runtime-model"
    assert seen["default_headers"] == {"X-Session-ID": "challenge-session"}
    assert "challenge-session" not in repr(gateway.usage)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{}'

    with patch("urllib.request.urlopen", return_value=Response()) as request:
        gateway._request_stdlib({"model": "runtime-model"}, 20)
    sent = request.call_args.args[0]
    assert sent.full_url == env["CHALLENGE_LLM_BASE_URL"] + "/chat/completions"
    assert dict((k.lower(), v) for k, v in sent.header_items())["x-session-id"] == "challenge-session"


@pytest.mark.parametrize('description', [
    '本地论文库中的 PDF', '本地论文库内的ＰＤＦ', '本地论文文件夹内的所有 PDF',
    'data/论文库', '/data/论文库/', '论文库', 'data/papers', '/data/papers',
])
def test_whole_library_scope_maps_only_to_dedicated_mount(tmp_path, description):
    library = tmp_path / 'data/papers'
    library.mkdir(parents=True)
    (library / '研究.PDF').write_bytes(b'%PDF fixture')
    with patch.dict(os.environ, {'RESEARCH_WORKSPACE_ROOT': str(tmp_path / 'data'),
                                'RESEARCH_RESULT_ROOT': str(tmp_path / 'output')}, clear=True):
        settings = Settings.from_env()
    refs = MaterialResolver(settings).resolve([description])
    assert [ref.filename for ref in refs] == ['研究.PDF']
    assert MaterialResolver(settings).resolve(['data/论文库/研究.PDF'])[0].path == library / '研究.PDF'
    for unsafe in ['data/论文库/../private.pdf', '本地论文库中指定的电池 PDF', '/etc/passwd']:
        with pytest.raises(ValueError):
            MaterialResolver(settings).resolve([unsafe])
