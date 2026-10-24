"""运行一轮 v3 形状的本地全流程仿真，并生成可复核的代理评分。

本脚本模拟平台的 11 题派发、逐题处理、结果回读和提交前检查。
本地没有官方裁判服务，因此 C 类事实和 D 类 layered-d-v3 服务器证据
使用明确标注的本地代理分；B、A 以及格式、引用、文件完整性检查是可复核的。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PAPERS = ROOT / "local-workspace" / "papers"
EXPERIMENTS = ROOT / "科研助手赛题材料" / "research-experiments"
TEAM = "v3_sim_20261021"
OUT = ROOT / "output" / "v3-official-simulation"
DISPATCH_ROOT = ROOT / "data" / TEAM


def load_local_credentials() -> None:
    """把本地测试密钥读入进程；任何输出文件都不会写入密钥。"""
    dotenv = ROOT / ".env.local"
    if not dotenv.is_file():
        return
    allowed = {"PLATFORM_LLM_URL", "PLATFORM_API_KEY", "AGENT_MODEL_NAME", "PLATFORM_LLM_MODEL",
               "RESEARCH_LLM_THINKING"}
    for raw in dotenv.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name in allowed:
            os.environ.setdefault(name, value.strip().strip('"').strip("'"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case_payload(letter: str, spec: dict[str, Any]) -> dict[str, Any]:
    """复用冻结公开题材，按正式 v3 题号构造 CaseContext。"""
    from evaluation.benchmark import case_payload as legacy_case_payload

    if letter != "D":
        return legacy_case_payload(letter, spec)
    refs = json.loads((EXPERIMENTS / "references.json").read_text(encoding="utf-8"))
    metrics = refs["cases"][spec["d"]]["metrics"]
    pairs = "; ".join(f"name={row['name']}, unit={row['unit']}" for row in metrics)
    case_id = spec["d"]
    return {
        "case_id": case_id,
        "function": "Reproduction",
        "title": spec["theme"],
        "input": f"运行授权公开小实验 {case_id}，固定 seed=42，逐项报告三个实测指标：{pairs}。提交真实图表。",
        "materials": [case_id + ".json"],
        "constraints": ["仅使用本题授权材料", "指标名称和单位必须逐项匹配", "无法核验时不得猜测"],
        "expected_output": "真实指标、图表和实验状态",
        "output_schema_ref": "Reproduction",
    }


def build_cases() -> list[dict[str, Any]]:
    """构造官方 v3 的 1/3/2/3/2 题量。"""
    from evaluation.benchmark import ROUNDS

    by_number = {row["number"]: row for row in ROUNDS}
    # C-01/C-03、D-01/D-02/D-05、E-01/E-02 是新版公开规则中的固定题。
    wanted = [("A", 1), ("B", 1), ("B", 2), ("B", 3),
              ("C", 1), ("C", 3), ("D", 1), ("D", 2), ("D", 3),
              ("E", 1), ("E", 2)]
    cases = []
    for letter, number in wanted:
        spec = by_number[number]
        payload = case_payload(letter, spec)
        if letter == "D" and payload["case_id"] == "D-03":
            # 正式题固定为 D-05；沿用 D-05 的冻结指标，只替换题目主题。
            payload = case_payload("D", {**spec, "d": "D-05"})
        cases.append(payload)
    return cases


def write_dispatch(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """写入平台格式的 dispatch 和 assigned 题目，并计算题目摘要。"""
    assigned = DISPATCH_ROOT / "assigned"
    assigned.mkdir(parents=True, exist_ok=True)
    entries = []
    ids = []
    for case in cases:
        case_id = case["case_id"]
        target = assigned / f"{case_id}.json"
        target.write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
        ids.append(case_id)
        row = {"case_id": case_id, "function": case["function"], "title": case["title"],
               "question_file": f"data/{TEAM}/assigned/{case_id}.json", "question_sha256": sha256(target)}
        if case_id.startswith("D-"):
            row["difficulty"] = {"D-01": "easy", "D-02": "medium", "D-05": "hard"}[case_id]
        entries.append(row)
    dispatch = {
        "dispatch_id": "local-v3-simulation-20261021",
        "team": TEAM,
        "schema_version": 3,
        "rules_version": "research-assistant-objective-v3",
        "cases_per_category": {"A": 1, "B": 3, "C": 2, "D": 3, "E": 2},
        "case_ids": ids,
        "cases": entries,
    }
    (DISPATCH_ROOT / "dispatch.json").write_text(json.dumps(dispatch, ensure_ascii=False, indent=2), encoding="utf-8")
    return dispatch


def run_agent(dispatch: dict[str, Any], case_timeout: int = 180) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """执行逐题路由并运行本地 precheck。"""
    sys.path.insert(0, str(ROOT / "src"))
    from research_agent.core.config import Settings
    from research_agent.core.engine import ResearchAgentEngine

    settings = Settings(workspace=ROOT, output=OUT, team=TEAM,
                        material_roots=[PAPERS, EXPERIMENTS / "publicdata"],
                        experiment_mode="local", local_experiment_root=EXPERIMENTS,
                        local_python=sys.executable, llm_calls=30, llm_tokens=40000,
                        case_timeout=case_timeout)
    engine = ResearchAgentEngine(settings)
    started = time.monotonic()
    summary = engine.solve_sync({"dispatch": True})
    summary["seconds"] = round(time.monotonic() - started, 2)
    precheck = None
    try:
        precheck = engine.precheck()
    except Exception as exc:  # 失败也要写出诊断，便于复盘
        precheck = {"valid": False, "error_type": type(exc).__name__, "message": str(exc)[:500]}
    return summary, precheck


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def citation_coverage(markdown: str, required: list[str]) -> tuple[int, list[str]]:
    """按新版引用覆盖口径检查正文标记和文末文件名。"""
    if "## 参考文献" not in markdown:
        return 0, []
    body, refs = markdown.split("## 参考文献", 1)
    used = set(re.findall(r"\[(\d+)(?:,\s*p\.\s*\d+)?\]", body))
    rows = dict(re.findall(r"^\[(\d+)\]\s+(.+\.pdf)\s*$", refs, re.M))
    names = {rows[number] for number in used if number in rows}
    covered = sorted(names & set(required))
    return len(covered), covered


def keyword_fact_proxy(markdown: str) -> list[str]:
    """为没有官方裁判的 C-01 提供可复核的保守关键词筛查。"""
    body = markdown.split("## 参考文献", 1)[0].casefold()
    checks = {
        "dpr-1": ("dpr", "编码器"),
        "dpr-2": ("bm25", "top-k"),
        "rag-1": ("参数化", "非参数化"),
        "rag-2": ("rag-sequence", "rag-token"),
        "fid-1": ("fid", "解码器"),
        "fid-2": ("natural questions", "triviaqa"),
        "retro-1": ("retro", "检索"),
        "retro-2": ("2 万亿", "参数效率"),
        "selfrag-1": ("self-rag", "按需"),
        "selfrag-2": ("反思 token", "证据"),
    }
    return [key for key, terms in checks.items() if all(term.casefold() in body for term in terms)]


def score_case(case_id: str, result: dict[str, Any] | None, out: Path) -> dict[str, Any]:
    """按官方分值上限计算本地可核验分，并记录评分来源。"""
    from evaluation.benchmark import ROUNDS
    from evaluation.scoring import score_chat, score_retrieval
    from evaluation.score_runs import experiment_checks

    if result is None:
        return {"score": 0.0, "state": "missing", "reason": "result file missing"}
    letter = case_id[0]
    if letter == "A":
        return {"score": score_chat(result), "state": "completed", "mode": "schema_objective"}
    if letter == "B":
        number = int(case_id.split("-")[1])
        spec = next(row for row in ROUNDS if row["number"] == number)
        returned = [row["filename"] for row in result.get("papers", [])]
        score = score_retrieval(returned, spec["gold_b"])
        return {"score": score, "state": "completed", "mode": "gold_file_set", "returned": returned,
                "gold": spec["gold_b"]}
    if letter == "C":
        from evaluation.current_review import review
        return review(case_id, result, out, TEAM)
    if letter == "E":
        from evaluation.current_review import review
        return review(case_id, result, out, TEAM)
    if letter == "D":
        try:
            detail = experiment_checks(out, TEAM, case_id, result)
        except Exception as exc:
            return {"score": 0.0, "state": result.get("status"), "mode": "local_d_proxy_error",
                    "error_type": type(exc).__name__, "message": str(exc)[:300]}
        detail.update({"score": detail.get("total", 0.0), "mode": "legacy_local_evidence_proxy"})
        return detail
    raise ValueError("unknown case category")


def score_round(dispatch: dict[str, Any], run_summary: dict[str, Any], precheck: dict[str, Any] | None,
                report_name="simulation-score.json") -> dict[str, Any]:
    """按 1/3/2/3/2 类别平均计算 100 分形状的本地代理分。"""
    category_ids = {letter: [case_id for case_id in dispatch["case_ids"] if case_id.startswith(letter + "-")]
                    for letter in "ABCDE"}
    scored_cases = {}
    for case_id in dispatch["case_ids"]:
        path = OUT / "data" / TEAM / f"{case_id}.json"
        try:
            scored_cases[case_id] = score_case(case_id, load_json(path) if path.is_file() else None, OUT)
        except Exception as exc:
            # 裁判不可用时明确记零并标记失败，不能借用旧判断补分。
            scored_cases[case_id] = {"score": 0.0, "mode": "review_failed", "error_type": type(exc).__name__,
                                     "message": str(exc)[:300]}
    category_scores = {}
    for letter, case_ids in category_ids.items():
        category_scores[letter] = round(sum(scored_cases[cid]["score"] for cid in case_ids) / len(case_ids), 4)
    total = round(sum(category_scores.values()), 4)
    report = {
        "simulation": "research-assistant-objective-v3",
        "label": "本地 11 题全流程仿真代理分，非官方榜单分",
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "team": TEAM,
        "dispatch": {"dispatch_id": dispatch["dispatch_id"], "case_ids": dispatch["case_ids"],
                     "cases_per_category": dispatch["cases_per_category"]},
        "run": run_summary,
        "precheck": precheck,
        "category_scores": category_scores,
        "automatic_score_proxy": total,
        "maximum": 100,
        "cases": scored_cases,
        "scoring_notes": [
            "A/B 使用字段和冻结文件集合的可复核客观公式。",
            "C/E 仅审阅本次生成正文并保存原句和哈希；模型裁判为本地代理，不能替代官方裁判。",
            "D 使用本地公开 runner 的旧证据量表映射到 40 分上限，官方 layered-d-v3 可信执行分仍以平台为准。",
            "同一模型生成与审阅存在偏差，官方专家复核和 Token 维度不在本地代理分内。",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / report_name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    global TEAM, OUT, DISPATCH_ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true", help="只生成 v3 dispatch，不运行模型")
    parser.add_argument("--run-id", help="独立轮次标识；每轮分离输入、结果和证据")
    parser.add_argument("--score-only", action="store_true", help="用当前裁判复核已存在结果，不覆盖原始评分")
    parser.add_argument("--case-timeout", type=int, default=180, help="单题时间预算（秒），默认与当前 Agent 配置一致")
    args = parser.parse_args()
    if args.case_timeout <= 0:
        parser.error("--case-timeout must be positive")
    load_local_credentials()
    sys.path.insert(0, str(ROOT / "src"))
    if args.run_id:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.run_id):
            raise ValueError("invalid run id")
        TEAM = args.run_id
        OUT = ROOT / "output" / TEAM
        DISPATCH_ROOT = ROOT / "data" / TEAM
    if args.score_only:
        old = load_json(OUT / "simulation-score.json")
        report = score_round(load_json(DISPATCH_ROOT / "dispatch.json"), old["run"], old["precheck"], "current-score.json")
        print(json.dumps({"proxy": report["automatic_score_proxy"], "categories": report["category_scores"]}))
        return
    if OUT.exists() and (OUT / "simulation-score.json").exists():
        raise ValueError("round already exists; choose a new run-id to preserve original results")
    # 每轮保留实际源码及摘要，避免后续优化覆盖已测版本。
    snapshot = OUT / "source-snapshot"
    manifest = {}
    for source in sorted((ROOT / "src").rglob("*.py")):
        relative = source.relative_to(ROOT)
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        manifest[relative.as_posix()] = sha256(source)
    (OUT / "source-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    cases = build_cases()
    dispatch = write_dispatch(cases)
    if args.prepare_only:
        print(json.dumps({"team": TEAM, "cases": dispatch["case_ids"]}, ensure_ascii=False))
        return
    # 记录实测预算，避免将历史宽松计时与当前限制混为一谈。
    (OUT / "run-settings.json").write_text(json.dumps({
        "case_timeout_seconds": args.case_timeout, "llm_calls_per_case": 30,
        "llm_tokens_per_case": 40000, "experiment_mode": "local",
        "python_version": sys.version, "experiment_root": str(EXPERIMENTS),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    run_summary, precheck = run_agent(dispatch, args.case_timeout)
    print(json.dumps({"event": "agent_finished", "complete": run_summary.get("complete"), "precheck": precheck}, ensure_ascii=False), flush=True)
    report = score_round(dispatch, run_summary, precheck)
    print(json.dumps({"complete": run_summary.get("complete"), "precheck": precheck,
                      "automatic_score_proxy": report["automatic_score_proxy"],
                      "category_scores": report["category_scores"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
