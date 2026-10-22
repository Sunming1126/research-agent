"""冻结升级源码后执行跨领域真实调用和 D 类公开源码自测。

记录工程和证据质量指标；没有独立内容金标准的报告不生成虚构总分。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from scripts.run_v3_official_simulation import load_local_credentials
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.models import CaseContext
from research_agent.core.session import CaseSession
from research_agent.core.utils import write_json
from research_agent.integrations.llm import GatewayLLM
from research_agent.workflows.layered_d_runner import LayeredDRunner


def cases():
    manifest = json.loads((ROOT / "paper_library/manifest.json").read_text(encoding="utf-8"))
    cv = [p["filename"] for p in manifest["papers"] if p["selected_category"] == "cs.CV"]
    report_papers = ["2512.24971.pdf", "2512.24985.pdf", "2512.25067.pdf", "2601.00123.pdf", "2601.00139.pdf"]
    definitions = [
        ("B-81", "Literature_Retrieval", cv,
         "寻找三篇原始研究，分别研究CNN压缩后的自然损坏鲁棒性、时间损坏下的骨架动作识别、"
         "以及SAR与不完整多光谱融合的洪水范围制图。不要纳入只提出低光视觉问答评测基准的论文。每个方向一篇，共三篇。"),
        ("C-81", "Survey", report_papers,
         "阅读指定五篇论文，比较它们应对数据损坏、观测不完整或部署资源约束的研究设计。"
         "逐篇说明研究对象、核心方法、数据或任务、评价指标及证据支持的局限；"
         "分析哪些设计原则可以迁移，哪些实验结果因任务与条件不同不能横向排名。"
         "至少800字，引用至少五篇指定论文，并给出可核验页码，不编造统一数值结论。"),
        ("E-81", "Idea", ["2512.24971.pdf", "2512.24985.pdf", "2512.25067.pdf"],
         "基于指定三篇论文，设计一个研究低光或损坏输入下视觉模型鲁棒性的可证伪方案。"
         "明确分类、动作识别和视觉问答的任务差异，说明哪些机制仅作为待验证迁移假设。"
         "给出三篇文献的互补关系、模块输入输出、可获取数据及适配、对照与消融、指标、"
         "计算预算、重复实验和失败判据。至少800字，区分论文事实与新建议，引用实际指定论文。"),
    ]
    return [{"case_id": cid, "function": function, "title": "跨领域架构升级诊断", "input": question,
             "materials": files, "constraints": ["仅访问题目授权材料", "数字、归因和页码可核查"],
             "expected_output": function, "output_schema_ref": function}
            for cid, function, files, question in definitions]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="+", default=["B-81", "C-81", "E-81", "D-01", "D-05"])
    parser.add_argument("--label", default="live-v1")
    args = parser.parse_args()
    out = ROOT / "output/architecture-upgrade-20261022" / args.label
    if (out / "summary.json").exists():
        raise ValueError("run label already exists; preserve original evidence")
    load_local_credentials()
    team = "upgrade_v4"
    out.mkdir(parents=True, exist_ok=True)
    manifest = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (ROOT / "src").rglob("*.py")}
    write_json(out / "source-manifest.json", manifest)
    selected = [case for case in cases() if case["case_id"] in args.cases]
    write_json(out / "cases.json", selected)
    settings = Settings(ROOT, out, team, [ROOT / "paper_library/pdfs"], case_timeout=180)
    engine = ResearchAgentEngine(settings)
    rows = []
    for case in selected:
        started = time.monotonic()
        try:
            result = engine.solve_sync(case)
            pointer = json.loads((out / f"data/{team}/work/receipts/{case['case_id']}.json").read_text(encoding="utf-8"))
            receipt_path = out / pointer["receipt"]
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            evidence = json.loads((receipt_path.parent / "evidence.json").read_text(encoding="utf-8"))
            row = {"case_id": case["case_id"], "complete": True, "schema_and_readback": True,
                   "tokens": sum(u["tokens"] for u in receipt["usage"]), "usage": receipt["usage"],
                   "audit": evidence.get("audit"), "evidence_audit": evidence.get("evidence_audit"),
                   "retrieval_trace": evidence.get("retrieval_trace"), "coverage": evidence.get("coverage")}
            if case["function"] == "Literature_Retrieval":
                expected = {"2512.24971.pdf", "2512.25067.pdf", "2601.00123.pdf"}
                actual = {p["filename"] for p in result["papers"]}
                row.update(expected_files=sorted(expected), returned_files=sorted(actual),
                           exact_match=actual == expected, recall=len(actual & expected) / len(expected),
                           precision=len(actual & expected) / max(1, len(actual)))
        except Exception as exc:
            row = {"case_id": case["case_id"], "complete": False, "error_type": type(exc).__name__, "message": str(exc)[:1000]}
        row["seconds"] = round(time.monotonic() - started, 2)
        rows.append(row)
        write_json(out / "summary.json", {"label": "跨领域真实调用诊断，非官方成绩", "cases": rows})
        print(json.dumps({k: row[k] for k in ("case_id", "complete", "seconds")}, ensure_ascii=False), flush=True)
    public = ROOT / "科研助手赛题材料/x新/research-experiments-20261020-source-v1"
    for cid in ("D-01", "D-02", "D-05"):
        if cid not in args.cases:
            continue
        started = time.monotonic()
        llm = GatewayLLM(timeout=180)
        work = out / "public-source-selftest" / cid
        settings = Settings(public, out, team, [public / cid])
        case = CaseContext(cid, "Reproduction", "当前公开方法源码生成", "按当前方法与协议实现算法，执行公开样例自测。",
                           [], [], "Reproduction", "Reproduction")
        try:
            with CaseSession(cid, 180).activate() as session:
                path = LayeredDRunner(settings, work, llm=llm).prepare_checked_implementation(case)
            report = json.loads((work / "public-selftest.json").read_text(encoding="utf-8"))
            row = {"case_id": cid, "complete": True, "scope": "local_gateway_source_and_public_sample_only",
                   "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "selftest": report,
                   "session": session.snapshot()}
        except Exception as exc:
            row = {"case_id": cid, "complete": False, "error_type": type(exc).__name__, "message": str(exc)[:1000]}
        row.update(seconds=round(time.monotonic() - started, 2), usage=llm.usage,
                   tokens=llm.total_tokens, known_tokens=llm.known_tokens, estimated_tokens=llm.estimated_tokens)
        rows.append(row)
        write_json(out / "summary.json", {"label": "跨领域真实调用诊断，非官方成绩", "cases": rows})
        print(json.dumps({k: row[k] for k in ("case_id", "complete", "seconds")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
