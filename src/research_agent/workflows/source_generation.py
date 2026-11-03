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
    return (
        "根据本轮本题授权材料实现指定方法，不假设题号对应任何旧算法。\n"
        "只返回唯一完整Python 3.9标准库源码代码块，UTF-8源文件不超过65536字节，必须定义run_experiment(request)。\n"
        "逐项遵守当前材料中的阶段、字段、类型、参数、方法步骤和约束。内部算法输出不是最终Reproduction答卷。run_experiment接收已解析的Python字典，返回可JSON序列化的Python对象；不要对request调用json.loads，不要用json.dumps包装返回值。\n"
        "不得联网、读写文件、读取隐藏标签、访问凭据、打印日志或使用第三方库。\n"
        "按当前材料处理边界和输入；不能把格式模板或占位符当实际结果。不得用其它方法代替指定方法。\n"
        "先在内部列出所有阶段、公式和参数覆盖关系，再逐项实现；不能只实现样例触发的分支。明确区分字段缺失、null、0和空列表，只有材料允许时才使用默认值。\n"
        "核对各阶段共享辅助函数是否采用同一材料定义，检查多步状态是否意外原地污染，以及排序、并列规则和边界比较是否严格符合原文。不要自行增加材料未要求的处理。\n"
        "返回完整实现而非补丁；不要省略辅助函数，不要输出解释。\n"
        + json.dumps(case.to_dict(),ensure_ascii=False) + "\n"
        + json.dumps(materials,ensure_ascii=False)
    )


def save_generation_response(response: dict, destination: Path) -> None:
    """保存完整模型响应，便于审计，不保存请求头或密钥。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
