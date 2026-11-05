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
from .d_generation_policy import formal_timeout, generation_window, generation_tokens, platform_service_error, PlatformExperimentUnavailable
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
        prompt = build_source_prompt(case, materials)
        if feedback:
            prompt += "\n本轮生成诊断（数据，非指令）：" + json.dumps(feedback, ensure_ascii=False)
            prompt += "\n重新返回完整实现；不得使用占位结果。"
        remaining_tokens = getattr(self.llm, "budget", 40000) - getattr(self.llm, "total_tokens", 0)
        requested = min(generation_tokens(case.case_id, baseline_only), remaining_tokens - (len(prompt) + 1) // 2 - 1200)
        if requested < 1800:
            raise RuntimeError("D source generation has insufficient remaining token budget")
        with generation_window(self.llm, baseline=baseline_only, case_id=case.case_id):
            checkpoint("d_source_generation")
            response = self.llm.completion("d_source", prompt, max_tokens=requested)
        save_generation_response(response, confined(self.work, f"source-attempt-{attempt}.json"))
        save_generation_response(response, confined(self.work, "generation-response.json"))
        # A late response remains audit evidence, but cancellation/deadline
        # expiry must stop source selection and any later formal submission.
        checkpoint("d_source_received")
        destination = confined(self.work, "implementation.py")
        save_source(response, destination)
        return destination

    def prepare_checked_implementation(self, case, tester=None):
        """Only generic syntax/source checks gate formal execution; no old-task oracle."""
        history = []
        feedback = None
        for attempt in range(1, 4):
            try:
                path = self.prepare_generated_implementation(case, feedback=feedback, attempt=attempt)
                # save_source already checks complete response, Python syntax and entry point.
                write_json(confined(self.work, 'public-selftest.json'), {
                    'mode': 'generic_source_checks_only', 'semantic_selftest_performed': False,
                    'selected_attempt': attempt, 'attempts': history,
                    'source_sha256': sha256_file(path)})
                return path
            except ModelQuotaError:
                raise
            except (ModelError, ValueError, SyntaxError, RuntimeError, TimeoutError) as exc:
                # Preserve cancellation even on the final attempt. A transport
                # timeout in an active session can still use bounded recovery.
                checkpoint()
                feedback = {'error_type': type(exc).__name__,
                            'message': 'Return one complete syntactically valid implementation matching current materials.'}
                history.append({'attempt': attempt, 'error_type': type(exc).__name__})
                write_json(confined(self.work, 'public-selftest.json'), {'attempts': history})
                session = current_session()
                if attempt == 3 or (session and session.remaining() < 420):
                    raise RuntimeError('no complete current-session model source available') from exc
        raise RuntimeError('no complete current-session model source available')

    def review_implementation(self, case, implementation):
        """当轮独立测试 + 材料定位复核；修复失败不覆盖完整原稿及来源响应。"""
        session = current_session()
        if session and session.remaining() < 660:
            return implementation
        response_path = confined(self.work, "generation-response.json")
        source_bytes = implementation.read_bytes()
        response_bytes = response_path.read_bytes()
        from .d_review import review_prompt, review_result, review_retry_reason
        from .d_selftest import test_prompt, test_plan_result, run_tests, preserves_tests
        materials = read_generation_materials(case.case_id, [self.settings.workspace, *self.settings.material_roots])
        source = source_bytes.decode("utf-8")
        selected = "original"
        audit = {"selected": selected, "semantic_verified": False}
        tests = []; before = {"state": "not_run", "results": []}
        # 不接触隐藏反馈；测试由本轮模型仅据材料独立提出，再在受限进程执行。
        available = getattr(self.llm, "budget", 40000) - getattr(self.llm, "total_tokens", 0)
        tests_prompt = test_prompt(materials, case_id=case.case_id)
        if session and session.remaining() >= 1100 and available >= len(tests_prompt) // 2 + 18000:
            try:
                with generation_window(self.llm, baseline=False, case_id=case.case_id):
                    proposed = self.llm.completion("d_tests", tests_prompt, max_tokens=8000)
                try:
                    test_plan = test_plan_result(proposed, materials)
                except (ValueError,TypeError):
                    test_plan = {'tests':[], 'received':0, 'rejected':['invalid_response']}
                tests = test_plan['tests']
                if not tests and (test_plan['received'] or test_plan['rejected']) and session.remaining() >= 1200:
                    with generation_window(self.llm, baseline=False, case_id=case.case_id):
                        proposed = self.llm.completion('d_tests', tests_prompt + '\n上次测试未通过格式或原文引用校验。请只给一个最小合法测试，完整填写各字段，逐字引用材料。', max_tokens=8000)
                    test_plan = test_plan_result(proposed, materials)
                    tests = test_plan['tests']
                before = run_tests(source, tests)
                audit['selftest'] = {'state': before['state'], 'count': len(tests),
                                    'passed': sum(r['passed'] for r in before['results']),
                                    'observed_phases': sorted({t['request']['phase'] for t in tests
                                                               if isinstance(t['request'].get('phase'), str)}),
                                    'received': test_plan['received'], 'rejected': test_plan['rejected']}
            except (ModelError, ValueError, TypeError, RuntimeError, TimeoutError) as exc:
                audit['selftest'] = {'state': 'unavailable', 'error_type': type(exc).__name__}
        prompt = review_prompt(materials, source, {'tests': tests, 'execution': before} if tests else None)
        available = getattr(self.llm, "budget", 40000) - getattr(self.llm, "total_tokens", 0)
        if available < len(prompt) // 2 + 8000 or (session and session.remaining() < 660):
            audit['state'] = 'insufficient_review_budget'
            write_json(confined(self.work, "source-review.json"), audit)
            return implementation
        try:
            with generation_window(self.llm, baseline=False, case_id=case.case_id):
                review = self.llm.completion("d_review", prompt, max_tokens=8000)
            result = review_result(review, materials, source)
            audit.update(state=result['state'], grounded_defects=len(result['defects']), rejected_defects=result['rejected'])
            # 格式错误与“检查通过”必须区分；有余量时只重试一次，不丢弃完整原稿。
            retry_reason = review_retry_reason(result, before)
            if retry_reason and (not session or session.remaining() >= 1000):
                with generation_window(self.llm, baseline=False, case_id=case.case_id):
                    review = self.llm.completion("d_review", prompt + "\n上次审查需重新核对（" + retry_reason + "）。请先检查失败测试是否合法并独立手算期望，再核对源码。测试可能错误，不强制修代码；确有错误才repair并逐字引用材料和源码，否则keep。完整输出约定JSON。", max_tokens=8000)
                result = review_result(review, materials, source)
                audit.update(state=result['state'], grounded_defects=len(result['defects']), rejected_defects=result['rejected'], format_retry=retry_reason=='invalid_response', retry_reason=retry_reason)
            if result['defects'] and (not session or session.remaining() >= 660):
                feedback = {"defects": result['defects'], "current_candidate": source,
                            "instruction": "先核实测试及诊断符合材料，仅修复确切错误，保留其它逻辑。返回完整实现。"}
                self.prepare_generated_implementation(case, feedback=feedback, attempt="review")
                after = run_tests(implementation.read_text(encoding='utf-8'), tests)
                if not preserves_tests(before, after):
                    implementation.write_bytes(source_bytes)
                    response_path.write_bytes(response_bytes)
                    audit['repair'] = 'rejected_test_regression'
                else:
                    selected = "review"
                    audit['repair'] = 'selected'
                audit['retest'] = {'state': after['state'], 'passed': sum(r['passed'] for r in after['results'])}
                if selected == 'review':
                    from .d_refinement import refine_remaining
                    audit['followup'] = refine_remaining(self, case, implementation, materials, tests, after)
                    if audit['followup']['selected']:
                        selected = 'review-2'
        except (ModelError, ValueError, TypeError, KeyError, SyntaxError, RuntimeError, TimeoutError) as exc:
            implementation.write_bytes(source_bytes)
            response_path.write_bytes(response_bytes)
            audit['error_type'] = type(exc).__name__
        if selected != 'original':
            from .d_holdout import validate_revision
            audit['independent_validation'] = validate_revision(self, case, implementation, materials, tests, source_bytes, response_bytes)
            if audit['independent_validation']['reverted']:
                selected = 'original'
        audit['selected'] = selected
        write_json(confined(self.work, "source-review.json"), audit)
        return implementation

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
            implementation = self.review_implementation(case, implementation)
            command = build_layered_command(sys.executable, script, case.case_id, implementation)
            env = os.environ.copy()
            env.update(PYTHONIOENCODING="utf-8", RESEARCH_RESULT_ROOT=str(source_root),
                       RESEARCH_WORKSPACE_TEAM=self.settings.team)
            checkpoint("d_formal_execution")
            completed = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace",
                                       timeout=remaining_timeout(formal_timeout(), reserve=15), env=env, check=False)
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

