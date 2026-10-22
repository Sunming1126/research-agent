"""按赛事五题类别平均公式，给 25 题仿真计算本地代理诊断分。"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from .build_300_dispatch import BASE
from .score_runs import citation_coverage, experiment_checks
from .score_ten_rounds import _evidence, _idea_proxy
from .scoring import score_chat, score_idea, score_retrieval


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def score_round(number: int) -> dict:
    out = BASE / f"round-{number:02d}"
    result_root = out / "results"
    team = f"sim300_r{number:02d}"
    status = read(out / "run-status.json")["cases"]
    frozen = read(out / "frozen-spec.json")["cases"]
    assigned = out / "workspace" / "data" / team / "assigned"
    by_category = defaultdict(list)
    details = {}
    for letter in "ABCDE":
        for index in range(1, 6):
            case_id = f"{letter}-{index:02d}"
            case = read(assigned / f"{case_id}.json")
            row = status.get(case_id)
            if row is None:
                raise ValueError(f"pending {case_id}")
            score = 0.0
            extra = {}
            if row["state"] == "completed":
                result = read(result_root / row["result_path"])
                if result["case_id"] != case_id:
                    raise ValueError(f"wrong result identity: {case_id}")
                if letter == "A":
                    score = score_chat(result)
                elif letter == "B":
                    returned = [paper["filename"] for paper in result["papers"]]
                    gold = frozen[case_id]["gold"]
                    score = score_retrieval(returned, gold)
                    extra = {"returned": returned, "gold": gold,
                             "question_origin": frozen[case_id]["question_origin"]}
                elif letter == "C":
                    claims = _evidence(result_root, team, case_id)["claims"]
                    counts = {name: sum(claim["filename"] == name for claim in claims)
                              for name in case["materials"]}
                    fact_proxy = 3 * sum(count >= 2 for count in counts.values())
                    refs, covered = citation_coverage(result["markdown"], case["materials"])
                    score = fact_proxy + 2 * refs
                    extra = {"claims_by_paper": counts, "covered_references": covered,
                             "factually_reviewed": False}
                elif letter == "D":
                    extra = experiment_checks(result_root, team, case_id, result)
                    score = extra["total"]
                else:
                    criteria = _idea_proxy(result["markdown"], case["materials"])
                    score = score_idea(criteria)
                    extra = {"visible_criteria": sorted(criteria), "factually_reviewed": False}
            by_category[letter].append(score)
            details[case_id] = {"score": round(score, 4), "state": row["state"],
                                "seconds": row["seconds"], **extra}
    categories = {letter: round(sum(scores)/5, 4) for letter, scores in by_category.items()}
    scored = {"round": number, "shape": "25 cases, five per category",
              "label": "本地代理诊断，C/E 未逐句人工事实复核；非官方榜分",
              "total_proxy": round(sum(categories.values()), 4),
              "category_scores": categories, "cases": details,
              "completed": sum(row["state"] == "completed" for row in details.values()),
              "failed": sum(row["state"] != "completed" for row in details.values())}
    (out / "proxy-score.json").write_text(json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: scored[k] for k in ("round", "total_proxy", "category_scores",
                                              "completed", "failed")}, ensure_ascii=False))
    return scored


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, choices=range(1, 11), required=True)
    score_round(parser.parse_args().round)
