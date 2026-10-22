"""三轮结束后仅复测失败路径；结果另存，不覆盖三轮原始成绩。"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts import run_v3_official_simulation as sim
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine


def main():
    sim.load_local_credentials()
    team = "v3_opt_20261022_followup"
    out = ROOT / "output" / team
    if (out / "validation-summary.json").exists():
        raise ValueError("follow-up already exists; original results must be preserved")
    sim.TEAM = team
    settings = Settings(workspace=ROOT, output=out, team=team,
        material_roots=[sim.PAPERS, sim.EXPERIMENTS / "publicdata"], experiment_mode="local",
        local_experiment_root=sim.EXPERIMENTS, local_python=sys.executable,
        llm_calls=30, llm_tokens=40000, case_timeout=480)
    engine = ResearchAgentEngine(settings)
    cases = [case for case in sim.build_cases() if case["case_id"] in {"B-01", "C-03", "E-01"}]
    out.mkdir(parents=True, exist_ok=True)
    manifest = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (ROOT / "src").rglob("*.py")}
    (out / "source-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    rows = []
    for case in cases:
        started = time.monotonic()
        cid = case["case_id"]
        try:
            result = engine.solve_sync(case)
            pointer = json.loads((out / f"data/{team}/work/receipts/{cid}.json").read_text(encoding="utf-8"))
            receipt_path = out / pointer["receipt"]
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            evidence = json.loads((receipt_path.parent / "evidence.json").read_text(encoding="utf-8"))
            rows.append({"case_id": cid, "complete": True, "score": sim.score_case(cid, result, out),
                         "audit": evidence.get("audit"), "tokens": sum(r["tokens"] for r in receipt["usage"]),
                         "usage": receipt["usage"], "seconds": round(time.monotonic() - started, 2)})
        except Exception as exc:
            rows.append({"case_id": cid, "complete": False, "error_type": type(exc).__name__,
                         "message": str(exc)[:600]})
        (out / "validation-summary.json").write_text(json.dumps({"label": "定向复测，不是第四轮或原始分替换", "cases": rows},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"case_id": cid, "complete": rows[-1]["complete"],
                          "score": rows[-1].get("score", {}).get("score")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
