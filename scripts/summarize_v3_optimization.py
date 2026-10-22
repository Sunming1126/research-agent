"""汇总三轮原始结果、统一裁判分、证据与资源开销，不覆盖原始评分。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_round(team):
    out = ROOT / "output" / team
    original = load(out / "simulation-score.json")
    current_path = out / "current-score.json"
    current = load(current_path) if current_path.is_file() else original
    rows = []
    for cid in current["dispatch"]["case_ids"]:
        pointer = out / "data" / team / "work" / "receipts" / f"{cid}.json"
        if not pointer.is_file():
            rows.append({"case_id": cid, "receipt_missing": True})
            continue
        path = out / load(pointer)["receipt"]
        receipt = load(path)
        evidence = load(path.parent / "evidence.json")
        audit = evidence.get("audit") or {}
        rows.append({"case_id": cid, "tokens": sum(row.get("tokens", 0) for row in receipt.get("usage", [])),
                     "model_calls": len(receipt.get("usage", [])), "claims": len(evidence.get("claims", [])),
                     "rejected_claims": len(evidence.get("rejected_claims", [])),
                     "audit_ok": audit.get("ok"), "audit_mode": audit.get("mode", "model"),
                     "audit_issues": audit.get("model_issues", audit.get("issues", [])),
                     "excluded": evidence.get("retrieval_trace", {}).get("explicitly_excluded", [])})
    manifest = out / "source-manifest.json"
    return {"team": team, "complete": current["run"]["complete"], "seconds": current["run"]["seconds"],
            "precheck": current["precheck"], "original_score": original["automatic_score_proxy"],
            "comparable_score": current["automatic_score_proxy"], "category_scores": current["category_scores"],
            "review_protocols": sorted({value["review_protocol"] for value in current["cases"].values()
                                         if "review_protocol" in value}),
            "rubric_hashes": sorted({value["rubric_sha256"] for value in current["cases"].values()
                                      if "rubric_sha256" in value}),
            "source_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(), "cases": rows,
            "total_agent_tokens": sum(row.get("tokens", 0) for row in rows),
            "long_report_fallbacks": sum(row.get("audit_mode") == "deterministic_fallback" for row in rows),
            "score_file": str(current_path.relative_to(ROOT) if current_path.is_file()
                              else (out / "simulation-score.json").relative_to(ROOT))}


def main():
    rounds = [summarize_round(f"v3_opt_20261022_r{i}") for i in range(1, 4)]
    baseline = load(ROOT / "output" / "v3-official-simulation" / "current-score.json")
    summary = {"label": "本地公开材料三轮真实执行与模型代理评审，非官方榜分",
               "baseline_comparable_score": baseline["automatic_score_proxy"],
               "baseline_category_scores": baseline["category_scores"], "rounds": rounds}
    followup = ROOT / "output" / "v3_opt_20261022_followup" / "validation-summary.json"
    if followup.is_file():
        summary["independent_followup"] = [{"case_id": row["case_id"], "complete": row["complete"],
            "score": row.get("score", {}).get("score"), "tokens": row.get("tokens")}
            for row in load(followup)["cases"]]
    summary["mean_score"] = sum(row["comparable_score"] for row in rounds) / len(rounds)
    dest = ROOT / "output" / "v3-optimization-20261022"
    dest.mkdir(parents=True, exist_ok=True)
    sources = list((ROOT / "src").rglob("*.py")) + [ROOT / name for name in (
        "Dockerfile", "pyproject.toml", "requirements.txt", "requirements-test.txt",
        "agent.py", "evaluation/current_review.py", "scripts/run_v3_official_simulation.py",
        "scripts/retest_v3_failures.py", "scripts/summarize_v3_optimization.py")]
    final_manifest = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(sources)}
    (dest / "final-source-manifest.json").write_text(json.dumps(final_manifest, indent=2), encoding="utf-8")
    (dest / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
