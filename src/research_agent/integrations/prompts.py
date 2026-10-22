"""分阶段模型提示词；版本写入模型调用记录。"""

PROMPT_VERSION = "20261022-v4"

instructions = {
    "chat": "回答科研问题，输出 answer 和一个 clarifying_question。",
    "query": "解析检索需求。输出 topic、methods、focus 字符串，exclusions 排除条件短语数组、variants 中英文同义术语数组，以及 subqueries 独立英文子问题数组。若题目为中文而论文可能为英文，variants 优先给出 4 至 6 个精确英文研究术语，覆盖题目指定的每个方法、机制和实体；保留 ChEC-seq 等原文专名，不只重复中文。对于 Literature_Retrieval 且用户要找多个不同主题的论文，subqueries 每个明确不同的目标单独写一条 3 至 8 词的英文检索短语，最多八条；优先覆盖全部目标，不要把同一方向拆为数条相似子问题而漏掉后续目标。其他题型可返回空数组。排除条件不要混入 topic 或 subqueries。",
    "retrieve": "根据题目和当前批次 candidates 中的论文标题、摘要及片段判断每篇是否真正相关。只能返回当前 candidates 中的完整 filename 和该论文提供的 chunk_id，不要从题目或先前批次猜测文件名。排除条件只在论文自身的核心研究对象或贡献属于排除项时生效；若排除项仅被用作相关工作、背景或实验对照，不应因此排除论文。对多目标问题逐个目标判断。输出 papers 数组，每项 filename、reason、chunk_ids（支持理由的片段编号数组）。可以返回空数组，禁止只按文件名判断。",
    "extract": "本次片段来自同一篇论文。提取与题目有关的5至7条互不重复的实质事实，分别覆盖本篇核心方法的不同组成或变体、实验任务、命名数据集、比较基线、结果及适用限制；证据不足时允许少于5条或返回空数组，绝不能凑数。各方法变体分别成条，不要只泛称提出框架。没有原文支持的维度直接跳过。不要把本篇在相关工作或表格中引用的其他论文方法与结果当成本篇贡献。输出 claims 数组，每项 claim（单个事实，最多100字）、chunk_id、quote（必须连续摘自该片段的原文，12至180字符；不要用省略号跳接两段）。不得把猜测或题目模拟观察当论文结论。",
    "report": "撰写约1200至1800个中文字符的 Markdown 正文，至少900字符，按题目比较方法、数据、指标、结论和局限。Survey 应优先写每篇自己的方法与实验事实；不要把另一篇论文对某模型的基线描述归因为该模型原始论文的方法定义。若 claims 支持同一论文的多种形式，逐一解释。Idea 必须涵盖动机、技术路线、基线、具体命名的基准数据集、实验与消融、量化指标、多文献互补、可行性、失败边界与可证伪方式。若证据中有命名数据集，可明确将其列为拟使用的基准，并说明适配；没有时必须标注为待选，不能假称论文使用。论文事实只能改写给定 claims 明确写出的内容；不要补充 claims 未包含的数据集名称、数值、基线或实验细节。每个事实段都使用其 ref 和 page 生成严格格式的 [n, p.x] 引用，页码必须等于对应 claim 的 page，不加问号。所有实测数字必须来自 claims。不能把证据中未提到的内容说成论文没有做，只能说给定证据未报告。待验证的新假设、技术机制、设计指标与预期效果每段明确标为建议/待验证，不写成论文结论。题目模拟观察独立注明来源为题目，不附论文引用。若 repair_issues 非空，逐条删除或修正无证据语句。不要生成参考文献节（程序会添加）。输出 markdown 字符串和 used_evidence_ids 数组；后者必须包括正文每篇引用对应的证据 ID。",
    "audit": "审查证据边界和题目覆盖。论文既有事实必须受 claims 支持；题目模拟观察的来源是 task.input，不需要论文证明。Idea 中明确标注为建议/假设/待验证的新方法、基线、数据选择和指标设计允许原创，不要求现有论文已经实施；只检查是否错误宣称已实测、已证实或归给论文。'给定证据未报告'不能误判为'整篇论文未报告'。多个事实可以引用同一页，重复主题可以概括，但不得虚构差异或互补。只有确凿的事实/数字/归因/页码错误，或遗漏题目明确要求，才令 ok=false。E 不强制引用每篇论文或额外表格。输出 ok 布尔值和 issues 数组（最多5条，每条给出问题原句及不支持的具体依据，别加入无依据的审查条件）。",
}
instructions["audit"] += "同时对照 source_quotes：即使 claim 有原文摘录，也要核对其解释是否超出摘录，不把本篇相关工作当自身贡献。研究设计中的置信区间包含零只能说明证据不足，不可直接宣称无效或证明因果假设错误。"
instructions["report"] += "Idea给出拟议搜索范围、重复次数、预算上限和统计比较方式，明确它们是待验证设计。缺乏授权医学等领域数据时，提出具体筛选/获得步骤，不冒称已经取得。正文控制在约1800中文字符，少复述论文事实，集中说明设计与证据关系。"
instructions["retrieve"] += "papers只收相关论文，不要把排除项放进papers再在reason中写排除。每项额外给relevant=true；不相关论文直接省略。"
instructions["repair"] = "只修正issues指出的科研报告错误，其他内容不改。返回patches数组，每项original是paragraphs中连续且逐字相同的原句或原段落，replacement是修正文本；可删除无依据语句。最多5处，逐项解决全部已知问题；事实只用给定claims，不改页码或添加未支持数值。不得输出整篇markdown。"
instructions["audit"] += "若提供applied_patches，只审查变更是否受证据支持、previous_issues是否全部解决；不要因局部变更未重述全文要素而拒绝。允许合理同义改写，不因没有重复论文主语或非必要措辞差异而判为事实错误。"
INSTRUCTIONS = instructions

INSTRUCTIONS["retrieve"] += (
    "每项给decision=include/exclude/uncertain；include必须有当前chunk_ids支持。"
    "明确不相关项用exclude，证据不足用uncertain，不能写include再在理由中否定。")
INSTRUCTIONS["extract"] += (
    "每条额外给facet=method/data/metric/result/limitation/unspecified之一，"
    "role=own_work/related_work/unknown之一。claim保留quote中的限定范围、比较对象和不确定性，"
    "优先选择题目涉及维度，最多5条。")
INSTRUCTIONS["verify_claims"] = (
    "逐条核查claim是否被对应quote和context支持。只判断给定原文，不借助常识补全。"
    "严格区分本篇工作、相关工作、提出的可能性和真实实验；检查否定、条件、比较对象和数值。"
    "输出claims数组，逐个覆盖全部输入evidence_id且不重复，每项含evidence_id、"
    "verdict=supported/unsupported/uncertain、facet=method/data/metric/result/limitation/unspecified、"
    "role=own_work/related_work/unknown和简短reason。不能把摘录存在当成解释正确。")
INSTRUCTIONS["report"] += (
    "先围绕coverage_requirements组织正文。Survey按比较维度综合，解释不同实验条件为何不可直接横比。"
    "Idea依次说明待验证假设、模块输入输出、文献互补、基线和数据、实验与消融、指标、预算与证伪条件。"
    "evidence_coverage中的gaps表示当前证据缺口，不等于整篇论文未做。"
    "verification=verbatim_only的条目仅是原文摘录，对其改写必须保留原文限定，不扩展为额外结论。")
INSTRUCTIONS["audit"] += (
    "若提供full_document，额外检查修订是否导致全篇要求缺失、前后矛盾或比较对象变化；"
    "事实支持仍重点审查变更及previous_issues，不要求未改段落重复所有设计要素。"
    "论文数字所在同段出现建议字样，不能免除该数字的原文支持要求。")
INSTRUCTIONS["report"] = INSTRUCTIONS["report"].replace(
    "每个事实段都使用其 ref 和 page 生成严格格式的 [n, p.x] 引用，页码必须等于对应 claim 的 page，不加问号。",
    "每个论文事实段使用对应evidence_id生成内部引用标记[E:ev-数字]，多条写[E:ev-1,ev-2]。"
    "不得自行生成论文编号或页码，程序根据台账渲染最终引用。")
INSTRUCTIONS["repair"] += (
    "新增或更改引用时使用对应证据标记[E:ev-数字]，程序生成最终页码；"
    "原段落中无需更改的有效引用可保留。original必须逐字复制当前paragraphs，不能改写原句。")
