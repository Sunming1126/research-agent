"""从冻结的 300 篇库生成十套正式形态派题，每套 A–E 各五题。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from .benchmark import EXPERIMENTS, ROOT
from .run_rounds import load_local_credentials


MANIFEST = ROOT / "paper_library" / "manifest-300.json"
BASE = ROOT / "output" / "simulation-300"
FIELDS = {"astro-ph.GA": "星系天文", "cond-mat.mtrl-sci": "材料物理",
          "cs.CL": "自然语言处理", "cs.CV": "计算机视觉", "cs.IR": "信息检索与推荐",
          "econ.GN": "经济学", "math.OC": "数学优化", "physics.optics": "光学",
          "q-bio.MN": "定量生物", "stat.ML": "统计机器学习"}


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def retrieval_question(papers: list[dict], distractors: list[dict]) -> tuple[str, str]:
    """单独生成诊断题；只把题目存入派题，金标不交给 Agent。"""
    load_local_credentials()
    from openai import OpenAI
    data = {"targets": [{"title": p["title"], "abstract": p["abstract"][:800]} for p in papers],
            "distractors": [{"title": p["title"], "abstract": p["abstract"][:400]} for p in distractors]}
    prompt = ("用中文写一条论文库检索题。三个 target 各用一个不同的研究主题短语描述，"
              "允许保留英文方法专名，但不要直接复制完整标题，不要出现文件名或 arXiv 编号。"
              "两个 distractor 的主题写作排除条件。请只输出 JSON 对象，字段 question 为单段中文题目。\n"
              + json.dumps(data, ensure_ascii=False))
    try:
        with OpenAI(base_url=os.environ["PLATFORM_LLM_URL"], api_key=os.environ["PLATFORM_API_KEY"],
                    timeout=75, max_retries=0) as client:
            response = client.chat.completions.create(
                model=os.environ["AGENT_MODEL_NAME"], max_tokens=5000,
                messages=[{"role": "system", "content": "你是独立的测试题编写者，只输出 JSON。"},
                          {"role": "user", "content": prompt}])
        raw = (response.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
        question = json.loads(raw)["question"]
        if (not isinstance(question, str) or len(question) < 40
                or any(p["arxiv_id"] in question for p in papers + distractors)):
            raise ValueError("weak generated question")
        return question, "model_paraphrase"
    except Exception as exc:
        diagnostic = (f" finish={response.choices[0].finish_reason} chars={len(raw)} "
                      f"preview={raw[:120]!r}") if "raw" in locals() else ""
        print(f"BENCH_QUERY_FALLBACK {type(exc).__name__}{diagnostic}", flush=True)
        # 保底题保留论文主题但更接近标题，需在汇总中单独标记难度不足。
        titles = "、".join(p["title"] for p in papers)
        excluded = "、".join(p["title"] for p in distractors)
        return f"在授权论文库中找出分别研究以下三项主题的论文：{titles}。排除主题：{excluded}。", "title_fallback"


def normalize_question(question: str) -> str:
    """避免“同时涉及”被误读为每一篇必须覆盖全部三个主题。"""
    question = question.replace("检索同时涉及以下三个研究主题的论文", "分别检索对应以下三个研究主题的三篇论文")
    prefix = "请为下列三个不同研究主题各找最相关的一篇论文，合计三篇。"
    return question if question.startswith(prefix) else prefix + question


def build(start_round: int = 1) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest["count"] != 300:
        raise ValueError("need exactly 300 verified papers")
    papers = manifest["papers"]
    by_field = defaultdict(list)
    for row in papers:
        by_field[row["selected_category"]].append(row)
    if set(by_field) != set(FIELDS) or any(len(rows) != 30 for rows in by_field.values()):
        raise ValueError("need 30 papers in each of ten fields")
    names = [row["filename"] for row in papers]
    references = json.loads((EXPERIMENTS / "references.json").read_text(encoding="utf-8"))["cases"]
    fields = list(FIELDS)
    for round_index in range(start_round-1, 10):
        team = f"sim300_r{round_index+1:02d}"
        out = BASE / f"round-{round_index+1:02d}"
        assigned = out / "workspace" / "data" / team / "assigned"
        specs_path = out / "frozen-spec.json"
        specs = json.loads(specs_path.read_text(encoding="utf-8")) if specs_path.exists() else {"cases": {}}
        ids, entries = [], []
        for letter in "ABCDE":
            for index in range(5):
                case_id = f"{letter}-{index+1:02d}"
                category = fields[(round_index*5+index) % 10]
                domain = FIELDS[category]
                occurrence = round_index // 2
                subset = by_field[category][occurrence*5:occurrence*5+5]
                chosen = [row["filename"] for row in subset]
                if letter == "A":
                    question = f"比较{domain}研究中的不同方法时，如何避免把任务、数据与评价指标混为一谈？给出简要建议和一个关键澄清问题。"
                    materials = []
                elif letter == "B":
                    previous = specs["cases"].get(case_id, {})
                    if previous and previous["question_origin"] == "model_paraphrase":
                        question, origin = previous["input"], previous["question_origin"]
                    else:
                        question, origin = retrieval_question(subset[:3], subset[3:])
                    question = normalize_question(question)
                    materials = names
                    specs["cases"][case_id] = {"gold": chosen[:3], "input": question,
                                               "question_origin": origin, "category": category}
                    write_json(specs_path, specs)
                elif letter == "C":
                    question = (f"仅根据本题指定的五篇{domain}论文，比较研究问题、方法、数据或分析对象、"
                                "指标、核心发现和局限；用论文完整文件名及页码给出可回查引用。")
                    materials = chosen
                elif letter == "E":
                    question = (f"模拟观察：在{domain}中，不同方法的优势随数据条件变化；该观察只是题目设定。"
                                "基于指定五篇论文设计可证伪研究：说明论文互补关系、方法路线、基线、"
                                "命名数据集或待选数据、消融、指标、可行性和失败边界。")
                    materials = chosen
                else:
                    refs = references[case_id]["metrics"]
                    metrics = "; ".join(f"name={m['name']}, unit={m['unit']}" for m in refs)
                    question = f"运行授权公开小实验 {case_id}，固定 seed=42，报告三个实测指标：{metrics}。提交真实图表。"
                    materials = [case_id + ".json"]
                function = {"A": "Chat", "B": "Literature_Retrieval", "C": "Survey",
                            "D": "Reproduction", "E": "Idea"}[letter]
                payload = {"case_id": case_id, "function": function, "title": domain,
                           "input": question, "materials": materials,
                           "constraints": ["仅使用本题授权材料", "论文事实与题目模拟观察分开"],
                           "expected_output": "符合题型 Schema 的结果", "output_schema_ref": function}
                path = assigned / f"{case_id}.json"
                write_json(path, payload)
                relative = f"data/{team}/assigned/{case_id}.json"
                ids.append(case_id)
                entries.append({"case_id": case_id, "function": function, "title": domain,
                                "question_file": relative,
                                "question_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        dispatch = {"team": team, "schema_version": 2, "cases_per_category": 5,
                    "rules_version": "research-assistant-objective-v2", "case_ids": ids, "cases": entries}
        write_json(out / "workspace" / "data" / team / "dispatch.json", dispatch)
        print(f"READY round={round_index+1:02d} cases=25", flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-round", type=int, choices=range(1, 11), default=1)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "src"))
    build(args.from_round)
