"""Blinded, bounded source-only diagnostics under the original native chat.

Generation, self-audit and this same-provider reviewer are separate records.
This reviewer is neither an exhaustive correctness test nor an official grader.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / '镜像测试'
SID = 'session-9c903d0e-5884-44fe-bc7f-3719da00864b'
WID = 'c61ec5e8-9932-4ef5-9709-fed302ad7355'
VERDICTS = ('supported', 'contradicted', 'not_in_evidence', 'uncertain')
SYSTEM = (
    '你是单独执行的事实核查员，不知道生成版本，不看生成器自审。只依据sources，不联网、'
    '不凭常识补证据。从report逐字摘出8至12条可检验的论文事实，尽量覆盖所有引用论文，'
    '优先核对方法归属、训练与推理、实验条件和量化结论。每条sentence必须为报告中连续原句。'
    '原创待验证方案与拟议参数不是论文实测，不能仅因尚未实验判假。'
    'verdict仅为supported/contradicted/not_in_evidence/uncertain。缺少摘录支持只表示'
    'not_in_evidence，不能据此断言原论文没做。supported/contradicted必须提供source_ids及'
    '一段逐字连续的source_quote，并解释支持/反驳关系；source_quote须完整包含在其中一个'
    'source_id的quote中，不要用省略号拼接。区分相关工作与作者贡献、否定、比较对象与条件。'
    '只输出JSON对象facts数组；每项sentence,verdict,source_ids,source_quote,reason。'
    '不得输出整体分数、满分或合格保证。'
)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split())


def check_fact(fact, report, known):
    issues = []
    if not isinstance(fact, dict):
        return ['fact_not_object']
    sentence = fact.get('sentence')
    if not isinstance(sentence, str) or len(norm(sentence)) < 12 or norm(sentence) not in norm(report):
        issues.append('sentence_not_contiguous_report_text')
    if fact.get('verdict') not in VERDICTS:
        issues.append('invalid_verdict')
    ids = fact.get('source_ids')
    if not isinstance(ids, list) or any(not isinstance(i, str) or i not in known for i in ids):
        issues.append('invalid_source_ids')
    if not isinstance(fact.get('reason'), str) or not fact['reason'].strip():
        issues.append('missing_reason')
    if fact.get('verdict') in ('supported', 'contradicted'):
        quote = fact.get('source_quote')
        if (not isinstance(quote, str) or len(norm(quote)) < 20 or not ids
                or 'invalid_source_ids' in issues
                or not any(norm(quote) in norm(known[i]['quote']) for i in ids)):
            issues.append('quote_not_contiguous_authorized_excerpt')
    return issues


def proof(study):
    from native_workspace_harness import native_rows, selection, workspace
    selection()
    assert SID in workspace()['sessionIds']
    request = json.loads((ROOT / '.session-audit' / 'p0-ce-assess-request-20261031.json').read_text(encoding='utf-8'))
    assert request['study'] == study and request['session_id'] == SID and request['workspace_id'] == WID
    for _ in range(50):
        rows, _ = native_rows(SID)
        for row in rows:
            if row.get('seq', 0) <= request['before_seq'] or row.get('type') != 'assistant/message':
                continue
            for item in row.get('data', {}).get('message', {}).get('content', []):
                text = json.dumps(item, ensure_ascii=False)
                if item.get('type') != 'tool-call' or 'p0_ce_assess_20261031.py' not in text or study not in text:
                    continue
                headers = [r for r in rows if r.get('type') == 'request/header' and r['seq'] <= row['seq']]
                header = max(headers, key=lambda r: r['seq'])
                config = header['data']['header']['config']
                assert config.get('provider') == 'magic' and config.get('model') == 'deepseek-v4.1-flash'
                return {**request, 'native_tool_seq': row['seq'], 'model_config_header_seq': header['seq'],
                        'native_tool_call_sha256': hashlib.sha256(text.encode()).hexdigest(),
                        'native_tool_execution_verified': True}
        time.sleep(.2)
    raise RuntimeError('No fresh original native tool proof; reviewer not invoked')


def prepare(study):
    """Freeze sampling rules without a target-model call."""
    out = study / 'assessment'
    out.mkdir(exist_ok=False)
    plan = {'study': study.name, 'script_sha256': digest(Path(__file__)), 'system': SYSTEM,
            'reports': 'every completed report in all three paired rounds, no cherry-picking',
            'sources': 'complete per-report authorized evidence ledger; no source facts or self-audit',
            'blinding': 'payload has no variant, round, score, self-audit, paths or version',
            'max_calls_per_report': 1, 'max_output_tokens': 3000, 'timeout_seconds': 75,
            'max_reports': 24, 'quote_validation': 'NFKC and whitespace only; continuous exact text',
            'assessment_errors': 'retain raw response; reject invalid facts; never auto-rewrite',
            'limits': 'same-provider sampled excerpt review, not human gold or exhaustive correctness',
            'official_score': None}
    save(out / 'protocol.json', plan)
    return plan


def run(study):
    out = study / 'assessment'
    plan = json.loads((out / 'protocol.json').read_text(encoding='utf-8'))
    assert plan['script_sha256'] == digest(Path(__file__))
    assert not (out / 'native-proof.json').exists(), 'Assessment already started; do not duplicate'
    native = proof(study.name)
    save(out / 'native-proof.json', native)
    from magic_api_bridge import MagicAPI
    jobs = []
    for number in (1, 2, 3):
        summary = json.loads((study / f'pair-{number}-result.json').read_text(encoding='utf-8'))
        assert summary['complete']
        phases = [p for p in study.glob(f'pair-{number}-*') if (p / 'summary.json').exists()
                  and json.loads((p / 'summary.json').read_text(encoding='utf-8')).get('complete')]
        assert len(phases) == 1
        jobs.extend((phases[0] / row['directory'], row) for row in summary['results'])
    assert len(jobs) == 24
    # Stable opaque IDs, with mapping kept outside the model payload.
    mapping = [{'blind_id': f'report-{i + 1:02}', 'directory': str(directory.relative_to(study)),
                'case_id': row['case_id'], 'variant': row['variant'], 'round': row['round'],
                'generation_status': row['status']} for i, (directory, row) in enumerate(jobs)]
    save(out / 'blinding-map.json', mapping)
    results = []
    api = MagicAPI()
    assert api.model == 'deepseek-v4.1-flash'
    bridge_sha = digest(HERE / 'magic_api_bridge.py')
    for mapping_row, (directory, generated) in zip(mapping, jobs):
        target = out / mapping_row['blind_id']
        target.mkdir(exist_ok=False)
        result = {'blind_id': mapping_row['blind_id'], 'status': 'not_assessed'}
        if generated['status'] != 'completed':
            result.update(status='no_report_generation_failed', target_real_model_calls=0)
        else:
            report = json.loads((directory / 'http-response.json').read_text(encoding='utf-8'))['markdown']
            evidence_files = list((directory / 'output').rglob('evidence.json'))
            assert len(evidence_files) == 1
            ledger = json.loads(evidence_files[0].read_text(encoding='utf-8'))['claims']
            sources = [{'source_id': e['evidence_id'], 'filename': e['filename'], 'page': e['page'], 'quote': e['quote']}
                       for e in ledger]
            reference_map = dict(re.findall(r'^\[(\d+)\]\s+([^\n]+\.pdf)\s*$', report, re.M))
            payload = {'report': report, 'sources': sources, 'reference_map': reference_map}
            save(target / 'input.json', payload)
            result.update(report_sha256=hashlib.sha256(report.encode()).hexdigest(),
                          ledger_sha256=digest(evidence_files[0]))
            try:
                response, _ = api.complete({'model': api.model, 'messages': [
                    {'role': 'system', 'content': SYSTEM},
                    {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
                    'max_tokens': plan['max_output_tokens'], 'stream': False, 'temperature': 0,
                    'thinking': {'type': 'disabled'}, 'response_format': {'type': 'json_object'}},
                    target / 'model-call', timeout=plan['timeout_seconds'])
                choice = response['choices'][0]
                assert choice.get('finish_reason') == 'stop'
                facts = json.loads(choice['message']['content'])['facts']
                assert isinstance(facts, list) and 1 <= len(facts) <= 12
                known = {s['source_id']: s for s in sources}
                checked = [{'fact': f, 'validation_issues': check_fact(f, report, known)} for f in facts]
                valid = [r['fact'] for r in checked if not r['validation_issues']]
                result.update(status='assessed' if len(valid) == len(facts) and len(facts) >= 8 else 'partial_assessment',
                              checked_facts=checked, valid_facts=len(valid), rejected_facts=len(facts) - len(valid),
                              verdict_counts={v: sum(f['verdict'] == v for f in valid) for v in VERDICTS},
                              limits=plan['limits'])
            except Exception as exc:
                result.update(status='assessment_failed', error_type=type(exc).__name__)
            call_path = target / 'model-call/call.json'
            actual = json.loads(call_path.read_text(encoding='utf-8')) if call_path.exists() else {}
            usage = actual.get('usage') or {}
            result.update(target_real_model_calls=int(call_path.exists()), total_tokens=usage.get('total_tokens'),
                          quota_exhausted=api.quota_exhausted.is_set())
        save(target / 'result.json', result)
        results.append(result)
        save(out / 'summary.json', {'complete': False, 'results': results, 'official_score': None})
        print(json.dumps({k: v for k, v in result.items() if k != 'checked_facts'}, ensure_ascii=False), flush=True)
        assert digest(Path(__file__)) == plan['script_sha256'] and digest(HERE / 'magic_api_bridge.py') == bridge_sha
        if api.quota_exhausted.is_set():
            save(out / 'summary.json', {'complete': False, 'results': results, 'stopped_reason': 'quota_exhausted'})
            return
    save(out / 'summary.json', {'complete': True, 'results': results, 'official_score': None, 'limits': plan['limits']})


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--study', required=True)
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    assert args.study.startswith('p0-ce-real-20261031-') and '/' not in args.study and '\\' not in args.study
    location = HERE / 'runs' / args.study
    if args.prepare:
        prepare(location)
        print(json.dumps({'prepared': True, 'target_model_calls': 0}))
    else:
        run(location)
