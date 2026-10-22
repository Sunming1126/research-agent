"""在独立目录复测单个失败题，不改动十轮原始产物。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time

from .benchmark import EXPERIMENTS, ROOT
from .library_benchmark import LIBRARY
from .run_rounds import load_local_credentials
from .ten_round_benchmark import ROUNDS, case_payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, choices=range(6, 16), required=True)
    parser.add_argument("--letter", choices=list("ABCDE"), required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if not args.revision.replace("-", "").isalnum():
        parser.error("revision must be alphanumeric or hyphenated")
    sys.path.insert(0, str(ROOT / "src"))
    from research_agent.core.config import Settings
    from research_agent.core.engine import ResearchAgentEngine

    load_local_credentials()
    spec = next(row for row in ROUNDS if row["number"] == args.round)
    out = ROOT / "output" / "ten-rounds" / f"round-{args.round:02d}-{args.revision}"
    team = f"ten_r{args.round:02d}_{args.revision.replace('-', '_')}"
    settings = Settings(workspace=ROOT, output=out, team=team,
                        material_roots=[LIBRARY, EXPERIMENTS / "publicdata"],
                        experiment_mode="local", local_experiment_root=EXPERIMENTS,
                        local_python=sys.executable, llm_calls=30, llm_tokens=40000,
                        case_timeout=300)
    source = ROOT / "output" / "library-evaluation" / "cold-index-benchmark-first-8-pages" / "document-cache"
    target = out / "data" / team / "work" / "document-cache"
    target.mkdir(parents=True, exist_ok=True)
    for item in source.glob("*.json"):
        shutil.copy2(item, target / item.name)
    started = time.monotonic()
    result = ResearchAgentEngine(settings).solve_sync(case_payload(args.letter, spec))
    summary = {"round": args.round, "letter": args.letter,
               "seconds": round(time.monotonic()-started, 2), "case_id": result["case_id"],
               "result_path": f"data/{team}/{result['case_id']}.json"}
    (out / "targeted-status.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
