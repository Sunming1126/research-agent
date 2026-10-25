from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..core.utils import confined, read_json, sha256_file, write_json, atomic_text
from ..integrations.instrumentation import tool
from ..core.session import checkpoint, remaining_timeout


def parse_metric_specs(text, case_id=None):
    """读取明确名称/单位，兼容标记、括号和Markdown表格，不按题号猜测。"""
    pairs = re.findall(r'''["']?name["']?\s*[=:：]\s*["']?(.+?)["']?\s*[,，]\s*["']?unit["']?\s*[=:：]\s*["']?([^;；。\n}"']+)''', text, flags=re.I)
    if not pairs:
        pairs = re.findall(r'([^\n；，。、|:：()（）]+?)\s*[（(]\s*(?:单位\s*[:：]?\s*)?([^()（）]+)[)）]', text)
    if not pairs:
        pairs = [(cells[0], cells[1]) for line in text.splitlines()
                 if line.strip().startswith('|')
                 for cells in [[c.strip() for c in line.strip().strip('|').split('|')]]
                 if len(cells) >= 2 and cells[0] not in {'指标', '名称', 'name'}
                 and not set(cells[0]) <= set('-: ')]
    specs = [(name.strip().strip('"\''), unit.strip().rstrip('.').strip('"\'')) for name, unit in pairs]
    if len(specs) != 3 or len(set(name for name, unit in specs)) != 3:
        raise ValueError("input must identify exactly three name/unit pairs; no implicit case-id defaults")
    return specs


def resolve_metric_specs(case, settings):
    """当前挂载protocol是D指标顺序依据，和题面明确字段交叉核对。"""
    try:
        from_input = parse_metric_specs(case.input, case.case_id)
    except ValueError:
        from_input = None
    prefix = 'experiments' if settings.workspace.name == 'data' else 'data/experiments'
    path = confined(settings.workspace, f'{prefix}/{case.case_id}/protocol.json')
    if settings.experiment_mode == 'platform' and path.is_file():
        protocol = read_json(path)
        rows = protocol.get('metrics')
        if protocol.get('case_id') != case.case_id or not isinstance(rows, list) or len(rows) != 3:
            raise ValueError('invalid current-case metric protocol')
        specs = [(row.get('name'), row.get('unit')) for row in rows if isinstance(row, dict)]
        if len(specs) != 3 or any(not isinstance(x, str) or not x.strip() for pair in specs for x in pair) or len({n for n,u in specs}) != 3:
            raise ValueError('invalid current-case metric names/units')
        if from_input is not None:
            if set(from_input) != set(specs):
                raise ValueError('input metric names/units conflict with current protocol')
        elif not all(name.casefold() in case.input.casefold() for name, _ in specs) and 'protocol' not in case.input.casefold():
            raise ValueError('input does not identify the current protocol metrics')
        return specs
    if from_input is None:
        raise ValueError('no explicit metric contract in input or current mounted protocol')
    return from_input


def failed_result(case_id, specs, status):
    return {"case_id": case_id, "status": status,
            "metrics": [{"name": n, "unit": u, "reference": None, "observed": None, "relative_error": None} for n,u in specs],
            "figures": []}


class ExperimentRunner:
    """执行受信任的实验脚本，并保留能证明指标来源的产物。"""
    def __init__(self, settings, work, llm=None):
        self.settings, self.work, self.llm = settings, work, llm
        self.proof = None

    @tool(name="execute_authorized_experiment")
    def run(self, case, specs):
        s = self.settings
        # v3 正式 D 题必须提交本队实现源码；旧公开包只在显式 local 模式下走旧入口。
        if case.case_id in {"D-01", "D-02", "D-05"} and s.experiment_mode == "platform" \
                and s.experiment_protocol in {"auto", "layered-d-v3"}:
            from .layered_d_runner import LayeredDRunner
            layered = LayeredDRunner(s, self.work, llm=self.llm)
            result = layered.run(case, specs)
            self.proof = layered.proof
            return result
        try:
            if s.experiment_mode == "platform":
                if os.environ.get("RESEARCH_EXPERIMENT_CASE") != case.case_id:
                    return failed_result(case.case_id, specs, "materials_missing")
                script = Path("/data/experiments/run_experiment.py")
                if not script.is_file():
                    return failed_result(case.case_id, specs, "materials_missing")
                command = [sys.executable, str(script), "--case", case.case_id]
                source_root = confined(self.work, "experiment")
            else:
                # 此目录来自本地开发配置，不接受模型生成的路径。
                root = s.local_experiment_root
                if root is None or not root.is_dir():
                    return failed_result(case.case_id, specs, "materials_missing")
                script = confined(root, "experiment.py")
                data_dir = confined(root, "publicdata")
                if not script.is_file() or not confined(data_dir, case.case_id + ".json").is_file():
                    return failed_result(case.case_id, specs, "materials_missing")
                source_root = confined(self.work, "experiment")
                run_id = self.work.name
                command = [s.local_python or sys.executable, str(script), "--case", case.case_id,
                           "--output", str(source_root), "--data", str(data_dir),
                           "--team", s.team, "--run-id", run_id,
                           "--task-id", "TASK_" + case.case_id.replace("-", "_") + "_SINGLE_MACHINE"]
            self.work.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env.update(PYTHONIOENCODING="utf-8", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
                       MKL_NUM_THREADS="1", MPLCONFIGDIR=str(confined(self.work, "mpl-cache")),
                       RESEARCH_RESULT_ROOT=str(source_root), RESEARCH_WORKSPACE_TEAM=s.team)
            checkpoint("experiment_execution")
            completed = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace",
                                       timeout=remaining_timeout(min(115, s.case_timeout), reserve=5), env=env, check=False)
            checkpoint("experiment_artifacts")
            # 只保存实验输出，不记录环境变量或认证凭据。
            atomic_text(confined(self.work, "experiment.stdout.jsonl"), completed.stdout)
            atomic_text(confined(self.work, "experiment.stderr.log"), completed.stderr)
            if completed.returncode:
                raise RuntimeError("experiment process failed")
            if s.experiment_mode == "platform":
                submission = json.loads(completed.stdout.strip().splitlines()[-1])
            else:
                submission = read_json(confined(source_root, "submission.json"))
                record = read_json(confined(source_root, "command.json"))
                if record["case_id"] != case.case_id or record["run_id"] != run_id:
                    raise ValueError("experiment run identity mismatch")
                for relative in ("metrics.json", "data/source_data.csv", "command.json"):
                    path = confined(source_root, relative)
                    if not path.is_file() or not path.stat().st_size:
                        raise ValueError("missing experiment evidence")
            # v19 的平台回执在不同 runner 版本中可能使用 complete/completed；
            # 内部统一成提交 Schema 使用的 completed，避免把真实实验误判为失败。
            if submission.get("status") == "complete":
                submission = dict(submission)
                submission["status"] = "completed"
            if submission.get("case_id") != case.case_id or submission.get("status") != "completed":
                raise ValueError("experiment result mismatch")
            actual = [(m["name"], m["unit"]) for m in submission.get("metrics", [])]
            # 指标名称和单位必须与题目逐项相同，不能给其他测量值改名。
            if actual != specs:
                raise ValueError("runner metric names/units differ from input; values cannot be relabelled")
            hashes = {}
            for figure in submission.get("figures", []):
                relative = figure["path"]
                if not relative.startswith(f"data/figure/{s.team}/"):
                    raise ValueError("figure belongs to wrong team")
                source = confined(source_root, relative)
                target = confined(s.output, relative)
                before = sha256_file(source)
                if source != target:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                if sha256_file(target) != before:
                    raise ValueError("figure hash mismatch")
                hashes[relative] = before
            self.proof = {"case_id": case.case_id, "mode": s.experiment_mode,
                          "command": command, "script_sha256": sha256_file(script),
                          "submission": submission, "figure_hashes": hashes}
            write_json(confined(self.work, "experiment-proof.json"), self.proof)
            return submission
        except (ValueError, KeyError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            write_json(confined(self.work, "experiment-error.json"), {"type": type(exc).__name__, "message": str(exc)[:600]})
            return failed_result(case.case_id, specs, "failed")

