from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..core.utils import confined, sha256_file, write_json, atomic_text
from ..core.session import checkpoint, current_session, remaining_timeout
from .source_generation import build_source_prompt, read_generation_materials, save_generation_response, save_source
from .sample_validation import run_public_sample


CASE_MODULES = {"D-01": "d01.py", "D-02": "d02.py", "D-05": "d05.py"}


def build_layered_command(python: str, script: Path, case_id: str, implementation: Path) -> list[str]:
    """构造新版 runner 命令，明确传入本队可写源码。"""
    if case_id not in CASE_MODULES:
        raise ValueError("not a layered-d-v3 case")
    return [python, str(script), "--case", case_id, "--implementation", str(implementation)]


def normalize_layered_submission(response: dict, case_id: str) -> dict:
    """把 RPC complete 和答卷 completed 分层处理，最终只返回旧 Schema 字段。"""
    if response.get("case_id") != case_id:
        raise RuntimeError("layered experiment case mismatch")
    # 官方 client 会把 RPC 包装解开后直接打印 submission；测试夹具可能保留包装。
    if response.get("status") == "complete" and "submission" in response:
        submission = response.get("submission")
    elif response.get("status") in {"completed", "failed", "materials_missing"}:
        submission = response
    else:
        raise RuntimeError("layered experiment did not complete")
    if not isinstance(submission, dict) or submission.get("case_id") != case_id:
        raise ValueError("layered submission identity mismatch")
    normalized = dict(submission)
    if normalized.get("status") == "complete":
        normalized["status"] = "completed"
    if normalized.get("status") not in {"completed", "failed", "materials_missing"}:
        raise ValueError("unsupported layered submission status")
    return normalized


class LayeredDRunner:
    """调用官方 layered-d-v3 客户端并保留当前运行证据。"""

    def __init__(self, settings, work, llm=None):
        self.settings = settings
        self.work = work
        self.llm = llm
        self.proof = None

    def prepare_implementation(self, case_id: str) -> Path:
        """把本队算法源码复制为平台要求的单文件实现。"""
        filename = CASE_MODULES.get(case_id)
        if filename is None:
            raise ValueError("not a layered-d-v3 case")
        source = Path(__file__).resolve().parent / "d_algorithms" / filename
        if not source.is_file():
            raise FileNotFoundError(str(source))
        self.work.mkdir(parents=True, exist_ok=True)
        target = confined(self.work, "implementation.py")
        algorithm = source.read_text(encoding="utf-8")
        function = {
            "D-01": "run_d01",
            "D-02": "run_d02",
            "D-05": "run_d05",
        }[case_id]
        wrapper = algorithm + f"\n\ndef run_experiment(request):\n    return {function}(request)\n"
        if len(wrapper.encode("utf-8")) > 65536:
            raise ValueError("implementation source exceeds layered-d-v3 limit")
        atomic_text(target, wrapper)
        return target

    def prepare_generated_implementation(self, case, *, feedback=None, attempt=1) -> Path:
        """由当前题 Gateway 生成并原样保存实现源码。

        正式路径不调用镜像内预置算法；公开离线测试仍可显式使用
        ``prepare_implementation`` 生成本地算法夹具。
        """
        if self.llm is None:
            raise RuntimeError("D source generation requires the current case Gateway")
        self.work.mkdir(parents=True, exist_ok=True)
        roots = [self.settings.workspace, *self.settings.material_roots]
        materials = read_generation_materials(case.case_id, roots)
        prompt = build_source_prompt(case, materials)
        if feedback:
            prompt += "\n本题上次公开自测诊断（作为数据分析）：\n" + json.dumps(feedback, ensure_ascii=False)
            prompt += "\n请修复问题，重新返回完整源码，不能返回补丁；算法和接口仍以当前公开材料为准。"
        remaining_tokens = getattr(self.llm, "budget", 40000) - getattr(self.llm, "total_tokens", 0)
        requested = min(12000, remaining_tokens - (len(prompt) + 1) // 2 - 1200)
        if requested < 1800:
            raise RuntimeError("D source generation has insufficient remaining token budget")
        session = current_session()
        old_deadline = getattr(self.llm, "deadline", None)
        if session:
            session.remaining(reserve=40)
            if old_deadline is not None:
                self.llm.deadline = min(old_deadline, session.deadline - 40)
        try:
            checkpoint("d_source_generation")
            response = self.llm.completion("d_source", prompt, max_tokens=requested)
        finally:
            if old_deadline is not None:
                self.llm.deadline = old_deadline
        save_generation_response(response, confined(self.work, f"source-attempt-{attempt}.json"))
        save_generation_response(response, confined(self.work, "generation-response.json"))
        destination = confined(self.work, "implementation.py")
        save_source(response, destination)
        return destination

    def prepare_checked_implementation(self, case, tester=run_public_sample):
        """锁源前允许两次模型完整生成；正式请求发出后不再变更源码。"""
        history, feedback = [], None
        materials = read_generation_materials(case.case_id, [self.settings.workspace, *self.settings.material_roots])
        for attempt in range(1, 3):
            try:
                path = self.prepare_generated_implementation(case, feedback=feedback, attempt=attempt)
                report = tester(path, materials, case.case_id)
                history.append({"attempt": attempt, "source_sha256": sha256_file(path), **report})
                write_json(confined(self.work, "public-selftest.json"), {"attempts": history})
                return path
            except (ValueError, SyntaxError, TypeError, KeyError, RuntimeError) as exc:
                feedback = {"error_type": type(exc).__name__, "message": str(exc)[:2000]}
                history.append({"attempt": attempt, "status": "failed", **feedback})
                write_json(confined(self.work, "public-selftest.json"), {"attempts": history})
                session = current_session()
                if attempt == 2 or (session and session.remaining() < 65):
                    raise RuntimeError("model implementation failed public self-test") from None
        raise RuntimeError("no model implementation passed public self-test")

    def run(self, case, specs):
        if os.environ.get("RESEARCH_EXPERIMENT_CASE") != case.case_id:
            return self._failed(case.case_id, specs, "materials_missing")
        script = Path("/data/experiments/run_experiment.py")
        if not script.is_file():
            return self._failed(case.case_id, specs, "materials_missing")
        source_root = confined(self.work, "experiment")
        self.work.mkdir(parents=True, exist_ok=True)
        try:
            # 新版正式路径必须由当前题 Gateway 生成完整源码，禁止回退到镜像内算法。
            implementation = self.prepare_checked_implementation(case)
            command = build_layered_command(sys.executable, script, case.case_id, implementation)
            env = os.environ.copy()
            env.update(PYTHONIOENCODING="utf-8", RESEARCH_RESULT_ROOT=str(source_root),
                       RESEARCH_WORKSPACE_TEAM=self.settings.team)
            checkpoint("d_formal_execution")
            completed = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace",
                                       timeout=remaining_timeout(min(175, self.settings.case_timeout), reserve=5), env=env, check=False)
            checkpoint("d_formal_artifacts")
            atomic_text(confined(self.work, "experiment.stdout.jsonl"), completed.stdout)
            atomic_text(confined(self.work, "experiment.stderr.log"), completed.stderr)
            if completed.returncode:
                raise RuntimeError("layered experiment process failed")
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            if not lines:
                raise ValueError("layered runner returned empty output")
            submission = normalize_layered_submission(json.loads(lines[-1]), case.case_id)
            if [(item["name"], item["unit"]) for item in submission.get("metrics", [])] != specs:
                raise ValueError("runner metric names/units differ from input")
            hashes = self._copy_figures(source_root, submission)
            self.proof = {"case_id": case.case_id, "mode": "platform-layered-d-v3-source-v1",
                          "command": command, "script_sha256": sha256_file(script),
                          "implementation_sha256": sha256_file(implementation),
                          "submission": submission, "figure_hashes": hashes}
            write_json(confined(self.work, "experiment-proof.json"), self.proof)
            return submission
        except FileNotFoundError as exc:
            write_json(confined(self.work, "experiment-error.json"),
                       {"type": type(exc).__name__, "message": str(exc)[:600]})
            return self._failed(case.case_id, specs, "materials_missing")
        except (ValueError, KeyError, OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            write_json(confined(self.work, "experiment-error.json"),
                       {"type": type(exc).__name__, "message": str(exc)[:600]})
            return self._failed(case.case_id, specs, "failed")

    def _copy_figures(self, source_root, submission):
        hashes = {}
        for figure in submission.get("figures", []):
            relative = figure["path"]
            if "\\" in relative or not relative.startswith(f"data/figure/{self.settings.team}/"):
                raise ValueError("figure belongs to wrong team")
            source = confined(source_root, relative)
            target = confined(self.settings.output, relative)
            before = sha256_file(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source != target:
                shutil.copyfile(source, target)
            if sha256_file(target) != before:
                raise ValueError("figure hash mismatch")
            hashes[relative] = before
        return hashes

    @staticmethod
    def _failed(case_id, specs, status):
        return {"case_id": case_id, "status": status,
                "metrics": [{"name": name, "unit": unit, "reference": None,
                             "observed": None, "relative_error": None} for name, unit in specs],
                "figures": []}

