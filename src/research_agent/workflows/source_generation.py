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
    boundary = ''
    if case.case_id == 'D-01':
        boundary = ('基线必须是训练标签的多数类常量预测；最高频并列时选字典序最小的标签，'
                    '为data.texts中每条文本重复该标签。禁止用输入顺序、Counter首次出现顺序或候选预测代替基线。\n')
    elif case.case_id == 'D-05':
        boundary = ('公开协议边界核对：同一时刻先处理所有到达/完成事件，再按服务器编号派发；'
                    '优先队列的时间相同时必须有可比较的稳定标识，绝不能让Python比较任务dict。'
                    '拒绝任务后该空闲服务器继续尝试等待任务，不能直接跳过服务器；'
                    '公平累计在派发时预留；无任务和空闲间隔能正常退出/推进。'
                    'baseline和candidate共享一个清晰的状态推进函数，避免重复事件和多套矛盾状态。\n')
    elif case.case_id == 'D-02':
        boundary = ('接口边界核对：run的baseline/candidate仅包含test_queries；'
                    'dev_queries排名只存details.search，不混入正式预测。rank按data.selected排序，不能重选参。'
                    '每个query完整排列所有documents；开发搜索逐项覆盖protocol.grid并按method的字典序规则打破并列。\n')
    return (
        "请根据下面当前题目的公开方法、协议、样例和输入，实现一个完整的 Python 3.9 标准库算法。\n"
        "只返回一个完整的 ```python 代码块，不要解释、不要省略函数、不要返回补丁、不要拆成多个版本。\n"
        "源码必须定义 run_experiment(request)，不得联网、读写文件、读取隐藏标签、访问模型凭据或打印日志。\n"
        "仅使用算法所需标准库，如math、collections、re、heapq、itertools、random、statistics、time；"
        "不使用动态加载、反射、私有运行时属性或第三方包。\n"
        "严格遵守 protocol.response_schema；这是算法内部返回，不是最终 Reproduction 答卷。\n\n"
        "生成时间受限：源码目标不超过1800 token、100行，省略注释、类型注解和重复包装；"
        "使用共享小函数实现基线和候选，仍须覆盖协议所有阶段和边界。"
        "优先保证baseline和完整输出结构；候选确实无法完成时按协议返回candidate=null，不能伪造轨迹或预测。\n"
        + boundary +
        f"当前题号：{case.case_id}\n当前题目输入：\n{case.input}\n\n"
        f"method.md：\n{materials['method']}\n\n"
        f"protocol.json：\n{materials['protocol']}\n\n"
        f"sample.json：\n{materials['sample']}\n"
    )


def save_generation_response(response: dict, destination: Path) -> None:
    """保存完整模型响应，便于审计，不保存请求头或密钥。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
