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
        candidates.append(Path(root) / case_id)
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
    return (
        "请根据下面当前题目的公开方法、协议、样例和输入，实现一个完整的 Python 3.9 标准库算法。\n"
        "只返回一个完整的 ```python 代码块，不要解释、不要省略函数、不要返回补丁、不要拆成多个版本。\n"
        "源码必须定义 run_experiment(request)，不得联网、读写文件、读取隐藏标签、访问模型凭据或打印日志。\n"
        "仅使用算法所需标准库，如math、collections、re、heapq、itertools、random、statistics、time；"
        "不使用动态加载、反射、私有运行时属性或第三方包。\n"
        "严格遵守 protocol.response_schema；这是算法内部返回，不是最终 Reproduction 答卷。\n\n"
        "生成前检查：事件驱动循环没有可派发任务时，只能跳到严格晚于当前时刻的下一次到达或完成事件，"
        "不能反复选择已空闲服务器的旧完成时刻；无未来事件时退出。\n"
        "检查空输入、首次任务晚到、空闲后换流、同时到达、截止时间及窗口边界。"
        "同一输入固定seed应返回相同算法数据；details不放实际运行耗时等易变诊断，耗时由平台测量。\n\n"
        "自测能力约定：优先用纯函数、列表和字典；用普通 import 导入算法标准库。"
        "不要使用反射、type(x).__name__、运行时或模块私有属性；类成员使用公开名称，避免 self._name。"
        "算法输入是普通 dict，不需要 hasattr/getattr 适配任意对象。"
        "D-05 同时违反 deadline 与 horizon 时先判 deadline；到窗口后未派发任务才统一 horizon。\n\n"
        "D-05 同一时刻按服务端编号逐个派发，每次选中任务后立即更新该流已分配服务量，"
        "再选择下一个空闲服务端的任务，不能先批量选择再更新；end==horizon可完成，"
        "时钟到窗口时仍在等待或尚未来到的所有任务也必须恰好记录一次horizon丢弃。\n\n"
        f"当前题号：{case.case_id}\n当前题目输入：\n{case.input}\n\n"
        f"method.md：\n{materials['method']}\n\n"
        f"protocol.json：\n{materials['protocol']}\n\n"
        f"sample.json：\n{materials['sample']}\n"
    )


def save_generation_response(response: dict, destination: Path) -> None:
    """保存完整模型响应，便于审计，不保存请求头或密钥。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
