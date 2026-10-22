#!/usr/bin/env python3
"""Run and validate all five public D experiments in isolated output folders."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


CASES = ("D-01", "D-02", "D-03", "D-04", "D-05")
TASK_IDS = {
    "D-01": "TASK_D_01_SINGLE_MACHINE",
    "D-02": "TASK_D_02_SINGLE_MACHINE",
    "D-03": "TASK_D_03_SINGLE_MACHINE",
    "D-04": "TASK_D_04_SINGLE_MACHINE",
    "D-05": "TASK_D_05_SINGLE_MACHINE",
}
METRIC_SPECS = {
    "D-01": [("Macro-F1", "分数"), ("Accuracy", "%"), ("运行时间", "秒")],
    "D-02": [("Recall@5", "%"), ("MRR@10", "分数"), ("查询吞吐", "queries/s")],
    "D-03": [("推理吞吐", "samples/s"), ("P95 延迟", "ms"), ("缓存命中率", "%")],
    "D-04": [("验证集准确率", "%"), ("约束通过率", "%"), ("搜索耗时", "分钟")],
    "D-05": [("公平性 Jain 指数", "分数"), ("归一化吞吐", "%"), ("平均等待时间", "ms")],
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def validate_case(case_id: str, out_dir: Path, run_id: str) -> dict[str, Any]:
    metrics_path = out_dir / "metrics.json"
    submission_path = out_dir / "submission.json"
    command_path = out_dir / "command.json"
    source_path = out_dir / "data" / "source_data.csv"
    figure_path = out_dir / "data" / "figure" / "calibration" / f"{case_id}.png"
    for path in (metrics_path, submission_path, command_path, source_path, figure_path):
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"missing or empty artifact: {path}")
    metrics = load_object(metrics_path)
    submission = load_object(submission_path)
    command = load_object(command_path)
    if metrics.get("run_id") != run_id or metrics.get("task_id") != TASK_IDS[case_id]:
        raise ValueError(f"metrics identity mismatch for {case_id}")
    if submission.get("case_id") != case_id or submission.get("status") != "completed":
        raise ValueError(f"submission status mismatch for {case_id}")
    actual_metrics = metrics.get("metrics")
    expected = METRIC_SPECS[case_id]
    if not isinstance(actual_metrics, list) or [(item.get("name"), item.get("unit")) for item in actual_metrics] != expected:
        raise ValueError(f"metric name/unit mismatch for {case_id}")
    if any(not isinstance(item.get("value"), (int, float)) for item in actual_metrics):
        raise ValueError(f"non-numeric metric value for {case_id}")
    submitted_figures = submission.get("figures")
    expected_figure = f"data/figure/calibration/{case_id}.png"
    if not isinstance(submitted_figures, list) or len(submitted_figures) != 1 or submitted_figures[0].get("path") != expected_figure:
        raise ValueError(f"figure metadata mismatch for {case_id}")
    if any(not str(submitted_figures[0].get(key, "")).strip() for key in ("caption", "x_label", "y_label", "legend")):
        raise ValueError(f"incomplete figure metadata for {case_id}")
    return {
        "case_id": case_id,
        "run_id": run_id,
        "task_id": TASK_IDS[case_id],
        "metrics": actual_metrics,
        "artifacts": {
            "metrics.json": {"bytes": metrics_path.stat().st_size, "sha256": sha256(metrics_path)},
            "submission.json": {"bytes": submission_path.stat().st_size, "sha256": sha256(submission_path)},
            "command.json": {"bytes": command_path.stat().st_size, "sha256": sha256(command_path)},
            "data/source_data.csv": {"bytes": source_path.stat().st_size, "sha256": sha256(source_path)},
            expected_figure: {"bytes": figure_path.stat().st_size, "sha256": sha256(figure_path)},
        },
        "command_record": command,
    }


def parse_events(stdout: str, case_id: str) -> dict[str, Any]:
    stages: list[str] = []
    iterations: list[int] = []
    results = 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("case_id") != case_id:
            continue
        if event.get("type") == "stage_event":
            stages.append(str(event.get("stage")))
        elif event.get("type") == "loop_iteration":
            iterations.append(int(event["iteration"]))
        elif event.get("type") == "result_generated":
            results += 1
    if "data_preprocess" not in stages:
        raise ValueError(f"{case_id} did not emit data_preprocess")
    if len(iterations) < 2:
        raise ValueError(f"{case_id} emitted fewer than two loop_iteration events")
    if results != 1:
        raise ValueError(f"{case_id} emitted {results} result_generated events")
    return {"stages": stages, "loop_iterations": iterations, "result_generated": results}


def run_case(args: argparse.Namespace, case_id: str, case_dir: Path) -> dict[str, Any]:
    run_id = f"calibration-{case_id}"
    command = [
        args.python,
        str(args.script),
        "--case",
        case_id,
        "--output",
        str(case_dir),
        "--data",
        str(args.data),
        "--team",
        "calibration",
        "--run-id",
        run_id,
        "--task-id",
        TASK_IDS[case_id],
    ]
    started = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
    elapsed = time.perf_counter() - started
    (case_dir / "calibration.stdout.jsonl").write_text(completed.stdout, encoding="utf-8")
    (case_dir / "calibration.stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"{case_id} failed with exit={completed.returncode}: {completed.stderr[-1000:]}")
    events = parse_events(completed.stdout, case_id)
    result = validate_case(case_id, case_dir, run_id)
    result["calibration_elapsed_wall_s"] = elapsed
    result["events"] = events
    return result


def main(args: argparse.Namespace) -> None:
    args.data = args.data.resolve()
    args.script = args.script.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not args.script.is_file():
        raise FileNotFoundError(args.script)
    results: list[dict[str, Any]] = []
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="d-materials-calibration-") as temp_root:
        temp_path = Path(temp_root)
        for case_id in CASES:
            results.append(run_case(args, case_id, temp_path / case_id))
        for result in results:
            case_dir = output / result["case_id"]
            case_dir.mkdir(parents=True, exist_ok=True)
            for name in ("metrics.json", "submission.json", "command.json", "calibration.stdout.jsonl", "calibration.stderr.log"):
                source = temp_path / result["case_id"] / name
                if source.is_file():
                    case_dir.joinpath(name).write_bytes(source.read_bytes())
            for relative in ("data/source_data.csv", f"data/figure/calibration/{result['case_id']}.png"):
                source = temp_path / result["case_id"] / relative
                target = case_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
    payload = {
        "schema_version": 1,
        "package": "research-objective-v2-20261010/d-materials",
        "seed": 42,
        "python": platform.python_version(),
        "command": sys.argv,
        "elapsed_wall_s": time.perf_counter() - started,
        "cases": results,
    }
    path = output / "calibration.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"type": "calibration_complete", "output": str(path), "cases": list(CASES)}, ensure_ascii=False, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parent / "publicdata")
    parser.add_argument("--output", type=Path, default=Path("calibration"))
    parser.add_argument("--script", type=Path, default=Path(__file__).resolve().parent / "experiment.py")
    parser.add_argument("--python", default=sys.executable)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        main(parse_args())
    except Exception as exc:
        print(f"calibration failed: {exc}", file=sys.stderr)
        raise
