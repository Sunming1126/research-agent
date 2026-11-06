"""Read retained evidence; never execute D code or target-model requests."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / '协同开发/research-agent-d-contract-20261106'
EVIDENCE = ROOT / '镜像测试/runs/academic-linux-20261105-11c45136'

def category(name):
    if 'protected_production' in name or 'freezes_all' in name:
        return 'historical_snapshot', '两份历史保护清单冻结了不同版本。以dc9完整B/E、公共代码和依赖为本轮保护基线，逐文件核对；不删除隔离护栏。'
    if 'restores_every' in name or 'reasoning_is_case' in name:
        return 'versioned_generation_policy', '旧95秒/关闭推理预期与正式1800秒不符。验证父级截止时间不扩张、420秒子窗口、实例及类属性恢复、失败恢复。'
    if any(s in name for s in ('platform_service', 'source_rejection', 'partial_measurements', 'postprocessing', 'partial_result')):
        return 'client_fixture_provenance', '夹具跳过生成且没有源码/响应对，新增材料复核先失败，未到被测客户端。补齐真实格式来源对并隔离复核；保留一次调用、部分结果、异常分类和落盘断言。'
    if 'unmodified_client' in name or 'still_reaches_real_client' in name:
        return 'formal_client_current_protocol', '旧170秒小于生成预留300秒，客户端未执行。采用正式1780秒并断言未改客户端/source→/run、源码配对与失败测量保留。'
    if 'cancelled' in name:
        return 'cancellation_injection', '旧注入挂在已停用的练习题tester上，没有真正取消正式生成。改为在实际模型生成阶段注入取消，验证不重试、不提交。'
    return 'obsolete_practice_oracle', '正式路径已移除旧题语义oracle与wall-clock baseline切换。改测当轮材料、完整源码/来源响应、通用语法重试和诚实失败，不把旧公开算法恢复到正式路径。'

items = []
for c in ET.parse(EVIDENCE / 'regression.xml').findall('.//testcase'):
    failure = c.find('failure')
    if failure is None:
        continue
    name = c.attrib['classname'] + '::' + c.attrib['name']
    group, hypothesis = category(name)
    items.append({'id':name, 'category':group, 'observed_failure':failure.attrib.get('message'),
                  'hypothesis':hypothesis, 'production_defect_confirmed':False,
                  'experiment':'待原Harness执行新版契约在原生产源码上的Linux对照；修改断言之前保留原JUnit。',
                  'decision':'诊断候选；不计修复、不计提分。'})
assert len(items) == 30
directory = REPO / 'docs/testing/2026-11-06-d-contract'
directory.mkdir(parents=True, exist_ok=True)
record = {'baseline_commit':'dc9ef0f2c9d257d14536389cbf72fa0b3e89b9bd',
          'teammate_commit':'046324bae9425aa0a1aeff49b9d8128831d18479',
          'raw_junit_sha256':hashlib.sha256((EVIDENCE/'regression.xml').read_bytes()).hexdigest(),
          'baseline_summary':json.loads((EVIDENCE/'summary.json').read_text()), 'cases':items}
(directory/'diagnostic-matrix.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
groups = {}
for item in items:
    groups[item['category']] = groups.get(item['category'],0)+1
lines = [
    '# D类30项共性失败：修复前诊断与验证矩阵', '',
    '观察：冻结BE候选dc9ef0f的原Harness Linux全量结果为601通过、30失败、0错误、0跳过；',
    '队友046324b为554通过、相同30项失败。三项Linux专用用例已实际通过。原失败保留，不作豁免。', '',
    '边界：30项失败不等于30个线上根因。D-05正式失分证据仍是服务量统计及部分场景；',
    '本矩阵只对可见源码和保留JUnit提出可证伪假设，不读取私有评分答案，不以回归数推断分数。', '',
    '假设：历史协议断言与不完整夹具遮蔽了现行链路。先在不修改生产源码的dc9上恢复有效的故障注入，',
    '再根据复现结果选择一个生产因素。任何真实失败均阻止放行；不得删除/skip失败或弱化来源校验。', '',
    '|分组|数量|验证重点|', '|---|---:|---|',
]
for key,count in groups.items():
    lines.append(f'|{key}|{count}|详见逐项矩阵；所有故障边界继续覆盖|')
lines += ['', '实验顺序：', '',
          '1. 先提交本诊断及原始失败清单；冻结B/E、公共代码、依赖哈希。',
          '2. 在独立D分支维护现行契约夹具，以相同dc9生产源码在Linux执行，模型调用为0，保留新旧JUnit和实际故障注入。',
          '3. 若仍出现生产失败，单因素修复，新旧交替重复；不得用更新测试代替生产修复证据。',
          '4. 再以原总控magic执行D-02/D-05当前材料的模型生成、独立验证及未改客户端全链路；公开输入不冒充隐藏正式题或官方分。',
          '5. 正式得分Delta只取正常平台有效评测，平台权限不足时明确保留未验证状态。', '',
          '门禁：全量0失败/0错误/0跳过；来源响应与源码一致；/source→/run一次；取消/超时/服务失败可识别；',
          '失败平台测量与图表不被占位结果覆盖；D独立改动不改变B/E及公共运行文件。', '',
          '结论：当前仅完成静态诊断，根因分类待实验反证。决策：先执行契约对照，暂不修改D算法或提交新正式镜像。', '']
(REPO/'docs/2026-11-06-D30诊断与验证矩阵.md').write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps({'cases':len(items),'groups':groups,'production_source_changed':False},ensure_ascii=False))
