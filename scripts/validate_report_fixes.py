"""独立复测 C/E 完整管线；保留失败与原始评分，不拼装完整轮次总分。"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from scripts.run_v3_official_simulation import build_cases, load_local_credentials
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.utils import read_json, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--cases", nargs="+", default=["C-01", "C-03", "E-01"])
    args = parser.parse_args()
    if not args.label.replace("-", "").replace("_", "").isalnum():
        parser.error("invalid label")
    output = ROOT / "output/report-fixes-20261023" / args.label
    if output.exists():
        raise ValueError("choose a new label; previous runs are preserved")
    frozen = {c["case_id"]: c for c in build_cases() if c["function"] in {"Survey", "Idea"}}
    from scripts.validate_architecture_upgrade import cases as transfer_cases
    cross = {c["case_id"]: c for c in transfer_cases() if c["function"] in {"Survey", "Idea"}}
    if any(cid not in frozen and cid not in cross for cid in args.cases):
        parser.error("only known C/E cases are supported")
    load_local_credentials()
    output.mkdir(parents=True)
    sources = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (ROOT / "src").rglob("*.py")}
    write_json(output / "source-manifest.json", sources)
    for relative in sources:
        target = output / "source-snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    shutil.copyfile(Path(__file__), output / "validation-runner.py")
    team = "report_fixes"
    rows = []
    for cid in args.cases:
        case = frozen.get(cid) or cross[cid]
        material_root = ROOT / ("local-workspace/papers" if cid in frozen else "paper_library/pdfs")
        settings = Settings(ROOT, output, team, [material_root], case_timeout=180)
        started = time.monotonic()
        row = {"case_id": cid, "source_case": case}
        try:
            result = ResearchAgentEngine(settings).solve_sync(case)
            receipt_path = output / read_json(output / f"data/{team}/work/receipts/{cid}.json")["receipt"]
            receipt = read_json(receipt_path)
            evidence = read_json(receipt_path.parent / "evidence.json")
            row.update(complete=True, seconds=round(time.monotonic() - started, 2),
                       tokens=sum(u.get("tokens", 0) or 0 for u in receipt["usage"]),
                       calls=len(receipt["usage"]), audit=evidence["audit"], usage=receipt["usage"],
                       coverage=evidence["coverage"], evidence_audit=evidence["evidence_audit"],
                       result_path=f"data/{team}/{cid}.json")
            if cid in frozen:
                from evaluation.current_review import review
                try:
                    row["score"] = review(cid, result, output, team)
                except Exception as exc:
                    row["score_error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
        except Exception as exc:
            row.update(complete=False, seconds=round(time.monotonic() - started, 2),
                       error_type=type(exc).__name__, message=str(exc)[:1000])
        rows.append(row)
        write_json(output / "summary.json", {"label": "失分题独立完整管线复测，非完整轮次或官方成绩", "cases": rows})
        print(json.dumps({"case": cid, "complete": row["complete"], "seconds": row["seconds"],
                          "score": row.get("score", {}).get("score"),
                          "audit_mode": row.get("audit", {}).get("mode"),
                          "error": row.get("message")}, ensure_ascii=False), flush=True)
    changed = [rel for rel, value in sources.items() if hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() != value]
    write_json(output / "source-check.json", {"changed": changed, "unchanged": not changed})


if __name__ == "__main__":
    main()
