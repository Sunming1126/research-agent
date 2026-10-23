"""Summarize observed evidence without changing any raw paired-run verdicts."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from compare_images import BASE, IMAGES, RUN
from harness_bridge import write_json
from test_agent import HERE, require


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def extract(version):
    root = HERE / 'runs' / (RUN + '-' + version)
    extra = HERE / 'runs' / (RUN + '-' + version + '-c02-corrected')
    rows = read(root / 'checks.json'); follow = read(extra / 'checks.json')
    by_id = {r['id']: r for r in rows}
    def effective(cid):
        if cid == 'C-02':
            row = next(r for r in follow if r['id'] == 'live.C-02.corrected')
        else:
            row = by_id['live.' + cid]
        return {k: row.get(k) for k in ['status', 'seconds', 'error', 'evidence']}
    abce = {cid: effective(cid) for cid in ['A-01', 'B-01', 'B-02', 'B-03', 'C-01', 'C-02', 'E-01', 'E-02']}
    public = [r for r in rows if r['group'] == 'D-public-algorithm']
    d = {}
    for cid in ['D-01', 'D-02', 'D-05']:
        r = by_id['live.' + cid + '.model_source_generation']
        history = []
        for p in (root / ('live-' + cid) / 'output/data/workspace_team/work' / cid).glob('*/public-selftest.json'):
            history = read(p)['attempts']
        response = read(root / ('live-' + cid) / 'response.json')
        final = response['response'].get('output', {}).get('answer')
        try:
            final_status = json.loads(final)['status']
        except (ValueError, TypeError, KeyError):
            final_status = None
        d[cid] = {'generation_status': r['status'], 'generation_error': r.get('error'),
                  'whole_local_flow_seconds': response['seconds'],
                  'selftest_attempts': history, 'formal_submission_status': final_status,
                  'formal_verified': False}
    quotes = [r for r in read(root / 'report-quote-audit/report-evidence.json') if r['case_id'] != 'C-02']
    quotes += [r for r in read(extra / 'report-quote-audit/report-evidence.json') if r['case_id'] == 'C-02']
    model_calls = []
    repeat_root = HERE / 'runs' / (RUN + '-' + version + '-d-repeat')
    for folder in [root, extra, repeat_root]:
        for path in folder.rglob('call.json'):
            meta = read(path)
            model_calls.append({'file': str(path), 'session_id': meta['session_id'], 'seconds': meta['seconds'],
                                'finish': meta.get('native_finish_reason'), 'reasoning': meta.get('reasoning_effort'),
                                'usage': meta.get('usage_events'), 'errors': meta.get('errors')})
    usage = [u for c in model_calls for u in c.get('usage', [])]
    raw_failures = [r for r in rows if r['status'] == 'FAIL']
    substantive = [r for r in raw_failures if r['id'] != 'live.C-02']
    substantive += [dict(r, run='corrected-C-02') for r in follow if r['status'] == 'FAIL']
    repeat_rows = read(repeat_root / 'checks.json')
    repeat_failures = [dict(r, run='D-independent-repeat') for r in repeat_rows if r['status'] == 'FAIL']
    substantive += repeat_failures
    identity = read(root / 'image-inspect.json')['Id']
    return {'target': IMAGES[version], 'immutable_id': identity,
            'raw_primary_counts': dict(Counter(r['status'] for r in rows)),
            'raw_primary_unique_check_count': len(by_id),
            'raw_failures': raw_failures, 'product_failures': substantive,
            'invalid_two_paper_survey': {'observed': by_id['live.C-02']['status'],
                'classification': 'Test-author invalid expectation; Survey requires five papers. Raw FAIL is preserved, but is not a product defect.'},
            'abce': abce, 'abce_pass': sum(r['status'] == 'PASS' for r in abce.values()),
            'retrieval': {cid: by_id['retrieval.exact.' + cid] for cid in ['B-01', 'B-02', 'B-03'] if 'retrieval.exact.' + cid in by_id},
            'D': d, 'public_algorithm': {'pass': sum(r['status'] == 'PASS' for r in public), 'rows': public},
            'D_repeat': {'generation_pass': sum(r['status'] == 'PASS' for r in repeat_rows if r['group'] == 'D-source-live'),
                         'public_pass': sum(r['status'] == 'PASS' for r in repeat_rows if r['group'] == 'D-public-algorithm'),
                         'rows': repeat_rows},
            'replays': read(root / 'reproducibility.json'),
            'quotes': quotes, 'quotes_found': sum(r.get('quotes_found', 0) for r in quotes),
            'quotes_checked': sum(r.get('quotes_checked', 0) for r in quotes),
            'native_harness_calls': len(model_calls), 'native_usage_total_tokens': sum(u.get('totalTokens', 0) for u in usage),
            'model_calls': model_calls, 'official_score': None}


def main():
    values = {v: extract(v) for v in IMAGES}
    freeze = read(BASE / 'toolkit-sha256.json')
    for name, digest in freeze.items():
        require(hashlib.sha256((HERE / name).read_bytes()).hexdigest() == digest, 'Frozen toolkit changed: ' + name)
    summary = {'run': RUN, 'versions': values, 'test_author_correction': read(BASE / 'test-author-correction.json'),
               'source_comparison': read(BASE / 'source-difference.json'),
               'image_integrity': read(BASE / 'image-artifact-integrity.json'),
               'scope': 'One paired ABCE public-data round plus two independent D-generation rounds. No official Gateway provenance, trusted experiments, private cases or score.',
               'frozen_toolkit_verified_unchanged': True, 'all_requirements_verified': False}
    write_json(BASE / 'comparison-summary.json', summary)
    left, right = values['source'], values['v14']
    lines = ['# 两版 0.4.1 镜像对照测试', '',
             '本次直接运行两份发布镜像中的程序，通过 DeepSeek Harness 桌面账号实际调用模型。测试期间没有修改镜像，也没有手改模型生成的算法。', '',
             f'现有版：`{IMAGES["source"]}`；队友版：`{IMAGES["v14"]}`。两个归档 SHA-256 与各自发布清单相符，实际镜像 ID 已固定。', '',
             '## 可以用于队友讨论的结果', '',
             '| 检查 | source-v1 | v14 | 含义 |', '| --- | --- | --- | --- |',
             f'| 有效 A/B/C/E 题 | {left["abce_pass"]}/8 | {right["abce_pass"]}/8 | 完成接口、字段与落盘检查；C-02 使用相同的五篇论文补测 |',
             f'| B 检索匹配预期 | {sum(r["status"]=="PASS" for r in left["retrieval"].values())}/3 | {sum(r["status"]=="PASS" for r in right["retrieval"].values())}/3 | 预期集合在测试前固定，不交给目标模型 |',
             f'| D 当前模型源码生成 | {sum(r["generation_status"]=="PASS" for r in left["D"].values())}/3 | {sum(r["generation_status"]=="PASS" for r in right["D"].values())}/3 | 只是本地源码流程，不是正式 D 实验完成 |',
             f'| D 独立 Python3.9 公开输入 | {left["public_algorithm"]["pass"]}/13 | {right["public_algorithm"]["pass"]}/13 | 断网、只读、非 root、固定 seed，独立断言算法结果 |',
             f'| D 第二容器复跑 | {sum(r["status"]=="PASS" for r in left["replays"])}/3 | {sum(r["status"]=="PASS" for r in right["replays"])}/3 | 另见原始结果中的 REVIEW/失败说明 |',
             f'| 第二轮 D 独立生成/公开输入 | {left["D_repeat"]["generation_pass"]}/3；{left["D_repeat"]["public_pass"]}/13 | {right["D_repeat"]["generation_pass"]}/3；{right["D_repeat"]["public_pass"]}/13 | 新源码独立生成，成功和失败均保留 |',
             f'| 报告摘录在所标 PDF 页找到 | {left["quotes_found"]}/{left["quotes_checked"]} | {right["quotes_found"]}/{right["quotes_checked"]} | 原文与页码检查，不是语义、创新性或官方得分 |',
             f'| 实际 Harness 调用记录 | {left["native_harness_calls"]} | {right["native_harness_calls"]} | 含该版复核与补测；共同规划和最终比较复核另存 |', '',
             '## 发现的问题与实际影响', '']
    for version, data in values.items():
        lines += [f'### {version}', '']
        if not data['product_failures']:
            lines.append('原始主轮未发现需计为产品问题的失败；仍需结合补测、语义检查和官方测试判断。')
        for row in data['product_failures']:
            lines.append('- `' + row['id'] + '`：' + (row.get('error') or '失败') + ('（质量探针）' if row['level'] == 'quality' else ''))
        for cid, row in data['abce'].items():
            if row['status'] != 'PASS' and cid == 'C-02':
                lines.append('- 合规 C-02 补测仍未完成：' + str(row.get('error')))
        lines.append('')
    lines += ['取消检查使用受控的 3 秒睡眠子进程，在启动后约 0.35 秒设置 CaseSession 取消信号；检查实际安装代码能否尽快结束子进程。它不是官方服务器的取消测试。', '',
              '错误分类检查显式注入 `D_SOURCE_AUDIT_UNAVAILABLE`。若结构化摘要只写通用失败，但 stderr 还在，影响是自动定位和重试决策，不代表平台确实发生过该故障。', '',
              '队友版第一轮 D-01/D-02 被本地静态自测拒绝；这些原始源码在独立 Python3.9 中通过了六个对应公开输入，见 `-v14-rejected-diagnostics`。这不能算作镜像已成功提交，也不能等同算法不正确或官方准入拒绝。两版都有类似本地静态规则，属于共同的标准库兼容性风险。', '',
              '队友版合规 C-02 的真实错误为 `insufficient reserved budget for extract`，不是模型服务欠费或请求超时。提取阶段后续预留过高，导致补检无法继续。应在保留正式总预算的前提下，让已有足够证据时的可选补检有界退出。', '',
              '## D-05：本轮是否重现卡死', '']
    for version, data in values.items():
        d05 = data['D']['D-05']; attempts = d05['selftest_attempts']
        probes = attempts[-1].get('probe_count', 1) if attempts else None
        lines.append(f'- {version}：源码流程 {d05["generation_status"]}；内部最后一轮公开探针数 {probes}，生成尝试记录 {len(attempts)} 次。外部七个公开 D-05 输入及复跑详见 JSON。')
    lines += ['', '一次生成成功不能证明算法永远不会卡死。D-05 的空输入、延迟首次到达、空闲间隔、流切换、并发预留、截止时间与窗口边界，应在每次生成后受限自测；真实失败须把原源码和具体反例反馈给模型完整重写。', '',
              '## 版本实际差异', '',
              f'两镜像 Agent Python 和安装依赖版本相同。共有 {len(summary["source_comparison"]["content_changed_files"])} 个文件存在内容增加、删除或修改，另有 {len(summary["source_comparison"]["newline_only_files"])} 个文件仅换行符不同。完整文件哈希与忽略换行后的差异已保存。', '',
              'v14 的主要增量集中在证据归因、事实选择、写作约束与 Token 预留；source-v1 的主要增量集中在 D-05 边界探针、失败源码和反例反馈、逐次源码留存、取消清理与平台错误分类。两者适合按模块审查，不能只按相同版本号相互覆盖。', '',
              '## 测试设置纠正，原始记录未覆盖', '',
              '初始 C-02 只给两篇论文，违反至少引用五篇的 Survey 规则。主轮脚本错误地预期成功，因此原始 checks.json 保留 FAIL；该项按测试设置错误解释，不算产品失败。其缺失报告的摘录 BLOCKED 也不是另外一个产品缺陷。两版另用完全相同的五篇材料补测，存放在各自 `-c02-corrected` 目录。', '',
              '## 同题耗时', '',
              '| 有效题 | source-v1 秒 | v14 秒 |', '| --- | ---: | ---: |']
    for cid in left['abce']:
        def seconds(data):
            # Recorder time and HTTP elapsed are both retained; use HTTP elapsed
            # for successful cases, recorder elapsed for failed assertions.
            row = data['abce'][cid]
            return round((row.get('evidence') or {}).get('seconds', row.get('seconds') or 0), 3)
        lines.append(f'| {cid} | {seconds(left)} | {seconds(right)} |')
    lines += ['', '仅一轮真实模型调用，耗时受生成随机性、模型服务和缓存影响，不作统计显著性或官方速度排名。两版串行执行真实模型题，配置均为同一 Harness 后端、关闭思考、1 CPU、1GB、180 秒。', '',
              '## 没有被本地测试证明的部分', '',
              '- 官方当前题 Gateway 来源审计与签名凭证、可信 Unix socket runner、隐藏输入、可信测量指标和最终榜单成绩；本地 D 最终失败/缺会话状态如实保留。',
              '- 任意论文库和极端规模、全句专家语义与创新性、模型服务不同配置下的稳定性；本轮仅覆盖提供的七篇公开 PDF、固定输入和公开边界。',
              '- Harness 桌面会话保留真实模型输入回复。Docker 与独立断言由外部协调脚本执行，不会被伪装成当时的模型工具轨迹。', '',
              '原始请求、HTTP 响应、模型输入输出、生成源码、每次自测、容器日志、公开 worker 输出和复跑均单独保留。测试结果不能用模型复核意见覆盖。', '']
    (BASE / '双版本对比报告.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({v: {'abce_pass': d['abce_pass'], 'public_pass': d['public_algorithm']['pass'],
                         'quotes': [d['quotes_found'], d['quotes_checked']], 'product_failures': [r['id'] for r in d['product_failures']]}
                      for v, d in values.items()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
