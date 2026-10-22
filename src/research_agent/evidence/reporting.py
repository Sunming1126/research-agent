from __future__ import annotations
import hashlib
import re
from collections import defaultdict
from ..core.models import Chunk, Evidence
from ..core.utils import source_quote_matches
from ..rag.retrieval import QueryPlan
from . import EvidenceError


def render_evidence_citations(markdown, evidence):
    """模型只选证据编号，文件编号与物理页码由已核验台账生成。"""
    known = {e["evidence_id"]: e for e in evidence}
    used = []

    def replace(match):
        ids = [value.strip() for value in match.group(1).split(",")]
        if any(value not in known for value in ids):
            raise EvidenceError("draft contains unknown evidence citation", passages=[match.group()])
        citations = []
        for value in ids:
            row = known[value]
            cite = f"[{row['ref']}, p.{row['page']}]" if row['page'] is not None else f"[{row['ref']}]"
            if cite not in citations:
                citations.append(cite)
            if value not in used:
                used.append(value)
        return "".join(citations)

    rendered = re.sub(r"\[E:(ev-\d+(?:\s*,\s*ev-\d+)*)\]", replace, markdown)
    if "[E:" in rendered:
        raise EvidenceError("malformed internal evidence citation")
    return rendered, used

def repair_paragraphs(markdown, issues):
    """优先定位审查给出的原句；定位失败时交付正文，不猜测待替换位置。"""
    paragraphs = markdown.split("\n\n")
    fragments = re.findall(r"[“「\"]([^”」\"]{8,})[”」\"]", "\n".join(issues))
    selected = [p for p in paragraphs if any(fragment in p for fragment in fragments)]
    return selected or paragraphs


def apply_report_patches(markdown, patches):
    """只接受当前稿中唯一出现的原段落，禁止模型追加整篇或修改未知文本。"""
    if not isinstance(patches, list) or not 1 <= len(patches) <= 5:
        raise EvidenceError("report repair requires one to five exact patches")
    result = markdown
    for patch in patches:
        if not isinstance(patch, dict):
            raise EvidenceError("invalid report patch")
        original, replacement = patch.get("original"), patch.get("replacement")
        if (not isinstance(original, str) or len(original) < 8 or not isinstance(replacement, str)
                or len(replacement) > len(original) * 3 + 300 or result.count(original) != 1):
            raise EvidenceError("report patch does not match a unique current passage")
        result = result.replace(original, replacement, 1)
    if result == markdown:
        raise EvidenceError("report repair made no change")
    return result


def report_requirements(function):
    """生成和审查共用研究要素，避免只检查引文而遗漏可执行实验设计。"""
    if function == "Idea":
        return ["分开标记论文事实、题目观察、新假设", "指定论文之间的具体互补接口和潜在冲突",
                "列出无新增机制、单机制和完整方案基线，说明公平的预算控制",
                "从证据选择具体命名基准并引用；若无命名证据，明确待选及筛选标准",
                "训练/验证/测试划分及防泄漏", "消融、主要指标、成本指标与失败判据",
                "给出拟议参数搜索范围、重复次数和统计检验；标注为待验证而非实测",
                "置信区间含零只能说明证据不足，不可据此证实无效或否定因果"]
    return ["逐篇说明方法组成和证据支持的变体", "比较实验任务、数据、指标、结果和适用边界",
            "区分原论文结论与跨论文推断", "证据缺口明示待核实，不能推断全文未做"]


def report_evidence_chunks(retriever, plan):
    """在主题召回之外补取方法、实验与局限；不依赖固定论文名称或领域。"""
    selected = [c for c in retriever.per_document(plan, 5) if c.filename.lower().endswith(".pdf")]
    if not hasattr(retriever, "search"):
        return selected
    seen = {c.chunk_id for c in selected}
    facets = ("method mechanism approach architecture formulation 方法 机制 模型",
              "experiment dataset benchmark evaluation results baseline 实验 数据集 基准 评测 结果",
              "limitations discussion assumptions failure computational cost 局限 假设 失败 计算 成本")
    for filename in sorted({c.filename for c in retriever.chunks if c.filename.lower().endswith(".pdf")}):
        for facet in facets:
            hits = retriever.search(QueryPlan(facet), top_k=12, filename=filename)
            candidate = next((h.chunk for h in hits if h.chunk.chunk_id not in seen
                              and h.chunk.kind == "text" and len(h.chunk.text.strip()) >= 80), None)
            if candidate:
                selected.append(candidate)
                seen.add(candidate.chunk_id)
    return selected


def fallback_evidence_chunks(chunks, filename, maximum=32):
    """从单篇论文前页补取可摘录正文，兼容按行切分的 PDF。"""
    # 部分论文每行只有约百字；250 字门槛会把整篇摘要错误过滤掉。
    candidates = [chunk for chunk in chunks if chunk.filename == filename and chunk.kind == "text"
                  and chunk.page is not None and chunk.page <= 3 and len(chunk.text.strip()) >= 80
                  and "@" not in chunk.text[:160]]
    return sorted(candidates, key=lambda chunk: (chunk.page, chunk.start))[:maximum]


def resolve_quote_source(chunk, quote, group):
    """核验逐行 PDF 的跨行摘录，并为同页拼接文本建立可追溯的临时片段。"""
    if chunk is None or not isinstance(quote, str) or len(quote) < 12:
        return None
    if source_quote_matches(quote, chunk.text):
        return chunk
    neighbors = sorted((item for item in group if item.filename == chunk.filename and item.page == chunk.page),
                       key=lambda item: item.start)
    if len(neighbors) < 2:
        return None
    combined = " ".join(item.text for item in neighbors)
    if not source_quote_matches(quote, combined):
        return None
    digest = hashlib.sha256((chunk.filename + str(chunk.page) + combined).encode("utf-8")).hexdigest()
    return Chunk(digest[:20], chunk.filename, chunk.page, chunk.heading, combined, "text", digest,
                 neighbors[0].start, neighbors[-1].end, chunk.document_sha256)


def model_evidence_rows(evidence):
    """摘录已在本地核验；写作阶段只传事实、论文和页码，节省模型预算。"""
    fields = ("evidence_id", "claim", "filename", "page", "ref")
    return [{**{key: row[key] for key in fields},
             **{key: row[key] for key in ("facet", "role", "verification") if key in row}} for row in evidence]


def remove_unsupported_authors(markdown, evidence):
    """删去无证据的英文姓氏归因，保留对应论文的事实和引文。"""
    supported = " ".join(str(row.get("claim", "")) for row in evidence).casefold()

    def replace(match):
        surname = match.group(1)
        return match.group(0) if surname.casefold() in supported else "该论文"

    return re.sub(r"(?<![A-Za-z])([A-Z][a-z]{2,})等人", replace, markdown)


def correct_paper_ids(markdown, filenames):
    """仅对唯一的一位编号笔误做确定性修正，避免错误引用另一篇论文。"""
    known = {name[:-4] for name in filenames if re.fullmatch(r"\d{4}\.\d{5}\.pdf", name)}

    def replace(match):
        candidate = match.group()
        if candidate in known:
            return candidate
        nearby = [value for value in known if sum(a != b for a, b in zip(value, candidate)) == 1]
        return nearby[0] if len(nearby) == 1 else candidate

    return re.sub(r"(?<!\d)\d{4}\.\d{5}(?!\d)", replace, markdown)


def _cited_fact(text, cite):
    """PDF 换行不应拆断数字与页码；每个已绑定事实句各自带引用。"""
    text = re.sub(r"\s+", " ", text.strip())
    return " ".join(part.strip() + " " + cite for part in
                    re.split(r"(?<=[。！？])\s*(?!\s*\[)", text) if part.strip())


def render_claim_ledger_section(ledger, refs):
    """把通过原文摘录核验的事实转为带物理页码的综述摘要。"""
    lines, used = ["## 逐篇可回查事实"], []
    grouped = defaultdict(list)
    for item in ledger:
        grouped[item.filename].append(item)
    for filename in sorted(grouped, key=lambda name: refs[name]):
        lines.append(f"### 论文 {refs[filename]}：{filename}")
        for item in grouped[filename]:
            cite = f"[{refs[filename]}, p.{item.page}]" if item.page is not None else f"[{refs[filename]}]"
            lines.append("- " + _cited_fact(item.claim, cite))
            used.append(item.evidence_id)
    return "\n".join(lines), used


def deterministic_idea(topic, ledger):
    """模型修复耗尽时，基于真实证据生成可证伪而不冒充实测的研究方案。"""
    filenames = sorted({item.filename for item in ledger})
    if not filenames:
        raise EvidenceError("Idea fallback requires verified evidence")
    refs = {name: index for index, name in enumerate(filenames, 1)}
    anchor, used = render_claim_ledger_section(ledger, refs)
    specific = specific_idea_sections(ledger, refs)
    sections = [
        "# 基于指定论文的待验证研究方案",
        "## 研究问题与动机",
        "题目给出的观察或问题是：" + topic[:220] + "。这一输入只定义研究目标，不等于已经得到的论文结论或本次真实实验结果。"
        "可检验的主问题是：在统一的数据划分、资源预算和评价指标下，将指定论文讨论的机制组合或对照，"
        "是否比单独使用某一机制更好地解释目标现象？下面的研究方案均标为待验证；各篇论文已有事实仅列于可回查证据节。",
        anchor,
        "## 技术路线与论文互补关系（待验证）",
        "建议把每篇论文已核验的机制分别作为候选模块或对照条件，而不是假称这些论文已经联合验证同一系统。"
        "先画出输入、状态、信息流、预测和反馈的模块图，逐一标注哪些环节来自论文事实，哪些连接是本方案新提出的假设。"
        "对于证据不足的模块关系，只设计接口与可测变量，不预先宣称存在协同效果。"
        "实现顺序建议从能独立运行的最小基线开始，再逐项加入其他机制；每次加入都保存代码版本、参数、随机种子和输入输出。",
        specific,
        "## 基线、数据与实验设计（待验证）",
        "首先建立最简单的无新增机制基线，以及各论文机制可独立运行的单模块基线。"
        "数据优先采用题目明确授权或文献证据明确命名且可合法获得的集合；若当前证据没有给出可复现数据集名称，则将具体数据集标为待选，"
        "并在开始实验前固定来源、划分方式、排除重复或泄漏的方法。"
        "实验至少包括单模块、两模块组合、完整方案三组，并用相同训练量、输入条件和评价脚本比较。"
        "对关键模块做移除消融，对阈值、样本量和噪声强度做敏感性分析；保留失败运行和异常样本，避免只报告最优结果。",
        "## 指标、可行性与瓶颈（待验证）",
        "建议预先指定一个主要效果指标和两个辅助指标：任务准确性或拟合误差、运行时间或资源消耗、以及跨条件稳定性。"
        "如研究对象是随机动态系统，还应比较状态转换时间分布和多次重复运行的方差；若是文献检索问题，"
        "则应区分召回、证据相关性与最终答案质量。所有指标的方向、单位、置信区间计算方法应在实验前固定。"
        "可行性以现有授权材料、可运行代码和有限计算预算为边界；主要瓶颈可能是不同论文的任务定义、参数尺度、数据协议和随机性处理不一致。"
        "在这些边界未对齐时，只报告各自体系内的结果，不直接横向相减。",
        "## 失败边界与证伪标准（待验证）",
        "若完整方案在预注册的主要指标上未稳定优于最强单模块基线，或优势只出现在某个未预先指定的子集，"
        "应视为核心互补假设未得到支持。若性能提升伴随不可接受的时间或资源开销，需报告质量与成本权衡。"
        "若移除某模块后结果不变，则该模块的必要性假设被削弱；若更换数据划分或随机种子后结论翻转，"
        "则稳健性假设不成立。复现实验应公开命令、配置、原始指标与图表，并把题目模拟观察、论文已有事实和本次新结果分开写明。",
    ]
    references = "\n".join(f"[{refs[name]}] {name}" for name in filenames)
    return "\n\n".join(sections) + "\n\n## 参考文献\n\n" + references, used


def specific_idea_sections(ledger, refs):
    """用逐篇已核验事实写出具体角色与候选基准，防止保底方案流于空泛。"""
    grouped = defaultdict(list)
    for item in ledger:
        grouped[item.filename].append(item)
    roles = ["## 指定论文的分工与接口（待验证）"]
    for name in sorted(grouped, key=lambda filename: refs[filename]):
        claim = grouped[name][0]
        role = ("验证约束" if re.search(r"约束|证明|边界|不能|代价", claim.claim)
                else "候选机制或模型")
        cite = f"[{refs[name]}, p.{claim.page}]" if claim.page is not None else f"[{refs[name]}]"
        roles.append(f"- 论文 {refs[name]}（{name}）作为{role}：{_cited_fact(claim.claim, cite)} "
                     "建议记录其输入条件、输出状态和可测量变量，再检验与其他模块连接是否有意义；连接本身尚未验证。")
    roles.append("建议接口顺序为：先由各候选机制输出状态或预测，再将输出送入验证约束，最后用共同指标比较；"
                 "若论文任务不同，先建立映射表并报告不能对齐的变量，不能直接声称模型能够无缝耦合。")
    benchmarks = ["## 具体候选基准（待验证）"]
    selected = []
    for name in sorted(grouped, key=lambda filename: refs[filename]):
        for claim in grouped[name]:
            if re.search(r"数据集|基准|benchmark|dataset|实验系统", claim.claim, re.I):
                selected.append((name, claim))
                break
    for name, claim in selected[:3]:
        cite = f"[{refs[name]}, p.{claim.page}]" if claim.page is not None else f"[{refs[name]}]"
        benchmarks.append(f"- 以论文 {refs[name]} 提及的对象作为候选：{_cited_fact(claim.claim, cite)} "
                          "本方案仅建议据此寻找可授权的数据或脚本；实际可用性需逐项核验。")
    if not selected:
        benchmarks.append("当前证据没有明确命名的数据或实验系统；具体基准待取得授权材料后再确定。")
    return "\n".join(roles) + "\n\n" + "\n".join(benchmarks)


def deterministic_survey(topic, ledger):
    """使用逐条核验的事实生成保底综述，不作无依据的跨论文归因。"""
    grouped = defaultdict(list)
    for item in ledger:
        grouped[item.filename].append(item)
    filenames = sorted(grouped)
    if len(filenames) < 5:
        raise EvidenceError("deterministic Survey requires five supported papers")
    refs = {name: index for index, name in enumerate(filenames, 1)}
    paragraphs = [
        "# 文献综述：" + topic[:100],
        "## 研究范围与证据方法",
        "本文仅梳理本题指定的论文，按照研究问题、检索或生成方法、实验设置、评价口径与局限进行比较。"
        "下文的已核验事实均来自当前能够定位的原文片段，紧随事实标出对应论文编号与 PDF 物理页码。"
        "没有进入证据台账的性能数字、数据集细节和方法差异一律不推断；题目中出现的模拟观察也不作为论文结论。"
        "这些限制会使综述偏保守，但便于逐句回查，并为后续补充全文证据留出清晰边界。",
    ]
    used = []
    for name in filenames:
        ref = refs[name]
        paragraphs.append(f"## 文献 {ref}：{name}")
        paragraphs.append(
            "本节记录该论文在当前授权片段中可核验的内容。阅读时应分别确认方法描述、实验条件与结论，"
            "不能把其他论文的设置移到本篇，也不能把某段证据未提到的项目直接判为全文没有实施。"
        )
        for item in grouped[name]:
            cite = f"[{ref}, p.{item.page}]" if item.page is not None else f"[{ref}]"
            paragraphs.append("已核验事实：" + _cited_fact(item.claim, cite))
            used.append(item.evidence_id)
        paragraphs.append(
            "该篇材料与其他论文的横向比较，应在相同任务、语料、检索预算和指标定义下进行。"
            "当前摘录可用于确定本篇的局部事实；若某一比较维度尚无直接证据，应该继续回查全文，"
            "而不是把缺失的摘录解释为实验失败、方法无效或作者未讨论该问题。"
        )
    paragraphs += [
        "## 横向比较与后续验证",
        "这些文献共同涉及从检索到生成的不同环节。可先将上述事实映射到检索器、外部记忆、证据融合和生成控制四个位置，"
        "再在统一的数据划分和检索语料下测试差异。这只是后续研究的比较框架，不是声称五篇论文已有同一套对照实验。"
        "建议同时保存问题、候选段落、最终引用、答案和运行成本，以便判断改善来自召回、筛选还是生成阶段。",
        "局限在于当前证据片段不足以支持所有跨模型结论，也不能直接比较不同论文报告的数值。"
        "若后续补入新的页级证据，可逐条扩充事实台账；对尚未验证的假设，需要独立对照实验。",
    ]
    body = "\n\n".join(paragraphs)
    references = "\n".join(f"[{refs[name]}] {name}" for name in filenames)
    return body + "\n\n## 参考文献\n\n" + references, used


def validate_citations(markdown, evidence, used_ids, minimum=1):
    if len(re.sub(r"\s", "", markdown)) < 800:
        raise EvidenceError("report body shorter than 800 characters")
    known_ids = {e["evidence_id"] for e in evidence}
    if not used_ids or not set(used_ids) <= known_ids:
        raise EvidenceError("report uses unknown or empty evidence IDs")
    supported = defaultdict(set)
    for e in evidence:
        if e["evidence_id"] in used_ids:
            supported[e["ref"]].add(e["page"])
    # 晶向 [111]、区间 [0,1] 等学科记号不是参考文献编号。
    citations = re.findall(r"\[(\d{1,2})(?:,\s*p\.\s*(\d+))?\]", markdown)
    if len({int(ref) for ref, _ in citations}) < minimum:
        raise EvidenceError("too few cited papers")
    for ref, page in citations:
        if int(ref) not in supported or (page and int(page) not in supported[int(ref)]):
            token = f"[{ref}" + (f", p.{page}]" if page else "]")
            passages = [p for p in markdown.split("\n\n") if token in p]
            raise EvidenceError(f"citation [{ref}, p.{page}] has no used evidence; "
                                f"allowed pages: {sorted(p for p in supported.get(int(ref), set()) if p is not None)}",
                                passages=passages[:2])
    # 已发生的数值事实必须附页码；待验证方案交由语义审查区分。
    for paragraph in markdown.split("\n\n"):
        plain_paragraph = re.sub(r"[*#>`]", "", paragraph.strip())
        proposal_heading = re.match(r"^[^。！？]{0,36}[（(](?:建议|待验证|拟议)[^）)]{0,40}[）)][:：]", plain_paragraph)
        sentences = re.split(r"(?<=[。！？])\s*(?!\s*\[)|\n", paragraph)
        for sentence in sentences:
            if not re.search(r"\d+(?:\.\d+)?\s*(?:%|ms|秒|分数|samples/s|queries/s)", sentence):
                continue
            plain = re.sub(r"[*#>`]", "", sentence.strip())
            proposed = re.match(r"^(?:[-*]\s*)?(?:建议|待验证|假设|目标|拟|作为待验证|我们建议|本方案建议)", plain)
            attributed = re.search(r"(?:论文|作者|文献|原文).{0,10}(?:报告|发现|使用|达到|提升|实测|表明)", plain)
            proposed = (proposed or proposal_heading) and not attributed
            if not re.search(r"\[\d+,\s*p\.\s*\d+\]", sentence) and not proposed:
                raise EvidenceError("numeric claim has no page citation", passages=[sentence])
