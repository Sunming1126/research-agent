"""汇总十轮原始诊断、逐轮决策与落盘文件校验。"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from .benchmark import ROOT
from .ten_round_benchmark import ROUNDS


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def collect() -> dict:
    rows = []
    cases_completed = cases_failed = tokens = 0
    durations = []
    file_checks = {"json": 0, "markdown_equal": 0, "figure_exists": 0,
                   "metrics_three": 0}
    for spec in ROUNDS:
        number = spec["number"]
        out = ROOT / "output" / "ten-rounds" / f"round-{number:02d}"
        score = read_json(out / "proxy-score.json")
        decision = read_json(out / "optimization.json")
        status = read_json(out / "run-status.json")
        if set(status["cases"]) != set(score["cases"]) or len(status["cases"]) != 5:
            raise ValueError(f"round {number}: case set mismatch")
        team = f"ten_r{number:02d}"
        round_tokens = round_seconds = 0
        for case_id, case in status["cases"].items():
            round_seconds += case["seconds"]
            durations.append(case["seconds"])
            if case["state"] != "completed":
                cases_failed += 1
                continue
            cases_completed += 1
            result = read_json(out / case["result_path"])
            if result["case_id"] != case_id:
                raise ValueError(f"round {number}: wrong case_id in {case_id}")
            file_checks["json"] += 1
            if case_id.startswith(("C-", "E-")):
                markdown = (out / result["path"]).read_text(encoding="utf-8")
                if markdown != result["markdown"]:
                    raise ValueError(f"round {number}: Markdown differs in {case_id}")
                file_checks["markdown_equal"] += 1
            if case_id.startswith("D-"):
                if len(result["metrics"]) != 3:
                    raise ValueError(f"round {number}: metric count in {case_id}")
                file_checks["metrics_three"] += 1
                for chart in result["figures"]:
                    if not (out / chart["path"]).is_file():
                        raise ValueError(f"round {number}: missing chart in {case_id}")
                    file_checks["figure_exists"] += 1
            pointer = out / "data" / team / "work" / "receipts" / f"{case_id}.json"
            if pointer.is_file():
                receipt = read_json(out / read_json(pointer)["receipt"])
                round_tokens += sum(item.get("tokens", 0) for item in receipt.get("usage", []))
        tokens += round_tokens
        rows.append({"number": number, "domain": score["domain"],
                     "score": score["total_proxy"], "categories": score["category_scores"],
                     "seconds": round(round_seconds, 2), "tokens": round_tokens,
                     "completed": sum(case["state"] == "completed" for case in status["cases"].values()),
                     "finding": decision["finding"], "change": decision["change"],
                     "verification": decision["verification"]})
    scores = [row["score"] for row in rows]
    return {"label": "本地自动代理诊断，非官方榜分；C/E 未经逐句人工事实复核",
            "rounds": rows, "mean_proxy": round(statistics.mean(scores), 4),
            "median_proxy": round(statistics.median(scores), 4),
            "range_proxy": [min(scores), max(scores)],
            "cases_completed": cases_completed, "cases_failed": cases_failed,
            "total_model_tokens": tokens,
            "total_case_seconds": round(sum(row["seconds"] for row in rows), 2),
            "max_case_seconds": max(durations), "file_checks": file_checks}


def write_report(data: dict) -> None:
    lines = ["# 百篇论文库十轮跨领域测试与逐轮优化总结", "",
             "日期：2026-10-18。测试使用已下载并登记的 100 篇论文、公开 D 类实验和用户配置的模型接口。",
             "**所有分数均为本地自动代理诊断分，不是正式榜分；C/E 的逐句事实准确性和专家质量未获人工复核。**",
             "", "## 总览", "",
             f"- 十轮原始分均值 **{data['mean_proxy']:.2f}/100**，中位数 {data['median_proxy']:.2f}，区间 {data['range_proxy'][0]:.2f}–{data['range_proxy'][1]:.2f}。",
             f"- 50 题中 {data['cases_completed']} 题成功落盘，{data['cases_failed']} 题失败；失败题保留原始分，后续独立复测不覆盖。",
             f"- 全部原始轮次累计模型 Token {data['total_model_tokens']:,}；逐题用时累计 {data['total_case_seconds']:.2f} 秒，最慢单题 {data['max_case_seconds']:.2f} 秒。",
             f"- 回读：{data['file_checks']['json']} 个 JSON case_id 一致，{data['file_checks']['markdown_equal']} 份 C/E Markdown 与 JSON 逐字一致，{data['file_checks']['metrics_three']} 个 D 结果恰好三项指标，{data['file_checks']['figure_exists']} 张图实际存在。",
             "", "## 十轮原始成绩", "",
             "| 轮次 | 领域 | 总分 | A | B | C | D | E | 完成题数 | 耗时秒 | 模型 Token |",
             "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in data["rounds"]:
        c = row["categories"]
        lines.append(f"| {row['number']-5} | {row['domain']} | {row['score']:.2f} | "
                     f"{c['A']:.2f} | {c['B']:.2f} | {c['C']:.2f} | {c['D']:.2f} | "
                     f"{c['E']:.2f} | {row['completed']}/5 | {row['seconds']:.2f} | {row['tokens']:,} |")
    lines += ["", "## 逐轮发现与优化", ""]
    for row in data["rounds"]:
        lines += [f"### 第 {row['number']-5} 轮：{row['domain']}", "",
                  f"- 发现：{row['finding']}", f"- 调整：{row['change']}",
                  f"- 验证：{row['verification']}", ""]
    lines += ["## 结论与边界", "",
              "- B 类的主要风险是复合主题漏召、模型筛选误报和查询规划超时。本次加入原题与改写双通道、独立子题召回、最佳分数合并、查询重试与保守弱尾过滤。第 5 轮的规划失败仍计入原始 0 分。",
              "- C/E 的自动代理分仅检查引用、原文摘录、文件一致性和方案要素，不能证明每个结论正确。正式提交前仍需对长文做逐句抽检。",
              "- D 类均执行了公开实验并保留真实运行产物；吞吐和耗时会随机器负载变化，不能把本地分数视作平台成绩。",
              "- 这组题由 100 篇公开论文构成，B 检索缓存覆盖每篇前八页；正式平台论文、资源限制与评审题型分布可能不同。十轮结果不能推断正式榜分。",
              "- 最终轮没有证据表明应继续调低阈值；当前配置冻结，下一步重点是事实抽检、容器资源与平台接口验证。",
              "", "## 最终核验", "",
              "- 2026-10-18 完整回归：51 项测试通过，其中 2 项按环境条件跳过。",
              "- 论文库清单复核：100 个 PDF 哈希校验通过，覆盖 10 个领域，合计 266,746,099 字节。",
              "- 第 5 轮失败的 B-10 在独立目录复测返回三篇目标，诊断 15/15；该次查询规划正常返回，不能据此声称截断重试分支已在真实网关触发。原始轮次仍为 85/100。",
              "", "## 可复查文件", "",
              "- `output/ten-rounds/round-06` 至 `round-15`：各轮原始结果、分数、诊断、优化决策、调用凭证与产物。",
              "- `output/ten-rounds/final-summary.json`：本报告的机器可读汇总。",
              "- `evaluation/ten_round_benchmark.py`：运行前冻结的十轮题目与目标文件名。",
              "- `evaluation/score_ten_rounds.py`：自动代理评分口径。", ""]
    path = ROOT / "docs" / "2026-10-18-十轮跨领域测试与逐轮优化.md"
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    data = collect()
    path = ROOT / "output" / "ten-rounds" / "final-summary.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(data)
    print(json.dumps({key: data[key] for key in ("mean_proxy", "cases_completed",
                                                  "cases_failed", "total_model_tokens", "file_checks")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
