"""使用同一套百篇 PDF 缓存依次运行两轮 A–E 诊断。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .benchmark import EXPERIMENTS, ROOT
from .library_benchmark import LIBRARY, LIBRARY_ROUNDS, case_payload
from .run_rounds import load_local_credentials


def settings_for(round_number: int, *, retest: bool = False, followup: bool = False,
                 followup2: bool = False, followup3: bool = False):
    sys.path.insert(0, str(ROOT / "src"))
    from research_agent.core.config import Settings

    if sum((retest, followup, followup2, followup3)) > 1:
        raise ValueError("choose one evaluation revision")
    suffix = "-followup3" if followup3 else "-followup2" if followup2 else "-followup" if followup else "-retest" if retest else ""
    team_suffix = "_followup3" if followup3 else "_followup2" if followup2 else "_followup" if followup else "_retest" if retest else ""
    out = ROOT / "output" / "library-evaluation" / f"round-{round_number}{suffix}"
    out.mkdir(parents=True, exist_ok=True)
    return Settings(workspace=ROOT, output=out, team=f"lib_eval_r{round_number}{team_suffix}",
                    material_roots=[LIBRARY, EXPERIMENTS / "publicdata"],
                    experiment_mode="local", local_experiment_root=EXPERIMENTS,
                    local_python=sys.executable, llm_calls=30, llm_tokens=40000,
                    case_timeout=600)


def prepare_index(round_number: int, *, retest: bool = False, followup: bool = False,
                  followup2: bool = False, followup3: bool = False) -> dict:
    """为 B 类候选库建立前八页缓存；C/E 正文仍读取指定论文全文。"""
    from research_agent.rag.materials import DocumentIndex

    spec = next(row for row in LIBRARY_ROUNDS if row["number"] == round_number)
    settings = settings_for(round_number, retest=retest, followup=followup,
                            followup2=followup2, followup3=followup3)
    files = case_payload("B", spec)["materials"]
    cache = settings.output / "data" / settings.team / "work" / "document-cache"
    started = time.monotonic()
    index = DocumentIndex(settings, cache)
    chunks = index.build(files, page_limit=8)
    result = {"pdfs": len(index.materials), "chunks": len(chunks),
              "seconds": round(time.monotonic() - started, 2)}
    (settings.output / "index-status.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def run_round(round_number: int, *, only: set[str] | None = None, retest: bool = False,
              followup: bool = False, followup2: bool = False, followup3: bool = False) -> dict:
    from research_agent.core.engine import ResearchAgentEngine

    load_local_credentials()
    spec = next(row for row in LIBRARY_ROUNDS if row["number"] == round_number)
    settings = settings_for(round_number, retest=retest, followup=followup,
                            followup2=followup2, followup3=followup3)
    engine = ResearchAgentEngine(settings)
    run_file = settings.output / "run-status.json"
    summary = json.loads(run_file.read_text(encoding="utf-8")) if run_file.is_file() else {
        "round": round_number, "retest": retest, "followup": followup,
        "followup2": followup2, "followup3": followup3, "theme": spec["theme"],
        "model": os.getenv("AGENT_MODEL_NAME", ""), "cases": {}}
    for letter in "ABDCE":
        if only and letter not in only:
            continue
        payload = case_payload(letter, spec)
        case_id = payload["case_id"]
        if summary["cases"].get(case_id, {}).get("state") == "completed":
            continue
        started = time.monotonic()
        print(f"START library_round={round_number} case={case_id}", flush=True)
        try:
            result = engine.solve_sync(payload)
            summary["cases"][case_id] = {"state": "completed", "seconds": round(time.monotonic()-started, 2),
                                          "result_path": f"data/{settings.team}/{case_id}.json"}
            print(f"DONE library_round={round_number} case={case_id} status={result.get('status', 'written')} seconds={summary['cases'][case_id]['seconds']}", flush=True)
        except Exception as exc:
            # 模型网关的异常可能含请求参数，控制台只打印类型；细节留在本题内部诊断文件。
            summary["cases"][case_id] = {"state": "failed", "seconds": round(time.monotonic()-started, 2),
                                          "error_type": type(exc).__name__}
            print(f"FAIL library_round={round_number} case={case_id} type={type(exc).__name__}", flush=True)
        run_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, choices=[4, 5], required=True)
    parser.add_argument("--prepare-index", action="store_true")
    parser.add_argument("--retest", action="store_true", help="使用独立目录复测，保留原始轮次结果")
    parser.add_argument("--followup", action="store_true", help="独立保存后续单题优化实验")
    parser.add_argument("--followup2", action="store_true", help="独立保存作者归因校验后的单题实验")
    parser.add_argument("--followup3", action="store_true", help="独立保存保底方案增强后的单题实验")
    parser.add_argument("--only", nargs="*", choices=list("ABCDE"))
    args = parser.parse_args()
    if sum((args.retest, args.followup, args.followup2, args.followup3)) > 1:
        parser.error("revision flags are mutually exclusive")
    if args.prepare_index:
        print(json.dumps(prepare_index(args.round, retest=args.retest, followup=args.followup,
                                       followup2=args.followup2, followup3=args.followup3),
                         ensure_ascii=False), flush=True)
    else:
        run_round(args.round, only=set(args.only) if args.only else None,
                  retest=args.retest, followup=args.followup, followup2=args.followup2,
                  followup3=args.followup3)


if __name__ == "__main__":
    main()
