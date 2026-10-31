"""合成论文和假模型响应；正式镜像不包含这些测试材料。"""
from pathlib import Path
import fitz
from research_agent.core.models import FUNCTIONS

METRICS = {
    "D-01": [("Macro-F1", "分数"), ("Accuracy", "%"), ("运行时间", "秒")],
    "D-02": [("Recall@5", "%"), ("MRR@10", "分数"), ("查询吞吐", "queries/s")],
    "D-03": [("推理吞吐", "samples/s"), ("P95 延迟", "ms") , ("缓存命中率", "%")],
    "D-04": [("验证集准确率", "%"), ("约束通过率", "%"), ("搜索耗时", "分钟")],
    "D-05": [("公平性 Jain 指数", "分数"), ("归一化吞吐", "%"), ("平均等待时间", "ms")],
}


def case(case_id, materials=None):
    fn = FUNCTIONS[case_id[0]]
    text = "Compare urban vegetation and surface temperature using remote sensing; exclude plant taxonomy only."
    if case_id.startswith("D-"):
        text = "; ".join(f"name={name}, unit={unit}" for name,unit in METRICS[case_id]) + "。"
    return {"case_id": case_id, "function": fn, "title": "Synthetic research fixture",
            "input": text, "materials": materials or [], "constraints": [], "expected_output": fn,
            "output_schema_ref": fn}


def papers(root):
    root.mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(1, 6):
        name = f"Study_{i}.pdf"
        doc = fitz.open()
        for j in range(2):
            page = doc.new_page()
            text = (f"Study {i}: Urban Vegetation\n\n"
                    "Remote sensing observations compare urban vegetation and surface temperature. "
                    "This synthetic fixture describes association, not causation.\n\n"
                    "Methods\nMatched urban districts are compared using a fixed observation window. "
                    "Shade and water availability are possible confounding variables.\n\n"
                    "Limitations\nThe test data do not establish a causal effect. "
                    "Future studies should compare seasons and report uncertainty.")
            page.insert_textbox(fitz.Rect(40,40,550,780), text, fontsize=12)
        doc.save(root/name)
        doc.close()
        names.append(name)
    return names


class FakeLLM:
    usage = []
    def json(self, stage, data, **kwargs):
        if stage == "chat":
            return {"answer": "关联需要通过控制混杂因素的实验进一步检验。", "clarifying_question": "研究对象的范围是什么？"}
        if stage == "query":
            return {"topic": "urban vegetation surface temperature", "methods": "remote sensing",
                    "focus": "comparison", "exclusions": ["plant taxonomy"], "variants": ["绿地 温度"]}
        if stage == "retrieve":
            return {"papers": [{"filename": row["filename"], "reason": "Uses remote sensing to compare vegetation and surface temperature.",
                                "chunk_ids": [row["chunks"][0]["chunk_id"]]} for row in data["candidates"]]}
        if stage == "extract":
            return {"claims": [{"claim": "The synthetic study compares vegetation and temperature.",
                                "chunk_id": row["chunk_id"], "quote": row["text"]}
                               for row in data["chunks"] if "Remote sensing" in row["text"]]}
        if stage == "report":
            rows = {}
            for evidence in data["claims"]:
                rows.setdefault(evidence["ref"], evidence)
            sections = ["# Synthetic fixture report", "", "## 证据比较", ""]
            for ref, row in rows.items():
                sections.append(f"本文材料是测试使用的合成文档。遥感观察比较了城市植被与地表温度，作者限定了结果的解释范围，不能据此建立因果关系。[{ref}, p.{row['page']}]")
                sections.append("")
            headings = ["研究动机与边界", "技术路线", "基线与数据集", "实验设计与消融", "量化指标", "多文献互补", "可行性与失败边界", "可证伪方式"]
            for heading in headings:
                sections += [f"## {heading}", "",
                    "以下内容是待验证的研究设计建议。建议在相同观察窗口内比较城市区域，明确研究对象、采样单位及可获得的数据。建议设定基线与对照条件，固定划分方式并保留独立测试集。建议记录成本、误差与不确定性，通过消融检验各组成部分。如果预注册指标不能稳定改善，应该否定相应假设并分析失败边界。", ""]
            return {"markdown": "\n".join(sections), "used_evidence_ids": [row["evidence_id"] for row in rows.values()]}
        if stage == "audit":
            return {"ok": True, "issues": []}
        raise AssertionError(stage)

