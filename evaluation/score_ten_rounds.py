"""每轮自动生成结构与证据代理分；不冒充专家事实复核。"""

from __future__ import annotations

import argparse
import json
import re

from .benchmark import ROOT
from .score_runs import citation_coverage, experiment_checks
from .scoring import score_chat, score_idea, score_retrieval
from .ten_round_benchmark import ROUNDS, case_payload


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _evidence(out, team, case_id):
    pointer = _load(out / "data" / team / "work" / "receipts" / f"{case_id}.json")
    receipt = out / pointer["receipt"]
    return _load(receipt.parent / "evidence.json")


def _idea_proxy(markdown, files):
    """只评价要点是否可见；事实正确性必须另做论文对照。"""
    headings = markdown.lower()
    covered = set()
    patterns = {
        "motivation": r"动机|研究问题|模拟观察",
        "route": r"技术路线|方法|框架|模型",
        "experiment": r"消融|对照实验|实验设计",
        "benchmark": r"基准|数据集|案例|实验系统",
        "metrics": r"量化指标|评价指标|指标",
        "bottleneck": r"局限|瓶颈|风险|失败边界",
        "feasibility": r"可行性|实现条件|资源预算|可操作性|适配性待验证",
    }
    for key, pattern in patterns.items():
        if re.search(pattern, headings):
            covered.add(key)
    citations, _ = citation_coverage(markdown, files)
    multi_paper_route = any(len(set(re.findall(r"\[(\d+)(?:,\s*p\.\d+)?\]", paragraph))) >= 4
                            and re.search(r"路线|框架|层|工具", paragraph)
                            for paragraph in markdown.split("\n\n"))
    if citations == 5 and (re.search(r"互补|分工|整合", headings) or multi_paper_route):
        covered.add("complement")
    return covered


def score_round(number: int) -> dict:
    spec = next(row for row in ROUNDS if row["number"] == number)
    out = ROOT / "output" / "ten-rounds" / f"round-{number:02d}"
    team = f"ten_r{number:02d}"
    status = _load(out / "run-status.json")
    cases, category_scores = {}, {}
    for letter in "ABCDE":
        case_id = case_payload(letter, spec)["case_id"]
        row = status["cases"].get(case_id)
        if row is None:
            raise ValueError(f"unfinished case: {case_id}")
        if row["state"] != "completed":
            category_scores[letter] = 0.0
            cases[case_id] = {"score": 0.0, "state": row["state"],
                              "error_type": row.get("error_type"), "seconds": row["seconds"]}
            continue
        result = _load(out / row["result_path"])
        detail = {}
        if letter == "A":
            score = score_chat(result)
        elif letter == "B":
            returned = [paper["filename"] for paper in result["papers"]]
            score = score_retrieval(returned, spec["gold"])
            detail = {"returned": returned, "gold": spec["gold"]}
        elif letter == "C":
            ledger = _evidence(out, team, case_id)["claims"]
            counts = {name: sum(claim["filename"] == name for claim in ledger) for name in spec["files"]}
            # 五篇各有至少两条经原文摘录核验的事实，每篇代理事实分 3 分。
            fact_proxy = 3.0 * sum(count >= 2 for count in counts.values())
            valid_refs, covered = citation_coverage(result["markdown"], spec["files"])
            citation_proxy = 2.0 * valid_refs
            score = fact_proxy + citation_proxy
            detail = {"verified_claims_by_paper": counts, "fact_proxy": fact_proxy,
                      "citation_proxy": citation_proxy, "covered_references": covered,
                      "factually_reviewed": False}
        elif letter == "D":
            detail = experiment_checks(out, team, case_id, result)
            score = detail["total"]
        else:
            covered = _idea_proxy(result["markdown"], spec["files"])
            score = score_idea(covered)
            detail = {"visible_criteria": sorted(covered), "factually_reviewed": False,
                      "audit": _evidence(out, team, case_id)["audit"]}
        category_scores[letter] = round(score, 4)
        cases[case_id] = {"state": "completed", "score": round(score, 4),
                          "seconds": row["seconds"], **detail}
    weaknesses = [f"{letter}={score:.2f}" for letter, score in category_scores.items()
                  if score < {"A": 5, "B": 15, "C": 25, "D": 40, "E": 15}[letter]]
    scored = {"round": number, "domain": spec["domain"],
              "proxy_version": 2,
              "label": "本地五题自动代理诊断分；C/E 不含人工事实复核，非官方榜分",
              "total_proxy": round(sum(category_scores.values()), 4),
              "category_scores": category_scores, "cases": cases,
              "weaknesses": weaknesses}
    (out / "proxy-score.json").write_text(json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8")
    write_summary(scored, out)
    return scored


def write_summary(scored, out):
    """每轮立即落盘总结；优化决策若稍后补入，可重新计分刷新文档。"""
    number = scored["round"]
    score = scored["category_scores"]
    lines = [f"# 第 {number-5}/10 轮：{scored['domain']}", "",
             "**口径：本地五题自动代理诊断分，C/E 未做人工事实复核，不是正式榜分。**", "",
             f"总分：**{scored['total_proxy']:.2f}/100**。", "",
             "| A | B | C | D | E |", "| ---: | ---: | ---: | ---: | ---: |",
             f"| {score['A']:.2f} | {score['B']:.2f} | {score['C']:.2f} | {score['D']:.2f} | {score['E']:.2f} |",
             "", "## 自动诊断", ""]
    for case_id, row in scored["cases"].items():
        state = row["state"]
        lines.append(f"- {case_id}：{state}，{row['score']:.2f} 分，{row['seconds']:.2f} 秒。")
    lines += ["", "主要未达满分项：" + ("、".join(scored["weaknesses"]) if scored["weaknesses"] else "无"),
              "", "C 分数衡量每篇是否有至少两条原文核验事实及五篇引用；E 分数衡量八个要点是否可见。"
              "两者不代表逐句事实正确或专家质量。", "", "## 本轮复盘与下一轮改动", ""]
    decision = out / "optimization.json"
    if decision.is_file():
        optimization = _load(decision)
        lines += [f"- 诊断：{optimization['finding']}",
                  f"- 改动：{optimization['change']}",
                  f"- 验证：{optimization['verification']}",
                  f"- 下一轮关注：{optimization['next_check']}"]
    else:
        lines.append("本轮人工核对与代码改动尚未记录。")
    (out / "round-review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, choices=range(6, 16), required=True)
    args = parser.parse_args()
    result = score_round(args.round)
    print(json.dumps({"round": args.round, "total_proxy": result["total_proxy"],
                      "category_scores": result["category_scores"],
                      "weaknesses": result["weaknesses"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
