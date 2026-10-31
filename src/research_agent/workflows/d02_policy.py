"""D-02-only source instructions; no embedded implementation or measurements."""
import json


def source_prompt(case, materials, *, baseline_only=False):
    if baseline_only:
        return (
            '仅生成本次完整基线恢复源码：Python 3.9标准库，唯一python代码块，定义run_experiment(request)。'
            '本次候选未完成，如实返回null，不实现grid搜索、开发集指标或group_metrics。目标900至1200 token。'
            'phase=run仅返回{baseline:测试排名字典,candidate:null,details:{}}，基线配置取protocol.default_config。'
            'phase=rank仍必须返回且只返回{candidate:排名}，配置取data.selected；rank字段名不代表候选选参已完成。'
            '两个阶段共用一个排名函数，接收查询对象列表，对每个q取q.text分词并以q.id作键；不能直接Counter(q)。'
            '两字段独立统计并预计算文档Counter、df、长度和平均长度；文档空长度保持0，只有平均长度0时按1处理。'
            '总分严格为正文BM25 + title_weight乘标题BM25，重复查询词重复贡献；按(-score,id)排序并返回全部文档。'
            '不读dev标签、不访问文件/网络/凭据，不输出指标或图表。下面公开材料中的候选步骤本次不实现。\n'
            f'当前题号：{case.case_id}\nmethod.md：\n{materials["method"]}\n'
            + 'protocol.json：' + json.dumps(json.loads(materials['protocol']),ensure_ascii=False,separators=(',',':'))
            + '\nsample.json：' + json.dumps(json.loads(materials['sample']),ensure_ascii=False,separators=(',',':'))
            + '\n只交上述基线和独立rank的完整源码；不要复制上次候选选参逻辑。'
        )
    scope = (
        '实现完整基线和候选。run中遍历protocol.grid，每种配置只计算一次开发排名和指标，'
        '同时保存search行并选择最优配置，不要选完再重算整个grid。目标2200至2800 token。'
    )
    return (
        '根据当前题公开材料生成完整Python 3.9标准库源码，仅返回唯一python代码块。'
        '定义run_experiment(request)，不打印、不联网、不读写文件、不读凭据、不使用第三方库、反射或动态加载。'
        '不输出最终答卷、图表或实测吞吐，不伪造结果。代码紧凑无注释但所有函数必须完整。\n'
        + scope + '\n'
        '实现组织是本题关键：先预处理两字段的文档Counter、df、长度、平均长度；再写唯一一个rank(查询列表,配置)辅助函数。'
        'run基线、每种开发配置、候选测试和独立rank请求全部调用这一函数。'
        '禁止为dev/test/rank复制第二份打分循环；重复实现曾导致标题误用正文平均长度、rank漏掉长度归一化。'
        '排名内使用预先计算的Counter，不能list.count或重新扫描文本；rank重复调用不得重新选参或读取dev标签。'
        '字段统计分别保存，文档空字段长度保留0，只有全体平均长度为0才按1处理。N包括所有文档。'
        'BM25频率f来自文档，查询重复词按查询词频乘贡献。查询对象先取q.text分词，不能直接Counter(q)。'
        '总分严格为正文BM25 + title_weight乘标题BM25，title_weight必须实际乘入，不能只当形参。'
        '未知词为0，返回全部文档并按(-score,id)排序。'
        'phase=rank在开发集处理之前返回，仅有candidate一个字段，配置来自data.selected；每次请求按当前语料重新统计。'
        'phase=run使用default_config基线，遍历grid完成候选；baseline/candidate均为test_queries的排名字典。'
        '另用单个指标函数接收开发排名与标签：Recall只取前5且为百分制；MRR只取前10且为0到1。'
        'search行存每条查询直接平均的recall_at_5_percent/mrr_at_10，group_metrics存各组指标和组宏均值。'
        '例如组A两条Recall为100、100、组B一条为0，普通均值200/3，组宏均值50，二者不能混用。'
        '每种配置只计算一次开发排名和指标，保存独立search行，遍历后直接复用选中配置。'
        '选参仅比较round(worst_mrr,12)、round(macro_mrr,12)、round(macro_recall,12)降序，'
        '再(title_weight,k1,b)升序；用max时后三项必须取负，用min时前三项取负，不能六项全max。'
        '只构造一个比较键，少写重复分支；不输出冗长注释，尽量用约100行完整实现。'
        '候选无法完成时允许诚实null，绝不能伪造search或预置固定排名。原始方法与字段如下。\n'
        f'当前题号：{case.case_id}\n当前题目输入：{case.input}\n'
        f'method.md：\n{materials["method"]}\n'
        'protocol.json：\n' + json.dumps(json.loads(materials['protocol']), ensure_ascii=False, separators=(',', ':'))
        + '\nsample.json：\n' + json.dumps(json.loads(materials['sample']), ensure_ascii=False, separators=(',', ':'))
        + '\n最终检查：必须实现run和rank两个不同分支。sample展示run，但官方会独立调用rank。'
        'rank分支必须在访问dev_queries和遍历grid之前返回，仅返回一个candidate字段。'
        'rank不允许重新选参，即使请求含开发数据；配置必须来自data.selected。'
        'run的baseline/candidate都对应test_queries，开发排名只在details.search。' + scope
    )
