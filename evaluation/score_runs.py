"""读取真实运行结果与人工复核记录，计算三轮本地诊断分。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from .benchmark import ROOT, EXPERIMENTS, CORE, FACTS, ROUNDS, case_payload
from .scoring import aggregate, score_chat, score_retrieval, score_survey, score_idea, score_metric, score_experiment


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def citation_coverage(markdown: str, required: list[str]) -> tuple[int, list[str]]:
    """与官方公开口径一致：正文标记和文末文件名同时存在即覆盖。"""
    if "## 参考文献" not in markdown:
        return 0, []
    body, refs = markdown.split("## 参考文献", 1)
    used = set(re.findall(r"\[(\d+)(?:,\s*p\.\s*\d+)?\]", body))
    rows = dict(re.findall(r"^\[(\d+)\]\s+(.+\.pdf)\s*$", refs, re.M))
    names = {rows[number] for number in used if number in rows}
    covered = sorted(names & set(required))
    return len(covered), covered


def receipt_for(out: Path, team: str, case_id: str) -> tuple[Path, dict]:
    pointer = load(out / "data" / team / "work" / "receipts" / f"{case_id}.json")
    path = out / pointer["receipt"]
    return path, load(path)


def experiment_checks(out: Path, team: str, case_id: str, result: dict) -> dict:
    """核对本机可见产物；平台 syscall 证据不在本机评分范围。"""
    if result.get("status") != "completed":
        return {"pipeline": 0.0, "metrics": 0.0, "chart": 0.0, "total": 0.0,
                "checkpoints": [False] * 8, "chart_checks": [False] * 6,
                "metric_scores": [0.0] * 3, "status": result.get("status")}
    receipt_path, receipt = receipt_for(out, team, case_id)
    work = receipt_path.parent
    experiment = work / "experiment"
    command = load(experiment / "command.json") if (experiment / "command.json").is_file() else {}
    metrics_json = load(experiment / "metrics.json") if (experiment / "metrics.json").is_file() else {}
    events = []
    stdout = work / "experiment.stdout.jsonl"
    if stdout.is_file():
        for line in stdout.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    event_names = {row.get("event") or row.get("stage") or row.get("type") for row in events}
    source = experiment / "data" / "source_data.csv"
    proof = receipt.get("proof") or {}
    checkpoints = [
        result.get("status") == "completed" and proof.get("submission") == result,
        command.get("case_id") == case_id and bool(command.get("run_id")),
        source.is_file() and source.stat().st_size > 0,
        (experiment / "metrics.json").is_file() and len(result.get("metrics", [])) == 3,
        "data_preprocess" in event_names,
        "loop_iteration" in event_names,
        "result_generated" in event_names,
        (work / "experiment-proof.json").is_file() and
        proof.get("script_sha256") == hashlib.sha256((EXPERIMENTS / "experiment.py").read_bytes()).hexdigest(),
    ]
    reference = load(EXPERIMENTS / "references.json")["cases"][case_id]["metrics"]
    metric_scores = []
    for metric, expected in zip(result["metrics"], reference):
        verified = metric["name"] == expected["name"] and metric["unit"] == expected["unit"]
        verified = verified and metrics_json.get("run_id") == command.get("run_id")
        verified = verified and any(row.get("name") == metric["name"] and
                                    row.get("unit") == metric["unit"] and
                                    row.get("value") == metric.get("observed")
                                    for row in metrics_json.get("metrics", []))
        metric_scores.append(score_metric(metric.get("observed"), expected["reference_value"],
                                          expected["relative_tolerance"], expected["absolute_tolerance"],
                                          verified=verified))
    figures = result.get("figures", [])
    figure = figures[0] if figures else {}
    image = out / figure.get("path", "") if figure else None
    file_ok = False
    if image and image.is_file():
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        file_ok = digest == proof.get("figure_hashes", {}).get(figure["path"])
        try:
            from PIL import Image
            with Image.open(image) as opened:
                opened.verify()
        except Exception:
            file_ok = False
    # 单位检查仅检验图轴存在括号、斜杠或百分号等显式单位记号。
    axis_text = figure.get("x_label", "") + " " + figure.get("y_label", "")
    chart_checks = [file_ok, bool(figure.get("x_label", "").strip()),
                    bool(figure.get("y_label", "").strip()),
                    bool(re.search(r"[%()/]|秒|分钟|分数|延迟|吞吐", axis_text)),
                    bool(figure.get("legend", "").strip()), bool(figure.get("caption", "").strip())]
    if not file_ok:
        chart_checks = [False] * 6
    points = score_experiment(checkpoints, metric_scores, chart_checks)
    return {**points, "checkpoints": checkpoints, "chart_checks": chart_checks,
            "metric_scores": metric_scores, "status": result["status"],
            "observed_metrics": [{"name": row["name"], "value": row["observed"], "unit": row["unit"]}
                                 for row in result["metrics"]]}


def score_round(number: int) -> dict:
    spec = next(row for row in ROUNDS if row["number"] == number)
    team = f"eval_r{number}"
    out = ROOT / "output" / "evaluation" / f"round-{number}"
    status = load(out / "run-status.json")
    judgments_path = ROOT / "evaluation" / "judgments" / f"round-{number}.json"
    judgments = load(judgments_path) if judgments_path.is_file() else {}
    categories = {}
    cases = {}
    for letter in "ABCDE":
        payload = case_payload(letter, spec)
        case_id = payload["case_id"]
        row = status["cases"].get(case_id)
        if not row or row["state"] not in {"completed", "failed"}:
            raise ValueError(f"case still pending: {case_id}")
        if row["state"] == "failed":
            categories[letter] = [0.0]
            cases[case_id] = {"score": 0.0, "state": "failed", "seconds": row["seconds"],
                              "error_type": row.get("error_type")}
            continue
        result = load(out / row["result_path"])
        if letter == "A":
            score = score_chat(result)
            detail = {}
        elif letter == "B":
            returned = [paper["filename"] for paper in result["papers"]]
            score = score_retrieval(returned, spec["gold_b"])
            detail = {"returned": returned, "gold": spec["gold_b"]}
        elif letter == "C":
            judgment = judgments.get(case_id)
            if judgment is None:
                raise ValueError(f"manual fact judgment missing: {case_id}")
            valid, covered = citation_coverage(result["markdown"], CORE)
            correct = judgment["correct_fact_ids"]
            if not set(correct) <= {key for key, _ in FACTS}:
                raise ValueError("unknown fact id in manual judgment")
            detail = score_survey(len(correct), len(FACTS), len(judgment["severe_errors"]), valid)
            detail.update({"correct_fact_ids": correct, "severe_errors": judgment["severe_errors"],
                           "covered_references": covered})
            score = detail["total"]
        elif letter == "D":
            detail = experiment_checks(out, team, case_id, result)
            score = detail["total"]
        else:
            judgment = judgments.get(case_id)
            if judgment is None:
                raise ValueError(f"manual Idea judgment missing: {case_id}")
            covered = set(judgment["covered"])
            score = score_idea(covered)
            detail = {"covered": judgment["covered"], "evidence": judgment["evidence"],
                      "missing": sorted(set(["motivation", "route", "experiment", "benchmark", "metrics",
                                              "bottleneck", "complement", "feasibility"]) - covered)}
        categories[letter] = [score]
        cases[case_id] = {"score": score, "state": "completed", "seconds": row["seconds"], **detail}
    overall = aggregate(categories, mode="diagnostic")
    scored = {"round": number, "theme": spec["theme"], "source_rule": "research-assistant-objective-v2",
              "label": "本地五题诊断分，非官方榜单分", "scores": overall, "cases": cases}
    (out / "local-score.json").write_text(json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8")
    return scored


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, choices=[1, 2, 3], required=True)
    args = parser.parse_args()
    scored = score_round(args.round)
    print(json.dumps({"round": args.round, "total": scored["scores"]["total"],
                      "category_scores": scored["scores"]["category_scores"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
