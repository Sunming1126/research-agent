"""D-02-only source instructions; no embedded implementation or measurements."""
import json


def source_prompt(case, materials, *, baseline_only=False):
    scope = (
        '本次仅实现基线。phase=run返回baseline完整排名、candidate=null、details={}；'
        'phase=rank仍必须返回且只返回{candidate:排名}，配置取data.selected（不可忽略title_weight）。'
        'rank的candidate字段不表示已经实现候选选参，它只是排名接口字段。目标900至1200 token。'
        if baseline_only else
        '实现完整基线和候选。run中遍历protocol.grid，每种配置只计算一次开发排名和指标，'
        '同时保存search行并选择最优配置，不要选完再重算整个grid。目标2200至2800 token。'
    )
    return (
        '根据当前题公开材料生成完整Python 3.9标准库源码，仅返回唯一python代码块。'
        '定义run_experiment(request)，不打印、不联网、不读写文件、不读凭据、不使用第三方库、反射或动态加载。'
        '不输出最终答卷、图表或实测吞吐，不伪造结果。代码紧凑无注释但所有函数必须完整。\n'
        + scope + '\n'
        'run的baseline和candidate必须是test_queries的排名字典：键必须逐一等于测试查询id；开发查询排名仅进入details.search。'
        'BM25的f是当前文档字段内该词频数，不是查询词频；先用文档f算贡献，再乘查询词频。'
        'run使用protocol.default_config得到基线；rank必须提前分支，仅用data.selected排名，'
        '不训练、不选参、不依赖开发标签；每次请求可能是不同语料、查询或参数，不能复用旧排名。'
        '官方吞吐会重复调用rank 30次并重复计时3轮。每次请求预计算两字段Counter、df和长度；'
        '查询内不要list.count或重复扫描分词文本，查询重复词用词频乘贡献。'
        '先按查询词计算各文档分数，再按(-score,id)稳定排序并返回全部文档。'
        '两个字段各自计算N/df/平均长度；空字段计入N，全空字段平均长度按1；缺词贡献0。'
        '普通查询均值必须直接把每条开发查询的Recall或MRR相加除以开发查询数，不能平均组均值。'
        '例如组A两条Recall为100、100，组B一条为0：普通均值是200/3，组宏均值是50。'
        'search行的recall_at_5_percent和mrr_at_10存普通均值；group_metrics里的macro字段才存组宏均值。'
        '两种均值分别计算，Recall为百分制、MRR为0到1。'
        '选参比较round(worst_mrr,12)、round(macro_mrr,12)、round(macro_recall,12)降序，'
        '然后(title_weight,k1,b)升序。用完整元组排序选择最优search行，避免多层if/elif和大量best变量。'
        '代码目标约100行，直接返回已有字典，不把同一排名复制到另一个字典。'
        '未知词全零时按文档id排序，不漏查询。'
        'run和rank严格遵守下面公开接口；候选确实无法完成可诚实返回null，但基线和rank必须可执行。\n'
        f'当前题号：{case.case_id}\n当前题目输入：{case.input}\n'
        f'method.md：\n{materials["method"]}\n'
        'protocol.json：\n' + json.dumps(json.loads(materials['protocol']), ensure_ascii=False, separators=(',', ':'))
        + '\nsample.json：\n' + json.dumps(json.loads(materials['sample']), ensure_ascii=False, separators=(',', ':'))
        + '\n最终检查：必须实现run和rank两个不同分支。sample展示run，但官方会独立调用rank。'
        'rank分支必须在访问dev_queries和遍历grid之前返回，仅返回一个candidate字段。'
        'rank不允许重新选参，即使请求含开发数据；配置必须来自data.selected。'
        'run的baseline/candidate都对应test_queries，开发排名只在details.search。' + scope
    )
