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
    return (
        '只返回一个完整Python 3.9标准库代码块，定义run_experiment(request)，目标2200至2800 token。'
        '禁止打印、网络、文件、凭据、第三方库、反射和动态加载。只输出实验接口要求的内容，严禁预置排名或伪造结果。\n'
        '每次请求按当前语料分别预计算正文和标题的文档Counter、df、长度和平均长度。N包含所有文档，'
        '空字段长度为0，仅平均长度为0时取1。所有阶段共用rank(查询列表,配置)，取q.text分词，以q.id作键。'
        '分数=正文BM25+title_weight×标题BM25。文档Counter提供词频，重复查询词重复贡献，'
        '未知词为0。返回全部文档并按(-score,id)排序。\n'
        'phase=rank必须先于任何开发集处理，使用data.selected，仅返回{candidate:测试排名}。'
        'phase=run以protocol.default_config生成基线，遍历protocol.grid，每配置只算一次开发排名和指标，'
        '保留每个search记录并复用选中配置。baseline和candidate都返回test_queries的排名字典，开发排名仅在details.search。\n'
        'Recall@5用百分制，MRR@10用0到1。search行保存按查询直接平均的recall_at_5_percent和mrr_at_10，'
        'group_metrics保存各组及组宏均值。用min和单一比较键'
        '(-round(worst_mrr,12),-round(macro_mrr,12),-round(macro_recall,12),title_weight,k1,b)。'
        '候选未完成时诚实返回null。\n'
        f'当前题号：{case.case_id}\n当前题目输入：{case.input}\n'
        f'method.md：\n{materials["method"]}\n'
        'protocol.json：\n' + json.dumps(json.loads(materials['protocol']), ensure_ascii=False, separators=(',', ':'))
        + '\nsample.json：\n' + json.dumps(json.loads(materials['sample']), ensure_ascii=False, separators=(',', ':'))
    )
