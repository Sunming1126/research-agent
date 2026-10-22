"""对真实论文与公开实验逐题运行 Agent，并保存可复核原始结果。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .benchmark import ROOT, PAPERS, EXPERIMENTS, ROUNDS, case_payload


def load_local_credentials() -> None:
    """只把本地密钥读入进程环境；不写入日志或评测文件。"""
    dotenv = ROOT / ".env.local"
    if not dotenv.is_file():
        return
    for raw in dotenv.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in {"PLATFORM_LLM_URL", "PLATFORM_API_KEY", "AGENT_MODEL_NAME", "RESEARCH_LLM_THINKING"}:
            os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def run_round(number: int, *, only: set[str] | None = None) -> dict:
    sys.path.insert(0, str(ROOT / "src"))
    from research_agent.core.config import Settings
    from research_agent.core.engine import ResearchAgentEngine

    load_local_credentials()
    spec = next(row for row in ROUNDS if row["number"] == number)
    team = f"eval_r{number}"
    out = ROOT / "output" / "evaluation" / f"round-{number}"
    out.mkdir(parents=True, exist_ok=True)
    settings = Settings(workspace=ROOT, output=out, team=team,
                        material_roots=[PAPERS, EXPERIMENTS / "publicdata"],
                        experiment_mode="local", local_experiment_root=EXPERIMENTS,
                        local_python=sys.executable, llm_calls=30, llm_tokens=40000,
                        case_timeout=480)
    engine = ResearchAgentEngine(settings)
    run_file = out / "run-status.json"
    summary = json.loads(run_file.read_text(encoding="utf-8")) if run_file.is_file() else {
        "round": number, "theme": spec["theme"], "model": os.getenv("AGENT_MODEL_NAME", ""), "cases": {}
    }
    for letter in "ABDCE":
        if only and letter not in only:
            continue
        payload = case_payload(letter, spec)
        case_id = payload["case_id"]
        if summary["cases"].get(case_id, {}).get("state") == "completed":
            continue
        started = time.monotonic()
        print(f"START round={number} case={case_id}", flush=True)
        try:
            result = engine.solve_sync(payload)
            summary["cases"][case_id] = {"state": "completed", "seconds": round(time.monotonic()-started, 2),
                                         "result_path": f"data/{team}/{case_id}.json"}
            print(f"DONE round={number} case={case_id} status={result.get('status', 'written')} seconds={summary['cases'][case_id]['seconds']}", flush=True)
        except Exception as exc:
            # 异常消息可能包含模型请求信息，评测摘要只写类型；详细错误由 Agent 写在本题工作目录。
            summary["cases"][case_id] = {"state": "failed", "seconds": round(time.monotonic()-started, 2),
                                         "error_type": type(exc).__name__}
            print(f"FAIL round={number} case={case_id} type={type(exc).__name__}", flush=True)
        run_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, choices=[1, 2, 3], required=True)
    parser.add_argument("--only", nargs="*", choices=list("ABCDE"))
    args = parser.parse_args()
    run_round(args.round, only=set(args.only) if args.only else None)


if __name__ == "__main__":
    main()
