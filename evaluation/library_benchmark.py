"""百篇本地论文库的两轮冻结诊断题；每题材料权限单独指定。"""

from __future__ import annotations

import json

from .benchmark import CORE, EXPERIMENTS, FACTS, ROOT


LIBRARY = ROOT / "paper_library" / "pdfs"
BIO = [
    "2512.24427.pdf",  # 表观遗传反馈与基因调控网络
    "2512.18442.pdf",  # 马尔可夫启动子模型
    "2512.20887.pdf",  # 生化控制中的动力学不确定性
    "2512.22415.pdf",  # 噬菌体的层级决策
    "2601.00036.pdf",  # 生物 Petri 网与群体感应
]

BIO_FACTS = [
    ("bio-1", "表观遗传反馈论文将慢速、反馈驱动的表观遗传变量纳入扩展的动力学平均场理论。"),
    ("bio-2", "该论文推导有效随机方程，用于描述稳定态与振荡态及细胞命运景观。"),
    ("bio-3", "启动子论文用离散启动子状态与常微分方程组成混合马尔可夫模型。"),
    ("bio-4", "该论文使用 ChEC-seq 停留时间参数化，并与随机模拟算法比较准确性和运行速度。"),
    ("bio-5", "生化控制论文用互信息率的概率流分解证明反应网络中的波动权衡。"),
    ("bio-6", "该论文认为抑制某成分波动需要另一成分增加变异性。"),
    ("bio-7", "噬菌体论文用信息论和 Lambda 噬菌体裂解/溶原决策研究层级抢占。"),
    ("bio-8", "该论文分析 RecA 和 CII 信号在随机模拟中的条件互信息与决策空间。"),
    ("bio-9", "生物 Petri 网论文提出含弱独立性与信号层级的扩展形式化模型。"),
    ("bio-10", "该论文将模型用于费氏弧菌群体感应，并区分信号流与物质流。"),
]

LIBRARY_ROUNDS = [
    {
        "number": 4,
        "theme": "百篇库中的检索增强问答",
        "a": "开放域问答系统为何要分别测量检索召回和答案质量？给出简明研究建议，并提出一个关键澄清问题。",
        "b": "在跨领域百篇论文中，仅找最初提出 RAG-Sequence/RAG-Token、Fusion-in-Decoder、多次按需检索和反思 token 的论文。排除单纯稠密检索、基准评测、外部记忆和指令微调论文。",
        "gold_b": [CORE[1], CORE[2], CORE[4]],
        "c": "只依据指定五篇论文，比较 DPR、RAG、FiD、RETRO 与 Self-RAG 的检索对象、证据融入生成的位置、评测证据和限制；每篇至少解释一个具体机制和一个可核验实验或结果。",
        "e": "模拟观察：本团队简单问答样本中固定检索常带来无关段落，但还没有因果对照。依据指定五篇论文设计按需检索加多段融合方案，给出基线、数据集、指标、消融、失败边界和反证条件。",
        "d": "D-04",
        "report_materials": CORE,
        "facts": FACTS,
    },
    {
        "number": 5,
        "theme": "百篇库中的生物系统建模",
        "a": "如果要比较随机启动子模型与平均场基因调控网络模型，应优先统一哪些实验条件？请给出一个关键澄清问题。",
        "b": "从百篇论文中找出：把表观遗传反馈纳入基因调控网络动力学平均场模型的论文；用 ChEC-seq 停留时间参数化马尔可夫启动子的论文；把弱独立性和信号层级合并到生物 Petri 网、应用于费氏弧菌群体感应的论文。不要返回只研究噬菌体决策或一般生化波动的论文。",
        "gold_b": [BIO[0], BIO[1], BIO[4]],
        "c": "只依据指定五篇生物系统论文，综述其模型对象、随机性处理、控制或信号机制、验证口径和适用边界。每篇至少给出一个具体机制和一个可核验的分析或实验设计；不同体系不能直接等同比较。",
        "e": "模拟观察：同一批合成生物电路中，平均场预测与单细胞开关时间偶尔不一致，此观察尚未被论文验证。结合指定五篇论文提出跨尺度建模 Idea，说明如何比较启动子随机性、表观遗传慢反馈与信号层级，并给出模拟基线、数据或实验设计、消融、定量指标、可行性和失败边界。",
        "d": "D-05",
        "report_materials": BIO,
        "facts": BIO_FACTS,
    },
]


def case_payload(letter: str, spec: dict) -> dict:
    """B 类授权全库，C/E 只授权五篇；D 类只授权公开实验题。"""
    functions = {"A": "Chat", "B": "Literature_Retrieval", "C": "Survey",
                 "D": "Reproduction", "E": "Idea"}
    case_id = spec["d"] if letter == "D" else f"{letter}-{spec['number']:02d}"
    if letter == "D":
        refs = json.loads((EXPERIMENTS / "references.json").read_text(encoding="utf-8"))["cases"][case_id]["metrics"]
        metrics = "; ".join(f"name={row['name']}, unit={row['unit']}" for row in refs)
        task = f"运行授权公开小实验 {case_id}，固定 seed=42，报告三个实测指标：{metrics}。提交真实图表。"
        materials = [case_id + ".json"]
    else:
        task = spec[letter.lower()]
        if letter == "A":
            materials = []
        elif letter == "B":
            manifest = json.loads((ROOT / "paper_library" / "manifest.json").read_text(encoding="utf-8"))
            if manifest["count"] != 100:
                raise ValueError("paper library must contain exactly 100 verified PDFs")
            materials = [row["filename"] for row in manifest["papers"]]
        else:
            materials = spec["report_materials"]
    return {"case_id": case_id, "function": functions[letter], "title": spec["theme"],
            "input": task, "materials": materials,
            "constraints": ["仅使用本题授权材料", "论文事实和题目模拟观察分开"],
            "expected_output": {"A": "科研回答与澄清问题", "B": "相关论文完整文件名与理由",
                                "C": "至少 800 字的证据综述", "D": "真实实验指标与图表",
                                "E": "至少 800 字的可证伪研究方案"}[letter],
            "output_schema_ref": functions[letter]}
