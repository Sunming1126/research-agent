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
    guidance = {
        'D-01': (
            '本次从训练数据重新建立词表和类别的字典序；统计df时一篇文档内重复词只算一次。'
            '训练集之外的词不能进入词表。使用协议中的epochs/lr/penalty，零初始化、全批量更新；'
            '每一轮先累计所有样本梯度再更新，bias也正则化。softmax先减最大logit。'
            'TF-IDF归一化后再附bias，空文本仍有bias；返回真实classes/vocabulary/idf/weights。'
            '基线始终使用训练标签多数类，并列选字典序最小，不能复用候选预测。'),
        'D-02': (
            '把分词、标题/正文词频、df、长度预先计算一次，在各配置和查询间复用；'
            '空标题也计入N，两个字段分别算平均长度和df，重复查询词按次数贡献。'
            'run中完整遍历protocol.grid；search记录开发查询排名、普通均值和分组统计。'
            '先算每个组内查询均值，再将组等权平均，不能按组大小加权。'
            '选参依次最大化round(最差组MRR,12)、round(组宏MRR,12)、round(组宏Recall,12)，'
            '最后最小化(title_weight,k1,b)。测试无标签，绝不参与选参。'
            'rank只使用data.selected并返回candidate排名，不再搜索；baseline也必须支持rank。'),
        'D-05': (
            '基线FIFO和公平候选共用事件推进函数，策略仅决定选哪个等待任务。服务器只有1至3个，'
            '用已排序任务的下标、等待列表、free_at与last_flow两个服务器数组及分流预留量即可。'
            '不要事件堆、active集合或多层完成事件循环，以免空闲事件反复入队导致死循环。'
            '当前派发处理后，只从严格大于now的下一到达时刻和忙服务器结束时刻中取最小值推进。'
            '没有未来任务且没有等待任务即退出；到horizon立即停止派发。不要将时间或权重强转整数。'
            '为每个服务器分别保存空闲时刻和上次流，即使空闲也不清除上次流。'
            '每轮先处理当前时刻：完成释放、所有到达入队、过期待办丢弃后，按服务器编号派发。发现到达事件后不能直接continue跳过派发；有空闲服务器和等待任务时不能先跳到下一个事件。所有当前派发完成后才推进时钟。'
            '公平优先级每次选择重新按累计已分配服务/权重计算；派发立即预留。'
            '仅比较数值和稳定id，不让堆比较任务字典。选中后先判start>=deadline，再判end>horizon；'
            '丢弃后同服务器继续选：外层按服务器编号，内层持续处理等待任务，直到该服务器成功派发或队列为空；不能用外层for的continue代替。失败任务不更新上次流和预留量。end==horizon允许完成。'
            '到窗口后剩余任务记horizon。每个任务恰好在trace或dropped出现一次；'
            'served_ms含全部声明流，零服务保留。每次请求从空状态开始。'),
    }
    boundary = guidance.get(case.case_id, '') + '\n'
    return (
        "请根据下面当前题目的公开方法、协议、样例和输入，实现一个完整的 Python 3.9 标准库算法。\n"
        "只返回一个完整的 ```python 代码块，不要解释、不要省略函数、不要返回补丁、不要拆成多个版本。\n"
        "源码必须定义 run_experiment(request)，不得联网、读写文件、读取隐藏标签、访问模型凭据或打印日志。\n"
        "仅使用算法所需标准库，如math、collections、re、heapq、itertools、random、statistics、time；"
        "不使用动态加载、反射、私有运行时属性或第三方包。\n"
        "严格遵守 protocol.response_schema；这是算法内部返回，不是最终 Reproduction 答卷。\n\n"
        "生成时间受限：源码保持紧凑，D-01/D-05目标约2200 token，D-02约2600 token；不要注释、类型注解、解释或重复包装；"
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
