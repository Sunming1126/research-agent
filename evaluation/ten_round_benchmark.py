"""十个领域的冻结诊断题：每轮 A/B/C/D/E 各一题。"""

from __future__ import annotations

import json

from .benchmark import EXPERIMENTS, ROOT
from .library_benchmark import LIBRARY


MANIFEST = json.loads((ROOT / "paper_library" / "manifest.json").read_text(encoding="utf-8"))
ALL_PAPERS = [row["filename"] for row in MANIFEST["papers"]]

# 先固定目标文件名与题目，再运行模型；后续轮次不能按模型答案修改金标。
ROUNDS = [
    {"number": 6, "domain": "信息检索与推荐", "category": "cs.IR",
     "files": ["2512.24268.pdf", "2512.24325.pdf", "2512.24366.pdf", "2512.24787.pdf", "2512.24943.pdf"],
     "gold": ["2512.24268.pdf", "2512.24325.pdf", "2512.24943.pdf"],
     "b": "从百篇论文中找出三篇：分别研究检索增强生成的语料投毒防御、推荐系统动态计算分配的多智能体强化学习，以及电子商务相关性中的长尾与视觉显著性基准。排除单纯生成式列表推荐和解释性推荐论文。",
     "focus": "检索和推荐系统的鲁棒性、计算分配、解释与评测"},
    {"number": 7, "domain": "自然语言处理", "category": "cs.CL",
     "files": ["2512.24867.pdf", "2512.24880.pdf", "2512.24885.pdf", "2512.24933.pdf", "2512.24997.pdf"],
     "gold": ["2512.24867.pdf", "2512.24885.pdf", "2512.24933.pdf"],
     "b": "找出动态组合知识陈述来评估大模型、用概率信念估计执行策略性对话行为、以及对多步大模型流水线做依赖引导提示词优化的三篇论文。排除一般架构连接设计和长法律文档分类。",
     "focus": "语言模型评测、对话决策和多步提示优化"},
    {"number": 8, "domain": "定量生物", "category": "q-bio.MN",
     "files": ["2512.17491.pdf", "2512.17597.pdf", "2512.18752.pdf", "2512.23784.pdf", "2512.20887.pdf"],
     "gold": ["2512.17491.pdf", "2512.17597.pdf", "2512.18752.pdf"],
     "b": "找出分别讨论非竞争化学反应网络的阿贝尔结构、用机械连杆设计最小别构 ATPase 类机器、以及生物进化之前选择如何出现的三篇论文。排除蛋白脂质编码和一般生化控制波动论文。",
     "focus": "反应网络结构、分子机器和进化前选择"},
    {"number": 9, "domain": "经济学", "category": "econ.GN",
     "files": ["2512.22917.pdf", "2512.23337.pdf", "2512.24852.pdf", "2512.24968.pdf", "2601.00896.pdf"],
     "gold": ["2512.22917.pdf", "2512.23337.pdf", "2512.24968.pdf"],
     "b": "在百篇库中找出广告限制如何改变亏本引流竞争与价格协调、异质企业创新网络中的研发生产率之谜、以及新闻出版商对生成式 AI 的战略响应三篇研究。排除慈善激励和医保就业论文。",
     "focus": "平台竞争、创新网络和生成式 AI 对媒体市场的影响"},
    {"number": 10, "domain": "统计机器学习", "category": "stat.ML",
     "files": ["2512.23927.pdf", "2512.24587.pdf", "2512.24768.pdf", "2512.24927.pdf", "2601.00038.pdf"],
     "gold": ["2512.23927.pdf", "2512.24768.pdf", "2512.24927.pdf"],
     "b": "找出不要求 Bellman 完备性的软拟合 Q 迭代、抵抗数据污染的稀疏离线强化学习、以及用快速前向值方法加速一阶扩散采样的三篇论文。排除多重风险控制和参数微分系统主动学习。",
     "focus": "强化学习、风险控制与生成模型的算法保证"},
    {"number": 11, "domain": "星系天文", "category": "astro-ph.GA",
     "files": ["2512.24433.pdf", "2512.24537.pdf", "2512.24670.pdf", "2512.24677.pdf", "2512.24840.pdf"],
     "gold": ["2512.24433.pdf", "2512.24537.pdf", "2512.24670.pdf"],
     "b": "找出用卷积网络恢复速度分辨回响测绘转移函数、比较 SPHEREx 低分辨光谱与多波段红移预测、以及多个超新星驱动气泡中的尘埃破坏研究。排除单颗超新星尘埃破坏和恒星参数推断。",
     "focus": "天文反演、红移预测和星际尘埃演化"},
    {"number": 12, "domain": "光学", "category": "physics.optics",
     "files": ["2512.24516.pdf", "2512.24641.pdf", "2512.24716.pdf", "2512.24846.pdf", "2512.24983.pdf"],
     "gold": ["2512.24516.pdf", "2512.24641.pdf", "2512.24716.pdf"],
     "b": "找出用偏振差分损耗增强空芯光纤消光、探索透明导电氧化物中极端电子温度以实现光学周期动态光子学、以及用宽带混沌熵源进行超快随机比特生成的三篇论文。排除结构光束光栅和光学脉冲神经网络。",
     "focus": "光纤偏振、动态光子学和混沌随机源"},
    {"number": 13, "domain": "数学优化", "category": "math.OC",
     "files": ["2512.24676.pdf", "2512.24717.pdf", "2512.24736.pdf", "2512.24785.pdf", "2512.24872.pdf"],
     "gold": ["2512.24676.pdf", "2512.24717.pdf", "2512.24736.pdf"],
     "b": "找出通过消息传递分解图结构非线性规划、求解受约束多目标 DC 型优化的近端次梯度算法、以及基于随机优化的定量风险管理研究。排除装箱近似算法和四次张量优化。",
     "focus": "图结构分解、近端算法和随机优化"},
    {"number": 14, "domain": "材料物理", "category": "cond-mat.mtrl-sci",
     "files": ["2512.24756.pdf", "2512.24771.pdf", "2512.24816.pdf", "2512.24849.pdf", "2512.24876.pdf"],
     "gold": ["2512.24756.pdf", "2512.24771.pdf", "2512.24816.pdf"],
     "b": "找出 X 射线光电子能谱的实践原则、Ni2MnGa 等铁磁体自发磁化的温度依赖、以及从第一性原理原子模拟上推到电极尺度的锰六氰基铁酸盐研究。排除晶体结构预测和固态储氢晶体。",
     "focus": "材料表征、磁性和电极多尺度建模"},
    {"number": 15, "domain": "计算机视觉", "category": "cs.CV",
     "files": ["2512.24971.pdf", "2512.24985.pdf", "2512.25008.pdf", "2512.25067.pdf", "2512.25075.pdf"],
     "gold": ["2512.24971.pdf", "2512.24985.pdf", "2512.25008.pdf"],
     "b": "找出压缩技术如何影响 CNN 对自然损坏的鲁棒性、低光室内视觉原语问答基准 DarkQA、以及利用深度基础模型的端到端稠密视觉 SLAM 三篇研究。排除动作识别和动态场景生成。",
     "focus": "视觉鲁棒性、低光问答和稠密 SLAM"},
]


def validate_benchmark() -> None:
    indexed = {row["filename"]: row for row in MANIFEST["papers"]}
    if len(ROUNDS) != 10 or len(ALL_PAPERS) != 100:
        raise ValueError("ten rounds require 100 papers")
    for spec in ROUNDS:
        if len(spec["files"]) != 5 or len(spec["gold"]) != 3:
            raise ValueError("each round requires five report papers and three retrieval targets")
        if not set(spec["files"]) <= indexed.keys() or not set(spec["gold"]) <= set(spec["files"]):
            raise ValueError("benchmark references an unknown paper")
        if any(indexed[name]["selected_category"] != spec["category"] for name in spec["files"]):
            raise ValueError("report papers must match the frozen domain")


def case_payload(letter: str, spec: dict) -> dict:
    functions = {"A": "Chat", "B": "Literature_Retrieval", "C": "Survey",
                 "D": "Reproduction", "E": "Idea"}
    number = spec["number"]
    d_case = f"D-{(number-6)%5+1:02d}"
    case_id = d_case if letter == "D" else f"{letter}-{number:02d}"
    if letter == "A":
        task = f"在{spec['focus']}的跨论文比较中，应先统一哪些研究条件？请给出简明建议和一个关键澄清问题。"
        materials = []
    elif letter == "B":
        task, materials = spec["b"], ALL_PAPERS
    elif letter == "C":
        task = (f"仅根据本题指定的五篇{spec['domain']}论文，比较各自的研究问题、方法、数据或分析对象、"
                "指标或验证、核心发现和局限。逐篇给出可回查事实，不能把不同论文的实验直接等同比较。")
        materials = spec["files"]
    elif letter == "E":
        task = (f"模拟观察：本团队在{spec['focus']}的初步比较中发现，不同方法的优势随数据条件变化；"
                "这只是题目设定，尚未被指定论文共同验证。基于指定五篇论文提出可证伪的研究方案，"
                "说明各篇互补角色、技术路线、具体候选基准、基线、消融、量化指标、可行性和失败边界。")
        materials = spec["files"]
    else:
        refs = json.loads((EXPERIMENTS / "references.json").read_text(encoding="utf-8"))["cases"][d_case]["metrics"]
        metrics = "; ".join(f"name={row['name']}, unit={row['unit']}" for row in refs)
        task = f"运行授权公开小实验 {d_case}，固定 seed=42，报告三个实测指标：{metrics}。提交真实图表。"
        materials = [d_case + ".json"]
    return {"case_id": case_id, "function": functions[letter], "title": spec["domain"],
            "input": task, "materials": materials,
            "constraints": ["仅使用本题授权材料", "论文事实和题目模拟观察分开"],
            "expected_output": {"A": "科研回答与澄清问题", "B": "相关论文完整文件名与理由",
                                "C": "至少 800 字的证据综述", "D": "真实实验指标与图表",
                                "E": "至少 800 字的可证伪研究方案"}[letter],
            "output_schema_ref": functions[letter]}


validate_benchmark()
