"""运行冻结的十轮跨领域真实模型诊断；每轮独立保存结果。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

from .benchmark import EXPERIMENTS, ROOT
from .library_benchmark import LIBRARY
from .run_rounds import load_local_credentials
from .ten_round_benchmark import ROUNDS, case_payload


def settings_for(number: int):
    sys.path.insert(0, str(ROOT / "src"))
    from research_agent.core.config import Settings

    out = ROOT / "output" / "ten-rounds" / f"round-{number:02d}"
    out.mkdir(parents=True, exist_ok=True)
    return Settings(workspace=ROOT, output=out, team=f"ten_r{number:02d}",
                    material_roots=[LIBRARY, EXPERIMENTS / "publicdata"],
                    experiment_mode="local", local_experiment_root=EXPERIMENTS,
                    local_python=sys.executable, llm_calls=30, llm_tokens=40000,
                    case_timeout=300)


def prepare_cache(settings) -> None:
    """复制已按文件哈希验证的前八页缓存；不把其他论文当成本题证据。"""
    source = ROOT / "output" / "library-evaluation" / "cold-index-benchmark-first-8-pages" / "document-cache"
    if len(list(source.glob("*.json"))) != 100:
        raise RuntimeError("verified shallow cache is missing")
    target = settings.output / "data" / settings.team / "work" / "document-cache"
    target.mkdir(parents=True, exist_ok=True)
    for item in source.glob("*.json"):
        destination = target / item.name
        if not destination.exists():
            shutil.copy2(item, destination)


def run_round(number: int) -> dict:
    from research_agent.core.engine import ResearchAgentEngine

    load_local_credentials()
    spec = next(row for row in ROUNDS if row["number"] == number)
    settings = settings_for(number)
    prepare_cache(settings)
    engine = ResearchAgentEngine(settings)
    status_file = settings.output / "run-status.json"
    status = json.loads(status_file.read_text(encoding="utf-8")) if status_file.is_file() else {
        "round": number, "domain": spec["domain"], "model": os.getenv("AGENT_MODEL_NAME", ""),
        "cases": {}}
    for letter in "ABDCE":
        payload = case_payload(letter, spec)
        case_id = payload["case_id"]
        if case_id in status["cases"]:
            continue
        started = time.monotonic()
        print(f"START round={number} case={case_id}", flush=True)
        try:
            result = engine.solve_sync(payload)
            row = {"state": "completed", "seconds": round(time.monotonic()-started, 2),
                   "result_path": f"data/{settings.team}/{case_id}.json"}
            print(f"DONE round={number} case={case_id} seconds={row['seconds']}", flush=True)
        except Exception as exc:
            # 控制台与总览只保留异常类型；详细诊断由每题工作目录保存。
            row = {"state": "failed", "seconds": round(time.monotonic()-started, 2),
                   "error_type": type(exc).__name__}
            print(f"FAIL round={number} case={case_id} type={type(exc).__name__}", flush=True)
        status["cases"][case_id] = row
        status_file.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, choices=range(6, 16), required=True)
    args = parser.parse_args()
    run_round(args.round)


if __name__ == "__main__":
    main()
