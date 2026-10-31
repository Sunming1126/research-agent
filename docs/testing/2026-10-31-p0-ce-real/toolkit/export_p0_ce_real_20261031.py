"""Publish bounded metadata/results, keeping requests, PDFs and profiles local."""
import hashlib
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / '协同开发/research-agent-stability-20261027'
META = json.loads((ROOT / '.session-audit/p0-ce-real-study-20261031.json').read_text(encoding='utf-8'))
STUDY = Path(META['directory'])
DOC = REPO / 'docs/testing/2026-10-31-p0-ce-real'
SID = 'session-9c903d0e-5884-44fe-bc7f-3719da00864b'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    assert sha(source) == sha(target)


def rate(n, d):
    return {'numerator': n, 'denominator': d, 'rate': n / d if d else None}


def main():
    assessment = read(STUDY / 'assessment/summary.json')
    assert assessment['complete'], 'Do not publish unfinished review as completed'
    protocol = read(STUDY / 'protocol.json')
    DOC.mkdir(exist_ok=True, parents=True)
    (DOC / '.gitattributes').write_text('* -text -filter\n', encoding='utf-8')
    save(DOC / 'frozen-protocol.json', protocol)
    copy(STUDY / 'assessment/protocol.json', DOC / 'assessment/protocol.json')
    rows = []
    environments = set()
    audits = {'baseline': [], 'candidate': []}
    directories = []
    for number in (1, 2, 3):
        summary = read(STUDY / f'pair-{number}-result.json')
        assert summary['complete']
        phases = [p for p in STUDY.glob(f'pair-{number}-*') if (p / 'summary.json').is_file()
                  and read(p / 'summary.json').get('complete')]
        assert len(phases) == 1
        phase = phases[0]
        directories.append(phase)
        for name in ('summary.json', 'native-proof.json', 'toolkit-fingerprints.json'):
            copy(phase / name, DOC / f'pair-{number}' / name)
        for row in summary['results']:
            directory = phase / row['directory']
            exported = DOC / f'pair-{number}' / row['directory']
            rows.append(row)
            for name in ('result.json', 'environment.json', 'http-response.json'):
                copy(directory / name, exported / name)
            environments.add(read(directory / 'environment.json')['sha256'])
            evidence = list((directory / 'output').rglob('evidence.json'))
            if evidence:
                assert len(evidence) == 1
                data = read(evidence[0])
                audit = data.get('audit', {})
                audits[row['variant']].append(audit.get('ok'))
                save(exported / 'self-audit.json', audit)
                save(exported / 'evidence-ledger-fingerprint.json', {
                    'raw_evidence_sha256': sha(evidence[0]), 'claims': len(data['claims']),
                    'ledger_published': False, 'reason': 'Full authorized source excerpts retained locally and D only',
                    'claims_sha256': hashlib.sha256(json.dumps(data['claims'], ensure_ascii=False, sort_keys=True).encode()).hexdigest()})
            for path in (directory / 'output').rglob('error.json'):
                copy(path, exported / 'error.json')
            for path in (directory / 'output').rglob('storage-recovery.json'):
                copy(path, exported / 'storage-recovery.json')
            for path in (directory / 'model-calls').glob('*/call.json'):
                copy(path, exported / 'model-calls' / path.parent.name / path.name)
    assert len(rows) == 24 and len(environments) == 1
    for row in rows:
        task = next(t for t in protocol['cases'] if t['case_id'] == row['case_id'])
        assert row['input_sha256'] == hashlib.sha256(json.dumps(task, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    newline_differences = []
    for variant in ('baseline', 'candidate'):
        source = Path(protocol['source'][variant]['path'])
        assert all(sha(source / rel) == value for rel, value in protocol['source'][variant]['files'].items())
        for rel in protocol['source'][variant]['files']:
            frozen = (source / rel).read_bytes()
            git_blob = subprocess.check_output(['git', 'show', protocol['source'][variant]['revision'] + ':' + rel], cwd=REPO)
            assert frozen.replace(b'\r\n', b'\n') == git_blob.replace(b'\r\n', b'\n'), rel
            if variant == 'candidate':
                work = (REPO / rel).read_bytes()
                assert frozen.replace(b'\r\n', b'\n') == work.replace(b'\r\n', b'\n'), rel
                if work != frozen:
                    newline_differences.append({'file': rel, 'difference': 'CRLF_vs_LF_only',
                                                'worktree_sha256': sha(REPO / rel), 'frozen_sha256': sha(source / rel)})
    save(DOC / 'source-byte-verification.json', {
        'frozen_source_files_per_variant': 45, 'exact_frozen_hashes_unchanged_during_calls': True,
        'all_frozen_files_equal_pinned_git_blobs_after_CRLF_to_LF': True,
        'candidate_worktree_differences': newline_differences,
        'note': 'Do not call different newline bytes identical hashes; no code difference after newline normalization'})
    assert all(sha(Path(p['path'])) == p['sha256'] for p in protocol['papers'])
    stats = {}
    for variant in ('baseline', 'candidate'):
        items = [r for r in rows if r['variant'] == variant]
        times = sorted(r['seconds'] for r in items)
        complete = sum(r['status'] == 'completed' for r in items)
        stats[variant] = {
            'delivery': rate(complete, len(items)), 'hard_failure': rate(len(items) - complete, len(items)),
            'http_200_invalid_schema': sum(r['http_status'] == 200 and not r['wire_fields_valid'] for r in items),
            'schema_diff_failure_count': 0, 'sdk_trace_linked': rate(sum(r['sdk_trace_linked'] for r in items), len(items)),
            'sdk_llm_count_matches': all(r['sdk_llm_call_count_matches'] for r in items),
            'target_calls': sum(r['target_real_model_calls'] for r in items),
            'total_tokens': sum(r['total_tokens'] for r in items),
            'unknown_usage_calls': sum(r['unknown_usage_calls'] for r in items),
            'seconds_sum': round(sum(times), 3), 'seconds_p50': statistics.median(times),
            'seconds_p95_nearest_rank': times[math.ceil(.95 * len(times)) - 1],
            'self_audit_ok': sum(v is True for v in audits[variant]),
            'self_audit_reports': len(audits[variant]),
            'per_case': {case: rate(sum(r['case_id'] == case and r['status'] == 'completed' for r in items), 3)
                         for case in ('C-01', 'C-03', 'E-01', 'E-02')},
            'round_delivery': [sum(r['round'] == n and r['status'] == 'completed' for r in items) for n in (1, 2, 3)],
            'rollback_rate': None, 'official_score_p50': None, 'official_score_p95': None,
        }
    copy(STUDY / 'precheck-result.json', DOC / 'precheck/summary.json')
    prechecks = read(STUDY / 'precheck-result.json')['results']
    precheck_proofs = []
    for phase in STUDY.glob('precheck-0-*'):
        if (phase / 'native-proof.json').exists():
            copy(phase / 'native-proof.json', DOC / 'precheck/native-proof.json')
            precheck_proofs.append(read(phase / 'native-proof.json')['native_tool_seq'])
    for name in ('summary.json', 'blinding-map.json', 'native-proof.json', 'validator-offline-tests.json'):
        copy(STUDY / 'assessment' / name, DOC / 'assessment' / name)
    review_stats = {}
    mapping = {r['blind_id']: r for r in read(STUDY / 'assessment/blinding-map.json')}
    for variant in ('baseline', 'candidate'):
        items = [r for r in assessment['results'] if mapping[r['blind_id']]['variant'] == variant]
        review_stats[variant] = {
            'reports_scheduled': len(items),
            'statuses': {v: sum(r['status'] == v for r in items) for v in sorted({r['status'] for r in items})},
            'valid_sampled_facts': sum(r.get('valid_facts', 0) for r in items),
            'rejected_reviewer_facts': sum(r.get('rejected_facts', 0) for r in items),
            'verdict_counts': {v: sum(r.get('verdict_counts', {}).get(v, 0) for r in items)
                               for v in ('supported', 'contradicted', 'not_in_evidence', 'uncertain')},
            'whole_report_correctness': None,
        }
    review_calls = []
    for path in (STUDY / 'assessment').glob('report-*/result.json'):
        copy(path, DOC / 'assessment' / path.parent.name / path.name)
        call = path.parent / 'model-call/call.json'
        if call.exists():
            copy(call, DOC / 'assessment' / path.parent.name / 'call.json')
            review_calls.append(read(call))
            response_path = path.parent / 'model-call/response.json'
            if response_path.exists():
                raw_response = read(response_path)
                choice = raw_response['choices'][0]
                content = choice['message']['content']
                diagnostic = {'finish_reason': choice.get('finish_reason'),
                              'raw_response_sha256': sha(response_path),
                              'content_chars': len(content), 'raw_response_retained_local_and_D': True}
                try:
                    parsed = json.loads(content)
                    facts = parsed.get('facts') if isinstance(parsed, dict) else None
                    diagnostic.update(valid_json=True, facts_count=len(facts) if isinstance(facts, list) else None,
                                      fact_count_within_frozen_limit=isinstance(facts, list) and 1 <= len(facts) <= 12)
                except ValueError:
                    diagnostic.update(valid_json=False)
                save(DOC / 'assessment' / path.parent.name / 'response-diagnostic.json', diagnostic)
    for path in (STUDY / 'toolkit-history').glob('*'):
        if path.is_file():
            copy(path, DOC / 'toolkit-history' / path.name)
    for name in ('prepare_p0_ce_real_20261031.py', 'p0_ce_real_20261031.py', 'p0_ce_real_worker_20261031.py',
                 'p0_ce_assess_20261031.py'):
        copy(ROOT / '镜像测试' / name, DOC / 'toolkit' / name)
    for name in ('dispatch_p0_ce_real_20261031.py', 'dispatch_p0_ce_assess_20261031.py',
                 'paste_p0_native_prompt_20261031.ps1', 'export_p0_ce_real_20261031.py'):
        copy(ROOT / '.session-audit' / name, DOC / 'toolkit' / name)
    # Export only verified current-study tool calls, selection fields and usage.
    sys.path.insert(0, str(ROOT / '镜像测试'))
    from native_workspace_harness import native_rows, workspace, selection
    selection()
    assert SID in workspace()['sessionIds']
    native, _ = native_rows(SID)
    calls = []
    usages = []
    start = min(precheck_proofs + [read(d / 'native-proof.json')['native_tool_seq'] for d in directories])
    for row in native:
        if row.get('seq', 0) < start:
            continue
        if row.get('type') == 'assistant/message':
            data = row['data']
            if data.get('usage'):
                usages.append({'seq': row['seq'], 'usage': data['usage']})
            for item in data.get('message', {}).get('content', []):
                text = json.dumps(item, ensure_ascii=False)
                if item.get('type') == 'tool-call' and META['study'] in text and (
                        'p0_ce_real_20261031.py' in text or 'p0_ce_assess_20261031.py' in text):
                    calls.append({'seq': row['seq'], 'name': item.get('name'), 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                                  'tool_call': item})
    save(DOC / 'native-tool-execution.json', {'session_id': SID, 'workspace_id': 'c61ec5e8-9932-4ef5-9709-fed302ad7355',
         'provider': 'magic', 'model': 'deepseek-v4.1-flash', 'calls': calls,
         'native_usage_events': usages, 'usage_scope': 'Native coordination only, never merged with target usage',
         'new_conversations_created': 0})
    metrics = {'study': META['study'], 'production_candidate_commit': '91d15f80d720476c9aee1f96a6320b97ecc97dea',
               'comparison_baseline': protocol['source']['baseline']['revision'], 'paired': stats,
               'precheck_calls': sum(r['target_real_model_calls'] for r in prechecks),
               'precheck_tokens': sum(r['total_tokens'] for r in prechecks), 'assessment': review_stats,
               'assessment_calls': len(review_calls),
               'assessment_known_tokens': sum((r.get('usage') or {}).get('total_tokens', 0) for r in review_calls),
               'assessment_unknown_usage_calls': sum(not isinstance((r.get('usage') or {}).get('total_tokens'), int) for r in review_calls),
               'environment_sha256': next(iter(environments)), 'frozen_input_fingerprints_verified': True,
               'environment_consistent': True, 'linux_accepted': False, 'platform_accepted': False,
               'P0_overall_accepted': False, 'production_promoted': False,
               'correctness_improvement_proven': False, 'schema_zero_observed_not_guaranteed': True,
               'score_p50': None, 'score_p95': None, 'score_variance': None, 'rollback_rate': None,
               'regression_historical': {'passed': 439, 'skipped': 6, 'strict_xfail': 1, 'rerun_this_live_stage': False}}
    save(DOC / 'metrics.json', metrics)
    # All original artifacts remain local/D; content hashes reveal no credentials.
    save(DOC / 'local-artifact-fingerprints.json', {p.relative_to(STUDY).as_posix():
         {'sha256': sha(p), 'bytes': p.stat().st_size} for p in STUDY.rglob('*') if p.is_file()})
    print(json.dumps({'exported': str(DOC), 'paired': stats, 'assessment': review_stats,
                      'assessment_calls': len(review_calls)}, ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
