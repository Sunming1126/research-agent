"""独立复测单题，不覆盖十轮原始结果与分数。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time

from .benchmark import EXPERIMENTS, ROOT
from .build_300_dispatch import BASE
from .run_rounds import load_local_credentials
from .scoring import score_retrieval
from .score_runs import experiment_checks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, choices=range(1, 11), required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if (args.case[:2] not in {f"{letter}-" for letter in "ABCDE"}
            or not args.revision.replace("-", "").isalnum()):
        parser.error("invalid case or revision")
    sys.path.insert(0, str(ROOT / "src"))
    from research_agent.core.config import Settings
    from research_agent.core.engine import ResearchAgentEngine
    from research_agent.rag.materials import DocumentParser

    load_local_credentials()
    original = BASE / f"round-{args.round:02d}"
    out = original / "retests" / args.revision
    team = f"sim300_r{args.round:02d}"
    settings = Settings(workspace=original / "workspace", output=out, team=team,
                        material_roots=[ROOT / "paper_library" / "pdfs-300", EXPERIMENTS / "publicdata"],
                        experiment_mode="local", local_experiment_root=EXPERIMENTS,
                        local_python=sys.executable, llm_calls=30, llm_tokens=40000,
                        case_timeout=300)
    source = BASE / f"candidate-cache-4-{DocumentParser.VERSION}" / "document-cache"
    target = out / "data" / team / "work" / "document-cache"
    target.mkdir(parents=True, exist_ok=True)
    for item in source.glob("*.json"):
        shutil.copy2(item, target / item.name)
    payload = json.loads((original / "workspace" / "data" / team / "assigned" /
                          f"{args.case}.json").read_text(encoding="utf-8"))
    started = time.monotonic()
    result = ResearchAgentEngine(settings).solve_sync(payload)
    summary = {"case_id": args.case, "revision": args.revision,
               "seconds": round(time.monotonic()-started, 2), "completed": True}
    if args.case.startswith("D-"):
        summary.update({"status": result.get("status"),
                        "metrics_count": len(result.get("metrics", [])),
                        "figures_count": len(result.get("figures", []))})
        if result.get("status") != "completed":
            summary["completed"] = False
        if result.get("status") == "completed":
            summary["score_checks"] = experiment_checks(out, team, args.case, result)
    if args.case.startswith("B-"):
        returned = [row["filename"] for row in result["papers"]]
        gold = json.loads((original / "frozen-spec.json").read_text(encoding="utf-8"))["cases"][args.case]["gold"]
        summary.update({"score": score_retrieval(returned, gold), "returned": returned, "gold": gold})
    if args.case.startswith(("C-", "E-")):
        summary.update({"markdown_chars": len(result["markdown"]), "markdown_path": result["path"]})
    (out / "retest-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
