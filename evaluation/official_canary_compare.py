"""Normalize user-visible ANIC run snapshots. Does not call the platform or model."""
import argparse
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

MAX = {'A': 5, 'B': 15, 'C': 25, 'D': 40, 'E': 15}
FIXED = set('B-01 B-02 B-03 C-03 C-05 D-01 D-02 D-05 E-01 E-04'.split())
DIMENSIONS = ('聊天类', '检索类', '综述事实类', '引文溯源类', '要点覆盖类', '实验流水线类', '数据与图表类')
CASE = r'[A-E]-\d{2}'
SCORE = re.compile(rf'^({CASE})已评分.*?得分：[ ]*(\d+(?:\.\d+)?)$')
USAGE = re.compile(rf'^\d{{2}}({CASE})Single(.*?)Agent 模型调用(\d+)\s*次Agent Token([\d,]+)模型耗时([\d.ms]+)纯处理耗时([\d.ms]+)(.*)$')
IDENTITY = ('upload_id', 'archive_sha256', 'source_commit', 'image_id', 'input_sha256', 'environment_sha256')


def seconds(raw):
    units = re.findall(r'(\d+(?:\.\d+)?)(ms|m|s)', raw)
    if ''.join(value + unit for value, unit in units) != raw or not units:
        raise ValueError('Unsupported duration: ' + raw)
    return float(sum(Decimal(value) * {'m': 60, 's': 1, 'ms': Decimal('.001')}[unit] for value, unit in units))


def distinct(old, new):
    if old is not None and old != new:
        raise ValueError(f'Conflicting snapshot entries: {old!r} and {new!r}')
    return new


def following(rows, label):
    i = rows.index(label) + 1
    while rows[i] == label:
        i += 1
    return rows[i]


def classify(note, status):
    if re.search('字段结构错误|非法输出|格式不合法|Schema', note, flags=re.I):
        return 'reported_output_contract_failure'
    if '超时' in note or '超时' in status:
        return 'reported_timeout'
    if '异常' in status or '失败' in status:
        return 'reported_execution_failure_unknown_stage'
    if '未记录选手可见错误提示' in note:
        return 'no_visible_error_reported'
    return 'not_observed_in_capture'


def capture(path, *, expected_run, upload_id=None):
    payload = path.read_bytes()
    snapshot = json.loads(payload.decode('utf-8-sig'))
    run = re.fullmatch(r'https?://aiagent-anic\.com/runs/(eval-\d{8}-[a-zA-Z0-9]+)', snapshot['url'])
    if run is None:
        raise ValueError('Expected a normal official run detail snapshot')
    if run[1] != expected_run:
        raise ValueError(f'Run changed during capture: expected {expected_run}, found {run[1]}')
    names = [x['name'] for x in snapshot['nodes']]
    if '评测已完成，可查看结果报告' not in names:
        raise ValueError('Finished result not visible; keep the snapshot and retry capture after rendering')
    title = next(s for s in names if s.startswith('初赛自动成绩 · '))
    score_label = next(s for s in names if s.startswith('本次自动得分 '))
    display = Decimal(re.search(r'本次自动得分\s*(\d+(?:\.\d+)?)', score_label)[1])
    report_start = names.index('判题说明与错误报告')
    report_end = names.index(title)
    scores, usage, notes = {}, {}, {}
    current = None
    for line in names[report_start:report_end]:
        if m := SCORE.fullmatch(line):
            current = m[1]
            scores[current] = distinct(scores.get(current), Decimal(m[2]))
        elif current and (line.startswith(('【', '本项计', '未记录选手')) or re.match(rf'^{CASE}[:：]', line)):
            notes.setdefault(current, []).append(line)
    for line in names:
        if m := USAGE.fullmatch(line):
            case, status, calls, tokens, model, handling, message = m.groups()
            parsed = dict(protocol_status=status, calls=int(calls), tokens=int(tokens.replace(',', '')),
                          model_seconds=seconds(model), processing_seconds=seconds(handling),
                          execution_note=message.strip() or None)
            usage[case] = distinct(usage.get(case), parsed)
    if len(scores) != 11 or set(scores) != set(usage) or set(scores) - FIXED not in [{f'A-0{i}'} for i in range(1, 6)]:
        raise ValueError('Expected eleven scored cases and their usage; incomplete or different dispatch')
    total_tokens = int(following(names, '本轮总 token').replace(',', ''))
    if sum(u['tokens'] for u in usage.values()) != total_tokens:
        raise ValueError('Per-case token total differs from the run header')
    cases = {}
    for case, value in sorted(scores.items()):
        max_score = MAX[case[0]]
        if not 0 <= value <= max_score:
            raise ValueError('Case score outside published maximum: ' + case)
        note = '\n'.join(dict.fromkeys(notes.get(case, [])))
        u = usage[case]
        cases[case] = dict(u, score=str(value), max_score=max_score, score_rate=float(value / max_score),
                           token_share=u['tokens'] / total_tokens if total_tokens else None,
                           judge_note=note or None, failure_class=classify(note, u['protocol_status']),
                           internal_stage_seconds=None, token_prompt_completion_split=None, input_sha256=None)
    categories, exact = {}, Decimal(0)
    for group, maximum in MAX.items():
        members = [r for k, r in cases.items() if k[0] == group]
        average = sum(Decimal(r['score']) for r in members) / len(members)
        exact += average
        tokens = sum(r['tokens'] for r in members)
        categories[group] = dict(score_average=float(average), max_score=maximum, score_rate=float(average / maximum),
            tokens=tokens, token_share=tokens / total_tokens if total_tokens else None, calls=sum(r['calls'] for r in members),
            model_seconds=round(sum(r['model_seconds'] for r in members), 3),
            processing_seconds=round(sum(r['processing_seconds'] for r in members), 3))
    if abs(display - exact) > Decimal('0.051'):
        raise ValueError('Recomputed category mean differs from the displayed official total')
    dimensions = {}
    dimension_start = names.index('七项自动评分')
    for dim in DIMENSIONS:
        value = following(names[dimension_start:], dim)
        shown, maximum = map(Decimal, value.split('/'))
        dimensions[dim] = dict(displayed_score=float(shown), max_score=int(maximum), score_rate=float(shown / maximum))
    ce = {case: r for case, r in cases.items() if case[0] in 'CE'}
    return dict(schema_version=1, source='normal competition page, user-visible fields only',
        run_id=run[1], url=snapshot['url'], captured_at=snapshot['at'], raw_snapshot_sha256=hashlib.sha256(payload).hexdigest(),
        score_rule_label=title.split(' · ', 1)[1], automatic_leaderboard_included='已计入初赛自动榜' in score_label,
        official_display_total=float(display), recomputed_total=float(exact), tokens=total_tokens,
        calls=sum(r['calls'] for r in cases.values()), wall_seconds=seconds(following(names, '总耗时')),
        completed_at_china=following(names, '完成时间'), identity=dict.fromkeys(IDENTITY) | {'upload_id': upload_id},
        dimensions=dimensions, categories=categories, cases=cases,
        ce_summary={'cases': len(ce), 'zero_scores': sum(Decimal(r['score']) == 0 for r in ce.values()),
                    'visible_contract_failures': sum(r['failure_class'] == 'reported_output_contract_failure' for r in ce.values()),
                    'unavailable': ['internal validation traces', 'failed stage', 'input fingerprint']})


def compare(before, after):
    if before['run_id'] == after['run_id']:
        raise ValueError('The same run cannot serve as both comparator and candidate')
    a, b = before['cases'], after['cases']
    shared = sorted(a.keys() & b.keys())
    change = {}
    def subtract(x, y):
        return round(y - x, 3) if x is not None and y is not None else None
    for key in shared:
        x, y = a[key], b[key]
        change[key] = {
            'score': float(Decimal(y['score']) - Decimal(x['score'])),
            **{name: subtract(x.get(name), y.get(name)) for name in ('tokens', 'calls', 'model_seconds', 'processing_seconds')},
        }
    identity_a, identity_b = before['identity'], after['identity']
    controlled_inputs = (a.keys() == b.keys() and before['score_rule_label'] == after['score_rule_label']
        and all(identity_a[k] and identity_a[k] == identity_b[k] for k in ('input_sha256', 'environment_sha256'))
        and all(identity.get(k) for identity in (identity_a, identity_b) for k in ('source_commit', 'archive_sha256')))
    return dict(before=before['run_id'], after=after['run_id'], inference='single pair; no component-level causal claim',
        comparison_class='single matched pair' if controlled_inputs else 'observational: version or input/environment not fully bound',
        unmatched_cases=sorted(a.keys() ^ b.keys()), cases=change,
        category_score_deltas={k: round(after['categories'][k]['score_average'] - before['categories'][k]['score_average'], 8) for k in MAX},
        official_display_score_delta=round(after['official_display_total'] - before['official_display_total'], 2),
        recomputed_score_delta=round(after['recomputed_total'] - before['recomputed_total'], 8),
        tokens_delta=after['tokens'] - before['tokens'], tokens_delta_ratio=after['tokens'] / before['tokens'] - 1 if before['tokens'] else None,
        calls_delta=subtract(before['calls'], after['calls']), wall_seconds_delta=subtract(before['wall_seconds'], after['wall_seconds']),
        ce_zero_delta=after['ce_summary']['zero_scores'] - before['ce_summary']['zero_scores'],
        d01_d02_observed_score_drops=[k for k in ('D-01', 'D-02') if k in change and change[k]['score'] < 0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    read = sub.add_parser('capture')
    read.add_argument('snapshot', type=Path)
    read.add_argument('--expected-run', required=True)
    read.add_argument('--upload-id')
    diff = sub.add_parser('compare')
    diff.add_argument('before', type=Path)
    diff.add_argument('after', type=Path)
    for command in (read, diff):
        command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = capture(args.snapshot, expected_run=args.expected_run, upload_id=args.upload_id) if args.mode == 'capture' else compare(
        json.loads(args.before.read_text(encoding='utf-8')), json.loads(args.after.read_text(encoding='utf-8')))
    with args.output.open('x', encoding='utf-8') as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
