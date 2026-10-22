"""三轮冻结诊断题；所有论文文件均来自公开 arXiv 页面。"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAPERS = ROOT / "local-workspace" / "papers"
EXPERIMENTS = ROOT / "科研助手赛题材料" / "research-experiments"
CORE = [
    "2004.04906-DPR.pdf", "2005.11401-RAG.pdf", "2007.01282-FiD.pdf",
    "2112.04426-RETRO.pdf", "2310.11511-Self-RAG.pdf",
]
ALL = CORE + ["2104.08663-BEIR.pdf", "2203.02155-InstructGPT.pdf"]

FACTS = [
    ("dpr-1", "DPR 使用问题编码器与段落编码器，将查询和段落映射到稠密向量并按相似度检索。"),
    ("dpr-2", "DPR 将稠密检索与 BM25 比较，并在开放域问答的 top-k 召回上报告结果。"),
    ("rag-1", "RAG 结合参数化 seq2seq 生成模型与非参数化的外部文档索引。"),
    ("rag-2", "RAG 提出 RAG-Sequence 和 RAG-Token 两种对检索文档边缘化的生成形式。"),
    ("fid-1", "FiD 分别编码多个检索到的段落，再由解码器融合信息。"),
    ("fid-2", "FiD 在开放域问答中比较不同检索段落数量并报告 Natural Questions 与 TriviaQA 结果。"),
    ("retro-1", "RETRO 借助外部大规模文本库检索邻近文本片段来辅助语言建模。"),
    ("retro-2", "RETRO 论文报告检索库规模约 2 万亿 token，并将参数效率与更大参数模型比较。"),
    ("selfrag-1", "Self-RAG 训练模型按需检索，而非每次固定检索。"),
    ("selfrag-2", "Self-RAG 使用反思 token 对生成内容及检索证据进行评价。"),
]

ROUNDS = [
    {
        "number": 1,
        "theme": "开放域问答中的检索增强生成路线",
        "a": "研究开放域问答时，DPR 检索、RAG 生成和 FiD 融合各解决什么问题？请给出简明回答，并提出一个最关键的研究范围澄清问题。",
        "b": "只找提出 RAG-Sequence/RAG-Token、Fusion-in-Decoder 或 Self-RAG 按需检索与反思机制的原始论文。不要返回只做指令微调的论文，也不要把普通稠密检索、基准评测或 RETRO 当成这些方法。",
        "gold_b": [CORE[1], CORE[2], CORE[4]],
        "c": "比较指定五篇论文的检索位置、生成阶段的证据融合方式、开放域问答评测、参数或计算代价与可核验的局限。请明确区分论文结论和基于证据提出的推断。",
        "e": "模拟观察：在本团队的小样本开放域问答试验中，固定 top-k 检索偶尔引入无关段落，但尚未形成论文级实验结论。基于指定论文提出按需检索与多段融合的可证伪 Idea，说明互补机制、复现瓶颈和量化指标。",
        "d": "D-01",
    },
    {
        "number": 2,
        "theme": "检索质量、跨域稳健性与外部记忆",
        "a": "如果检索器在新领域退化，为什么生成器的答案可能受影响？给出一个可测的研究假设，并提出一个关键澄清问题。",
        "b": "寻找 DPR 稠密段落检索的原始论文、BEIR 零样本信息检索基准论文，以及 RETRO 外部文本库语言模型论文。排除只做指令微调的论文，也不要返回 FiD、RAG 或 Self-RAG。",
        "gold_b": [CORE[0], "2104.08663-BEIR.pdf", CORE[3]],
        "c": "以指定五篇论文为证据，比较稠密检索、检索增强生成、多段融合、外部记忆及自我反思对跨域稳健性的潜在作用。对未被论文直接验证的跨域推断要明示待验证。",
        "e": "模拟观察：本团队把问答语料换到医学领域后，检索 recall 降低，但尚无因果对照。结合指定论文提出跨域检索诊断和自适应生成方案，明确数据泄漏防护、复现瓶颈、基线、消融和失败边界。",
        "d": "D-02",
    },
    {
        "number": 3,
        "theme": "自适应检索、证据可信度与推理效率",
        "a": "在需要低延迟的问答系统中，如何判断一次检索值得执行？请给出可测思路，并提出一个关键澄清问题。",
        "b": "只找研究生成时按需检索并使用反思 token 的 Self-RAG 原始论文，以及提出 RAG-Sequence/RAG-Token 的 RAG 原始论文。排除仅做指令微调、纯检索器、FiD、BEIR 和 RETRO。",
        "gold_b": [CORE[4], CORE[1]],
        "c": "对指定五篇论文写综述，重点比较每次固定检索、检索邻近片段、按需触发检索和解码阶段融合的证据成本。区分实际论文实验与本轮尚未测试的推理延迟假设。",
        "e": "模拟观察：本团队系统在简单问题上可能不需要检索，在复杂问题上固定检索成本较高；这只是待验证观察。提出以置信度触发检索、证据筛选和多段融合为核心的 Idea，给出 latency-quality 权衡、可重复实验及失败边界。",
        "d": "D-03",
    },
]


def case_payload(letter: str, round_spec: dict) -> dict:
    number = round_spec["number"]
    case_id = f"{letter}-{number:02d}"
    functions = {"A": "Chat", "B": "Literature_Retrieval", "C": "Survey", "D": "Reproduction", "E": "Idea"}
    function = functions[letter]
    if letter == "D":
        case_id = round_spec["d"]
        refs = json.loads((EXPERIMENTS / "references.json").read_text(encoding="utf-8"))["cases"][case_id]["metrics"]
        pairs = "; ".join(f"name={row['name']}, unit={row['unit']}" for row in refs)
        task = f"运行授权公开小实验 {case_id}，固定 seed=42，逐项报告三个实测指标：{pairs}。提交真实图表。"
        materials = [case_id + ".json"]
    else:
        task = round_spec[letter.lower()]
        materials = [] if letter == "A" else ALL if letter == "B" else CORE
    expected = {
        "A": "回答与一个澄清问题", "B": "完整 PDF 文件名和理由", "C": "至少 800 字的证据综述",
        "D": "真实指标、图表和实验状态", "E": "至少 800 字的可验证 Idea 报告",
    }[letter]
    return {"case_id": case_id, "function": function, "title": round_spec["theme"],
            "input": task, "materials": materials, "constraints": ["仅使用本题授权材料", "事实与模拟观察分开"],
            "expected_output": expected, "output_schema_ref": function}
