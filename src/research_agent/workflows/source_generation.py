"""D 类本题源码生成与原样保存。

正式平台要求实现源码必须来自当前题、当前会话的 Gateway 模型回复。
本模块只负责请求、提取和保存完整源码，不执行模型代码，也不修改模型返回内容。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path


_CODE_BLOCK = re.compile(r"^```(?:python|py)?[ \t]*\r?\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)


def _normalize_source(source: str) -> str:
    """只统一换行并校验入口；不补代码、不格式化、不添加注释。"""
    if not isinstance(source, str) or not 0 < len(source.encode("utf-8")) <= 65536:
        raise ValueError("source must be 1..65536 UTF-8 bytes")
    normalized = source.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"
    tree = ast.parse(normalized, feature_version=(3, 9))
    if not any(isinstance(node, ast.FunctionDef) and node.name == "run_experiment" for node in tree.body):
        raise ValueError("source must define run_experiment(request)")
    return normalized


def extract_source(response: dict) -> str:
    """从完整 chat/completions 响应中提取唯一的完整 Python 实现。"""
    if not isinstance(response, dict):
        raise ValueError("model response must be an object")
    found: set[str] = set()
    for choice in response.get("choices", []):
        if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
            continue
        message = choice.get("message", {})
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        candidates = [content]
        candidates.extend(match.group(1) for match in _CODE_BLOCK.finditer(content))
        for candidate in candidates:
            try:
                found.add(_normalize_source(candidate))
            except (SyntaxError, ValueError, RecursionError):
                continue
    if len(found) != 1:
        raise ValueError("expected exactly one complete source with finish_reason=stop")
    return found.pop()


def save_source(response: dict, destination: Path) -> str:
    """保存规范化后的完整源码并返回源码 SHA-256。"""
    source = extract_source(response)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = source.encode("utf-8")
    destination.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def read_generation_materials(case_id: str, roots: list[Path]) -> dict[str, str]:
    """只从当前题授权的实验材料根目录读取公开方法、协议和样例。"""
    candidates = []
    for root in roots:
        root = Path(root)
        candidates.append(root if root.name == case_id else root / case_id)
    candidates.extend([Path("/data/experiments") / case_id, Path("/experiment")])
    for directory in candidates:
        if not directory.is_dir():
            continue
        paths = {name: directory / name for name in ("method.md", "protocol.json", "sample.json")}
        if all(path.is_file() for path in paths.values()):
            return {
                "method": paths["method.md"].read_text(encoding="utf-8"),
                "protocol": paths["protocol.json"].read_text(encoding="utf-8"),
                "sample": paths["sample.json"].read_text(encoding="utf-8"),
            }
    raise FileNotFoundError(f"authorized experiment materials missing for {case_id}")


def build_source_prompt(case, materials: dict[str, str]) -> str:
    """生成只要求完整源码的提示词；题目材料作为待分析内容传入。"""
    checks = {
        "D-01": "检查训练标签、词表/类别排序、预测与实际返回权重一致，固定seed重复运行的模型与预测完全一致。\n",
        "D-02": (
            "排名若是{id,score}字典，读取row['id']，不能当(id,score)元组解包。"
            "打分遍历查询tokens，f是当前文档中该查询词的次数，不是文档长度；不得改为遍历文档自身词、完全忽略查询。"
            "alpha与beta查询在互斥语料必须返回不同相关首位；非匹配词f=0贡献0。"
            "开发Recall/MRR由实际完整排名和公开relevant核算，普通查询均值与分组等权宏均值分别计算。\n"),
        "D-05": (
            "首次派发时服务器无previous_flow，setup必须为0；只有previous_flow存在且不同才收取setup_ms。"
            "空闲期间保留上个流；不能把last_flow is None当作发生流切换。"
            "没有可派发任务时只跳到严格晚于当前时刻的到达/完成事件，无未来事件时退出，不能反复选择旧空闲时刻。"
            "同一时刻按服务器编号依次派发，每次立刻计入该流已分配量，之后再选下一台任务。"
            "若选中任务因deadline或horizon被拒绝，当前服务器仍空闲，必须在同一派发时刻继续尝试其他等待任务，"
            "直到接收一个任务或等待队列为空；不能直接continue到下一服务器、跳事件或提前退出。"
            "拒绝不消耗时间、不改变服务器上个流或空闲时间、不增加该流已分配量。"
            "同时违反deadline和horizon先判deadline；end==horizon可完成；到窗口停止派发，等待和未来任务均恰好一次horizon。\n"),
    }.get(case.case_id, "")
    return (
        "请根据当前题目的公开方法、协议、样例和输入，实现完整Python3.9标准库算法。\n"
        "只返回一个完整```python代码块，不解释、不省略函数、不返回补丁或多个版本。\n"
        "源码定义run_experiment(request)，不得联网、读写文件、读取隐藏标签/模型凭据或打印日志。\n"
        "使用算法所需标准库和普通import，不用动态加载、反射、模块/对象私有属性或第三方包。"
        "类成员使用公开名称，输入是普通dict，不需要适配任意对象。\n"
        "严格遵守protocol.response_schema，这是算法内部返回，不是最终Reproduction答卷。"
        "同一输入固定seed返回相同算法数据；details不放实际运行耗时等易变诊断，耗时由平台测量。\n"
        + checks +
        f"当前题号：{case.case_id}\n当前题目输入：\n{case.input}\n\n"
        f"method.md：\n{materials['method']}\n\n"
        f"protocol.json：\n{materials['protocol']}\n\n"
        f"sample.json：\n{materials['sample']}\n"
    )


def save_generation_response(response: dict, destination: Path) -> None:
    """保存完整模型响应，便于审计，不保存请求头或密钥。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
