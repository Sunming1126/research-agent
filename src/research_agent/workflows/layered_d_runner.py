from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..core.utils import confined, sha256_file, write_json, atomic_text
from ..core.session import checkpoint, current_session, remaining_timeout
from .source_generation import build_source_prompt, read_generation_materials, save_generation_response, save_source
from .sample_validation import run_public_sample
from .d_generation_policy import generation_window, generation_tokens, platform_service_error, PlatformExperimentUnavailable
from ..integrations.llm import ModelError, ModelQuotaError


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

    def prepare_generated_implementation(self, case, *, feedback=None, attempt=1, baseline_only=False) -> Path:
        """由当前题 Gateway 生成并原样保存实现源码。

        正式路径不调用镜像内预置算法；公开离线测试仍可显式使用
        ``prepare_implementation`` 生成本地算法夹具。
        """
        if self.llm is None:
            raise RuntimeError("D source generation requires the current case Gateway")
        self.work.mkdir(parents=True, exist_ok=True)
        roots = [self.settings.workspace, *self.settings.material_roots]
        materials = read_generation_materials(case.case_id, roots)
        if case.case_id == 'D-02':
            from .d02_policy import source_prompt
            prompt = source_prompt(case, materials, baseline_only=baseline_only)
        else:
            prompt = build_source_prompt(case, materials)
        if baseline_only and case.case_id != 'D-02':
            prompt += ('\n本次先完成可执行基线：run阶段只实现公开协议的baseline，candidate必须为null，details为{}。'
                       '不生成候选算法、开发集搜索或训练；不得伪造结果。'
                       'D-02的rank阶段仍实现data.selected指定的排名，baseline仅返回test_queries的完整排名。'
                       '只返回紧凑完整源码，目标700至1000 token；不需要解释、注释、类型标注或无用辅助函数。')
        if feedback:
            prompt += "\n本题上次公开自测诊断（作为数据分析）：\n" + json.dumps(feedback, ensure_ascii=False)
            previous_source = confined(self.work, 'implementation.py')
            if previous_source.is_file() and not (baseline_only and case.case_id == 'D-02'):
                prompt += '\n上次完整源码：\n' + previous_source.read_text(encoding='utf-8')
            prompt += "\n请修复问题，重新返回完整源码，不能返回补丁；算法和接口仍以当前公开材料为准。"
        if baseline_only and case.case_id == 'D-02':
            prompt += ('\n本次时间只够基线恢复：完整源码只包含BM25排名所需函数；'
                       'run仅按default_config返回test_queries排名，candidate=null、details={}；'
                       'rank仅按data.selected返回{candidate:测试排名}。不要开发集搜索或组指标。'
                       '上一版源码仅供定位问题，不要复制其中候选选参逻辑。')
        remaining_tokens = getattr(self.llm, "budget", 40000) - getattr(self.llm, "total_tokens", 0)
        requested = min(generation_tokens(case.case_id, baseline_only), remaining_tokens - (len(prompt) + 1) // 2 - 1200)
        if requested < 1800:
            raise RuntimeError("D source generation has insufficient remaining token budget")
        with generation_window(self.llm, baseline=baseline_only, case_id=case.case_id):
            checkpoint("d_source_generation")
            response = self.llm.completion("d_source", prompt, max_tokens=requested)
        save_generation_response(response, confined(self.work, f"source-attempt-{attempt}.json"))
        save_generation_response(response, confined(self.work, "generation-response.json"))
        destination = confined(self.work, "implementation.py")
        save_source(response, destination)
        return destination

    def prepare_checked_implementation(self, case, tester=run_public_sample):
        """自检用于诊断和有余量时的修复，不能代替正式实验准入。"""
        history, feedback, candidate, verified = [], None, None, None
        materials = read_generation_materials(case.case_id, [self.settings.workspace, *self.settings.material_roots])
        for attempt in range(1, 3):
            try:
                session = current_session()
                # D-02 repairs must still leave time for independent rank timing.
                # A short, newly generated honest baseline is preferable when a
                # second full search implementation cannot fit the remaining time.
                baseline_rescue = (case.case_id == 'D-02' and attempt > 1 and session is not None
                                   and session.remaining() < 100)
                path = self.prepare_generated_implementation(case, feedback=feedback, attempt=attempt,
                                                             baseline_only=baseline_rescue)
                # 只有完整、可解析、当前模型原样输出的源码才可作为候选。
                # 后续修复截断时保留之前的完整版本，不能拼接或修改源码。
                candidate = (path.read_bytes(), (self.work / 'generation-response.json').read_bytes())
                report = tester(path, materials, case.case_id)
                verified = (candidate, attempt)
                history.append({"attempt": attempt, "source_sha256": sha256_file(path), **report})
                write_json(confined(self.work, "public-selftest.json"), {"attempts": history,
                           'selected_attempt': attempt, 'public_selftest_passed': True})
                return path
            except ModelQuotaError:
                checkpoint("d_generation_quota_failure")
                if candidate is None:
                    raise
                break
            except (ValueError, SyntaxError, TypeError, KeyError, RuntimeError, TimeoutError) as exc:
                checkpoint("d_generation_failure")
                feedback = {"error_type": type(exc).__name__, "message": str(exc)[:2000]}
                history.append({"attempt": attempt, "status": "failed", **feedback})
                write_json(confined(self.work, "public-selftest.json"), {"attempts": history})
                session = current_session()
                if attempt == 2 or (session and session.remaining() < 75):
                    break
        if candidate is None:
            raise RuntimeError('no complete current-session model source available')
        if verified is not None:
            candidate = verified[0]
        path = confined(self.work, 'implementation.py')
        path.write_bytes(candidate[0])
        (self.work / 'generation-response.json').write_bytes(candidate[1])
        write_json(confined(self.work, 'public-selftest.json'), {
            'attempts': history, 'decision': 'submit_complete_model_source_for_formal_evaluation',
            'public_selftest_passed': verified is not None,
            'selected_attempt': verified[1] if verified else None, 'source_sha256': sha256_file(path)})
        return path

    def run(self, case, specs):
        if os.environ.get("RESEARCH_EXPERIMENT_CASE") != case.case_id:
            return self._failed(case.case_id, specs, "materials_missing")
        script = Path("/data/experiments/run_experiment.py")
        if not script.is_file():
            return self._failed(case.case_id, specs, "materials_missing")
        source_root = confined(self.work, "experiment")
        self.work.mkdir(parents=True, exist_ok=True)
        submission = None
        try:
            read_generation_materials(case.case_id, [self.settings.workspace, *self.settings.material_roots])
        except FileNotFoundError as exc:
            write_json(confined(self.work, "experiment-error.json"),
                       {"type": type(exc).__name__, "stage": "materials", "message": str(exc)[:600]})
            return self._failed(case.case_id, specs, "materials_missing")
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
            atomic_text(confined(self.work, "experiment.stdout.jsonl"), completed.stdout)
            atomic_text(confined(self.work, "experiment.stderr.log"), completed.stderr)
            if completed.returncode:
                service_error = platform_service_error(completed.stderr)
                if service_error:
                    raise PlatformExperimentUnavailable(service_error)
                raise RuntimeError("layered experiment process failed; see experiment.stderr.log")
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            if not lines:
                raise ValueError("layered runner returned empty output")
            submission = normalize_layered_submission(json.loads(lines[-1]), case.case_id)
            # 保存客户端真实回执；后处理失败也不得用占位答卷覆盖这份结果。
            write_json(confined(self.work, "platform-submission.json"), submission)
            if [(item["name"], item["unit"]) for item in submission.get("metrics", [])] != specs:
                raise ValueError("runner metric names/units differ from input")
            hashes = self._copy_figures(source_root, submission)
            self.proof = {"case_id": case.case_id, "mode": "platform-layered-d-v3-source-v1",
                          "command": command, "script_sha256": sha256_file(script),
                          "implementation_sha256": sha256_file(implementation),
                          "submission": submission, "figure_hashes": hashes}
            write_json(confined(self.work, "experiment-proof.json"), self.proof)
            return submission
        except PlatformExperimentUnavailable as exc:
            write_json(confined(self.work, "experiment-error.json"),
                       {"type": type(exc).__name__, "stage": "platform_service", "message": str(exc)})
            raise
        except (ValueError, TypeError, KeyError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            write_json(confined(self.work, "experiment-error.json"),
                       {"type": type(exc).__name__, "message": str(exc)[:600]})
            if submission is not None:
                raise RuntimeError('received platform result failed postprocessing; see platform-submission.json') from exc
            checkpoint("d_execution_failure")
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

