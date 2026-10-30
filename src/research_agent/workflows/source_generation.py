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
D_SOURCE_PROMPT_VERSION = "20261026-d-candidate-first-v1"


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
    source_target = {'D-01': '1400至2000', 'D-02': '1500至2200', 'D-05': '1200至1800'}.get(case.case_id, '2000至3000')
    protocol_text, sample_text = materials['protocol'], materials['sample']
    final_check = ''
    if case.case_id == 'D-01':
        boundary = ('基线必须是训练标签的多数类常量预测；最高频并列时选字典序最小的标签，'
                    '为data.texts中每条文本重复该标签。禁止用输入顺序、Counter首次出现顺序或候选预测代替基线。\n')
        boundary += (
            '实现核对：先只用train建立排序词表及平滑IDF，再编码train/texts；'
            'raw词频乘IDF后L2归一化，最后追加常数bias，未知词丢弃，空文本只剩bias。'
            '用稀疏的(列号,值)保存每条特征以避免每轮遍历所有零值。'
            '零初始化W，每epoch先用同一旧W累计全部样本的softmax梯度，再统一更新；'
            'softmax先减去最大logit；梯度除训练样本数，所有权重含bias都加penalty*W。'
            '不能改用SGD、朴素贝叶斯、类别重加权或提前停止；epochs/learning_rate/penalty读取protocol。'
            'details直接返回实际classes/vocabulary/idf/weights，不能嵌套model；预测与这些权重一致。\n')
    elif case.case_id == 'D-05':
        final_check = (
            '\nD-05交付前检查：served_ms必须初始化为{flow:0.0 for flow in data["flow_weights"]}，'
            '然后只累计trace中的service_ms。禁止从jobs或trace提取流名初始化字典；'
            '即使jobs为空，全部声明流都必须保留为0。拒绝任务后当前服务器继续尝试，'
            '公平优先级每次从最新allocated计算，成功派发立刻增加allocated；不要缓存旧优先级。'
            '退出循环后遍历原始jobs，把不在trace/dropped中的id全部记horizon，'
            '必须覆盖尚未到达的未来任务，不能只遍历waiting。只返回完整简洁代码，禁止长注释。\n')
        boundary = ('公开协议边界核对：同一时刻先处理所有到达/完成事件，再按服务器编号派发；'
                    '优先队列的时间相同时必须有可比较的稳定标识，绝不能让Python比较任务dict。'
                    '拒绝任务后该空闲服务器继续尝试等待任务，不能直接跳过服务器；'
                    '公平累计在派发时预留；无任务和空闲间隔能正常退出/推进。'
                    'baseline和candidate共享一个清晰的状态推进函数，避免重复事件和多套矛盾状态。\n')
        boundary += (
            '采用简洁实现：服务器数量很小，用数组记录busy_until和last_flow，等待任务用list。'
            '每次派发都以最新allocated重新用min选任务；不要使用堆缓存到达时的公平优先级，'
            '否则派发后其他等待任务的优先级过期；无需heapq、completed集合或重复的事件释放逻辑。'
            'now从0开始且只在now<horizon内派发；每轮先纳入arrival<=now全部任务并清理deadline<=now。'
            '按server升序，每个空闲server用while不断选任务；拒绝只移除任务，不更新last_flow、busy_until或allocated。'
            '成功派发才更新上述状态并立刻增加allocated，使用实际正flow_weight相除，不能将小于1的权重夹到1。'
            '一轮后now跳到严格大于当前时刻的最早未来到达或busy_until；绝不把空闲server的旧时间加入最小值。'
            '到horizon立即停止，所有尚未处理任务（包括未来到达或此时已过期）记horizon；'
            '不能在窗口之后再判deadline。trace是已确认end<=horizon的完成任务，退出循环不能漏计这些服务。'
            'dropped只含id/reason；trace保留全部原字段；served_ms包括零服务流。\n')
    elif case.case_id == 'D-02':
        protocol_text, sample_text = _retrieval_prompt_materials(protocol_text, sample_text)
        final_check = (
            '\nD-02交付前检查：每次得分都必须使用当前query词项。选参直接取最小的六元组'
            '(-round(worst_mrr_at_10,12),-round(macro_mrr_at_10,12),'
            '-round(macro_recall_at_5_percent,12),title_weight,k1,b)。'
            '前三项为负，后三项为正；切勿用max同时最大化六项。'
            '例如三项指标全相等时，title_weight=0优先于2、k1=0.4优先于2、b=0优先于1。'
            'rank必须在进入grid搜索之前提前返回；公共指标计算只写一个小函数，'
            '对全部开发query调用一次，对每个group分别调用一次，以免重复冗长逻辑。'
            'search行顶层recall_at_5_percent/mrr_at_10必须来自全部dev_queries的普通均值，'
            '禁止把macro_recall/macro_mrr填到这两个字段；宏平均只放group_metrics。'
            '例如一组1个query的MRR=1，另一组2个query的MRR均为0.5时，'
            '普通MRR=2/3，宏MRR=0.75，二者不等。空relevant也计入查询数，贡献0。'
            'grid和default_config必须读取request["protocol"]，不要把样例的27个配置或默认参数抄成源码常量。'
            '只写紧凑源码，不要注释。\n')
        boundary = ('接口边界核对：run的baseline/candidate仅包含test_queries；'
                    'dev_queries排名只存details.search，不混入正式预测。rank按data.selected排序，不能重选参。'
                    '每个query完整排列所有documents；开发搜索逐项覆盖protocol.grid并按method的字典序规则打破并列。\n')
        boundary += (
            '实现核对：标题/正文分别预计算每文档Counter、长度、df、avglen和正IDF；'
            '统计可在本次请求内复用，不能跨语料复用。IDF必须用method中的log(1+...)，'
            '不能照搬其他BM25库的负IDF截断。查询保留重复词，空字段仍计入N，avglen=0取1。'
            '评分循环遍历query的词项，f从当前document的Counter读取；禁止遍历所有文档词项而忽略query。'
            '开发集每组独立算Recall@5百分制及MRR@10，再对组等权平均；普通查询均值另外报告。'
            'search每行包含config、完整开发rankings、recall_at_5_percent、mrr_at_10及group_metrics。'
            '选参用min比较(-round(worst_mrr,12),-round(macro_mrr,12),-round(macro_recall,12),'
            'title_weight,k1,b)，不能混淆最大化指标与最小化参数。baseline用protocol.default_config。\n')
    return (
        "请根据下面当前题目的公开方法、协议、样例和输入，实现一个完整的 Python 3.9 标准库算法。\n"
        "只返回一个完整的 ```python 代码块，不要解释、不要省略函数、不要返回补丁、不要拆成多个版本。\n"
        "源码必须定义 run_experiment(request)，不得联网、读写文件、读取隐藏标签、访问模型凭据或打印日志。\n"
        "仅使用算法所需标准库，如math、collections、re、heapq、itertools、random、statistics、time；"
        "不使用动态加载、反射、私有运行时属性或第三方包。\n"
        "严格遵守 protocol.response_schema；这是算法内部返回，不是最终 Reproduction 答卷。\n\n"
        "首次就完成baseline、candidate及必要details；不要先交只有基线的版本等待下一次升级。\n"
        f"生成时间受限：本题源码尽量控制在{source_target} token；省略长注释、类型注解和重复包装；"
        "使用共享小函数实现基线和候选，仍须覆盖协议所有阶段和边界。"
        "不要为了行数或长度目标删掉训练、选参、调度规则或details；确实无法完成候选时才按协议返回null，不能伪造轨迹或预测。\n"
        "下面的当前method/protocol是规则来源；实现建议如与当前材料冲突，以当前材料为准。\n"
        + boundary +
        f"当前题号：{case.case_id}\n当前题目输入：\n{case.input}\n\n"
        f"method.md：\n{materials['method']}\n\n"
        f"protocol.json（算法相关字段）：\n{_compact_json(protocol_text)}\n\n"
        f"sample.json（仅演示接口，可能为节选；不能硬编码这些数据）：\n{_compact_json(sample_text)}\n"
        "\n现在直接返回一个完整代码块。不要输出任何注释、说明、推理或未使用变量。"
        "baseline和candidate必须共享核心计算函数，只改变协议要求的策略或参数。"
        f"目标{source_target} token，优先简洁正确，所有用到的模块必须导入。\n"
        + final_check
    )


def _retrieval_prompt_materials(protocol_text, sample_text):
    """D-02上下文突出算法接口；完整原材料仍用于自检及正式执行。"""
    try:
        protocol, sample = json.loads(protocol_text), json.loads(sample_text)
        # 仅移除外层答卷/计分/资源说明，保留所有算法参数、阶段和等价规则。
        omitted = {'metrics', 'plot_labels', 'figure_caption', 'scoring', 'runtime_scope',
                   'output_scope', 'aggregate_scope', 'resources'}
        protocol = {key: value for key, value in protocol.items() if key not in omitted}
        data = sample.get('data', {})
        if 'documents' in data and 'dev_queries' in data and 'test_queries' in data:
            dev = data['dev_queries'][:2]
            required = {key for query in dev for key in query.get('relevant', [])}
            required.update(doc['id'] for doc in data['documents'][:3])
            sample = {**sample, 'data': {**data,
                'documents': [doc for doc in data['documents'] if doc['id'] in required],
                'dev_queries': dev, 'test_queries': data['test_queries'][:2]}}
        return json.dumps(protocol, ensure_ascii=False), json.dumps(sample, ensure_ascii=False)
    except (ValueError, TypeError, KeyError):
        return protocol_text, sample_text


def _compact_json(text: str) -> str:
    """仅压缩材料中的JSON空白，完整保留当前题算法规则及样例值。"""
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return text


def save_generation_response(response: dict, destination: Path) -> None:
    """保存完整模型响应，便于审计，不保存请求头或密钥。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
