"""结合原始产物和人工事实复核，给百篇库两轮评测计本地诊断分。"""

from __future__ import annotations

import argparse
import json

from .benchmark import ROOT
from .library_benchmark import LIBRARY_ROUNDS, case_payload
from .score_runs import citation_coverage, experiment_checks, load
from .scoring import aggregate, score_chat, score_idea, score_retrieval, score_survey


def score_round(number: int, *, retest: bool = False) -> dict:
    spec = next(row for row in LIBRARY_ROUNDS if row["number"] == number)
    out = ROOT / "output" / "library-evaluation" / f"round-{number}{'-retest' if retest else ''}"
    team = f"lib_eval_r{number}{'_retest' if retest else ''}"
    status = load(out / "run-status.json")
    judgments = load(ROOT / "evaluation" / "judgments" /
                     f"library-round-{number}{'-retest' if retest else ''}.json")
    categories, cases = {}, {}
    for letter in "ABCDE":
        payload = case_payload(letter, spec)
        case_id = payload["case_id"]
        row = status["cases"].get(case_id)
        if row is None or row["state"] not in {"completed", "failed"}:
            raise ValueError(f"case pending: {case_id}")
        if row["state"] == "failed":
            categories[letter] = [0.0]
            cases[case_id] = {"state": "failed", "score": 0.0, "seconds": row["seconds"],
                              "error_type": row.get("error_type")}
            continue
        result = load(out / row["result_path"])
        if letter == "A":
            detail = {}
            score = score_chat(result)
        elif letter == "B":
            returned = [paper["filename"] for paper in result["papers"]]
            score = score_retrieval(returned, spec["gold_b"])
            detail = {"returned": returned, "gold": spec["gold_b"]}
        elif letter == "C":
            judgment = judgments.get(case_id)
            if judgment is None:
                raise ValueError(f"human fact review missing: {case_id}")
            frozen = {key for key, _ in spec["facts"]}
            correct = set(judgment["correct_fact_ids"])
            if not correct <= frozen:
                raise ValueError("unknown frozen fact ID")
            valid, covered = citation_coverage(result["markdown"], spec["report_materials"])
            detail = score_survey(len(correct), len(frozen), len(judgment["severe_errors"]), valid)
            detail.update({"correct_fact_ids": sorted(correct), "severe_errors": judgment["severe_errors"],
                           "covered_references": covered})
            score = detail["total"]
        elif letter == "D":
            detail = experiment_checks(out, team, case_id, result)
            score = detail["total"]
        else:
            judgment = judgments.get(case_id)
            if judgment is None:
                raise ValueError(f"human Idea review missing: {case_id}")
            covered = set(judgment["covered"])
            score = score_idea(covered)
            detail = {"covered": sorted(covered), "evidence": judgment["evidence"]}
        categories[letter] = [score]
        cases[case_id] = {"state": "completed", "score": score, "seconds": row["seconds"], **detail}
    overall = aggregate(categories, "diagnostic")
    scored = {"round": number, "retest": retest, "theme": spec["theme"],
              "source_rule": "research-assistant-objective-v2",
              "label": "本地五题诊断分，非官方榜单分", "scores": overall, "cases": cases}
    (out / "local-score.json").write_text(json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8")
    return scored


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, choices=[4, 5], required=True)
    parser.add_argument("--retest", action="store_true")
    args = parser.parse_args()
    scored = score_round(args.round, retest=args.retest)
    print(json.dumps({"round": args.round, "total": scored["scores"]["total"],
                      "category_scores": scored["scores"]["category_scores"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
