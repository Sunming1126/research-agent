"""Real installed-image acceptance for the merged 0.4.2 candidate.

Uses the same frozen primary suite, replacing the test-author invalid C-02 with
its already frozen five-paper correction. Separate run directories keep history.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
import re

import compare_images as C
import comparison_d_repeat as D
import test_agent as T
from harness_bridge import write_json

IMAGE = 'research-assistant:0.4.2-hybrid-v1'
RUN = 'hybrid-20261023-v042'


def authorization(rec, inputs):
    output = rec.root / 'live-ABCE/output'
    for cid in ['C-01', 'C-02', 'E-01', 'E-02']:
        def check(cid=cid):
            task = inputs['tasks'][cid]; allowed = set(task['input']['materials'])
            report = json.loads((output / ('data/workspace_team/' + cid + '.json')).read_text(encoding='utf-8'))
            files = list((output / 'data/workspace_team/work' / cid).glob('*/evidence.json'))
            T.require(len(files) == 1, 'No unique ledger')
            claims = json.loads(files[0].read_text(encoding='utf-8'))['claims']
            cited = set(re.findall(r'[A-Za-z0-9_.-]+\.pdf', report['markdown']))
            T.require({x['filename'] for x in claims} <= allowed and cited <= allowed, 'Unassigned PDF used')
            T.require(not cid.startswith('C') or len(cited) >= 5, 'Survey needs five assigned papers')
            return {'authorized_pdfs': sorted(allowed), 'referenced': sorted(cited)}
        rec.test('authorized_reports.' + cid, 'report-authorization', check)


def replay(rec):
    p = T.command(['python', str(T.HERE / 'check_reproducibility.py'), '--run-id', rec.root.name], timeout=120)
    (rec.root / 'reproducibility-stdout.txt').write_bytes(p.stdout + p.stderr)
    T.require(p.returncode == 0, 'Replay runner failed')
    for row in json.loads((rec.root / 'reproducibility.json').read_text(encoding='utf-8')):
        rec.add(row['id'], row['group'], row['status'], evidence=row, error=row.get('error'), level='quality')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['all', 'd-repeat'], default='all')
    args = parser.parse_args(); os.environ['TEST_HARNESS_REASONING'] = 'off'
    root = T.HERE / 'runs' / (RUN + ('-d-repeat' if args.stage == 'd-repeat' else ''))
    root.mkdir(parents=True, exist_ok=False); rec = T.Recorder(root)
    inputs = json.loads(json.dumps(C.frozen_inputs()))
    inputs['tasks']['C-02'] = json.loads((C.BASE / 'corrected-C-02-input.json').read_text(encoding='utf-8'))
    write_json(root / 'frozen-inputs.json', inputs)
    write_json(root / 'toolkit-sha256.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
               [T.HERE / name for name in ['accept_hybrid.py', 'compare_images.py', 'comparison_container_checks.py',
                'comparison_report_evidence.py', 'comparison_d_repeat.py', 'test_agent.py', 'harness_bridge.py', 'public_oracles.py']]})
    identity = C.metadata(rec, IMAGE)
    if args.stage == 'all':
        rec.test('image.installed_source_inventory', 'runtime', lambda: C.inspect_source(rec))
        rec.test('container.suite_executed', 'runtime', lambda: C.offline(rec))
        T.http_checks(rec)
        if T.DIRECT_CASE_HTTP:
            from protocol_422_checks import fixed
            fixed(rec)
        rec.test('runtime.outer_timeout_and_no_late_submission', 'time-budget', lambda: C.outer_timeout(rec))
        C.live(rec, inputs)
        rec.test('reports.independent_quote_audit', 'report-evidence', lambda: C.quote_checks(rec))
        authorization(rec, inputs)
    else:
        D.run_d(rec, inputs)
    replay(rec)
    rec.test('agent.harness_evidence_review', 'LLM-orchestration', lambda: T.review(rec))
    value = {'target': IMAGE, 'image_id': identity, 'counts': dict(Counter(r['status'] for r in rec.rows)),
             'official_score': None, 'official_admission_verified': False, 'run': root.name,
             'mandatory_failures': [r['id'] for r in rec.rows if r['status'] == 'FAIL' and r['level'] == 'required'],
             'quality_failures': [r['id'] for r in rec.rows if r['status'] == 'FAIL' and r['level'] == 'quality'],
             'note': 'Actual Harness account calls and immutable installed-image execution; no algorithm hand edits'}
    write_json(root / 'summary.json', value); print(json.dumps(value, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
