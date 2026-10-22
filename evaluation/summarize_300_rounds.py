"""汇总 300 篇论文、十轮 25 题的冻结原始诊断，不混入独立复测。"""

from __future__ import annotations

import json
import statistics

from .build_300_dispatch import BASE


def summarize() -> dict:
    rounds = []
    for number in range(1, 11):
        path = BASE / f"round-{number:02d}" / "proxy-score.json"
        score = json.loads(path.read_text(encoding="utf-8"))
        if len(score["cases"]) != 25 or score["completed"] + score["failed"] != 25:
            raise ValueError(f"round {number} does not contain 25 scored cases")
        b_cases = [score["cases"][f"B-{index:02d}"] for index in range(1, 6)]
        rounds.append({"round": number, "total_proxy": score["total_proxy"],
                       "category_scores": score["category_scores"],
                       "completed": score["completed"], "failed": score["failed"],
                       "b_exact_gold": sum(set(row.get("returned", [])) == set(row.get("gold", []))
                                           and len(row.get("returned", [])) == len(row.get("gold", []))
                                           for row in b_cases)})
    totals = [row["total_proxy"] for row in rounds]
    category_means = {letter: round(statistics.mean(row["category_scores"][letter] for row in rounds), 4)
                      for letter in "ABCDE"}
    summary = {"label": "本地代理诊断；非官方榜分，C/E 未逐句人工事实复核",
               "papers": 300, "rounds": rounds, "cases": 250,
               "completed": sum(row["completed"] for row in rounds),
               "failed": sum(row["failed"] for row in rounds),
               "mean_proxy": round(statistics.mean(totals), 4),
               "median_proxy": round(statistics.median(totals), 4),
               "minimum_proxy": min(totals), "maximum_proxy": max(totals),
               "category_means": category_means,
               "b_exact_gold": sum(row["b_exact_gold"] for row in rounds)}
    target = BASE / "aggregate-summary.json"
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("cases", "completed", "failed", "mean_proxy",
                                                   "median_proxy", "minimum_proxy", "maximum_proxy",
                                                   "category_means", "b_exact_gold")}, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    summarize()
