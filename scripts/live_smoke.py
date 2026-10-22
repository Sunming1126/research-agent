"""仅供开发联调：使用明确标记的合成文档验证真实模型调用。"""
import json
import os
import sys
import time
from pathlib import Path

os.environ["RESEARCH_RUNTIME"] = "local"
sys.path.insert(0, "/src/tests")
from fixtures import papers, case
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.utils import write_json

root = Path("/results/live-smoke")
names = papers(root/"papers")
s = Settings(root, root/"output", "dev_smoke", [root/"papers"],
             experiment_mode="local", local_experiment_root=Path("/materials"),
             llm_calls=30, llm_tokens=40000, case_timeout=480)
engine = ResearchAgentEngine(s)
summary = {"fixture": "synthetic papers, not official questions", "cases": []}
for letter in os.environ.get("SMOKE_CASES", "ABCE"):
    payload = case(letter+"-01", names if letter != "A" else [])
    if letter == "A":
        payload["input"] = "解释城市绿地覆盖与地表温度可能的关系，区分关联与因果，并提出一个关键研究范围问题。"
    elif letter == "B":
        payload["input"] = "检索使用遥感比较城市植被与地表温度的论文，排除只做植物分类的论文。"
    elif letter == "C":
        payload["input"] = "阅读全部指定合成测试论文，完成至少900字的中文综述，比较研究问题、方法、数据、指标、结论、局限。对没有报告的数据与数字如实说明，不编造。每篇实际引用，标注真实页码。"
    else:
        payload["input"] = "根据指定合成测试论文提出研究方案。模拟观察显示部分植被区域温度较低，但尚未检验因果效应。区分论文结论、模拟观察与待验证方案。至少900字，覆盖动机、技术路线、基线、数据集、实验消融、量化指标、互补机制、可行性和失败边界及可证伪方式。"
    started = time.monotonic()
    try:
        result = engine.solve_sync(payload)
        row = {"case_id": payload["case_id"], "ok": True, "seconds": round(time.monotonic()-started,2),
               "path": result.get("path"), "papers": len(result.get("papers",[])),
               "chars":len(result.get("markdown",""))}
    except Exception as exc:
        row = {"case_id": payload["case_id"], "ok": False, "seconds": round(time.monotonic()-started,2),
               "error": str(exc)}
    summary["cases"].append(row)
    print(json.dumps(row,ensure_ascii=False),flush=True)
    write_json(root/"summary.json",summary)

