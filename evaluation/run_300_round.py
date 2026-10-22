"""运行一套 25 题正式形态仿真；每题立即落盘，允许断点续跑。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

from .benchmark import EXPERIMENTS, ROOT
from .build_300_dispatch import BASE
from .run_rounds import load_local_credentials


def run(number: int) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from research_agent.core.config import Settings
    from research_agent.core.dispatch import load_dispatch
    from research_agent.core.engine import ResearchAgentEngine

    load_local_credentials()
    out = BASE / f"round-{number:02d}"
    team = f"sim300_r{number:02d}"
    settings = Settings(workspace=out / "workspace", output=out / "results", team=team,
                        material_roots=[ROOT / "paper_library" / "pdfs-300", EXPERIMENTS / "publicdata"],
                        experiment_mode="local", local_experiment_root=EXPERIMENTS,
                        local_python=sys.executable, llm_calls=30, llm_tokens=40000,
                        case_timeout=300)
    # 对共享材料建立只读候选缓存；冷启动成本在独立基准中另计。
    from research_agent.rag.materials import DocumentParser
    source = BASE / f"candidate-cache-4-{DocumentParser.VERSION}" / "document-cache"
    target = settings.output / "data" / team / "work" / "document-cache"
    if len(list(source.glob("*.json"))) != 300:
        raise RuntimeError("300-paper candidate cache must be built first")
    target.mkdir(parents=True, exist_ok=True)
    for item in source.glob("*.json"):
        destination = target / item.name
        if not destination.exists():
            shutil.copy2(item, destination)
    cases = load_dispatch(settings)
    engine = ResearchAgentEngine(settings)
    status_path = out / "run-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {
        "round": number, "team": team, "model": os.environ.get("AGENT_MODEL_NAME", ""), "cases": {}}
    for payload in cases:
        case_id = payload["case_id"]
        if case_id in status["cases"]:
            continue
        print(f"START round={number:02d} {case_id}", flush=True)
        started = time.monotonic()
        try:
            result = engine.solve_sync(payload)
            row = {"state": "completed", "seconds": round(time.monotonic()-started, 2),
                   "result_path": f"data/{team}/{result['case_id']}.json"}
        except Exception as exc:
            row = {"state": "failed", "seconds": round(time.monotonic()-started, 2),
                   "error_type": type(exc).__name__}
        status["cases"][case_id] = row
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"DONE round={number:02d} {case_id} {row['state']} {row['seconds']:.2f}s", flush=True)
    if all(row["state"] == "completed" for row in status["cases"].values()):
        print("PRECHECK", engine.precheck(), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, choices=range(1, 11), required=True)
    args = parser.parse_args()
    run(args.round)
