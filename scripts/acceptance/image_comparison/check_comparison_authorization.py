"""Independent report authorization checks, identical for both image versions."""
import json
import re
from pathlib import Path

from compare_images import BASE, RUN
from harness_bridge import write_json
from test_agent import HERE


def main():
    frozen = json.loads((BASE / 'frozen-inputs.json').read_text(encoding='utf-8'))
    corrected = json.loads((BASE / 'corrected-C-02-input.json').read_text(encoding='utf-8'))
    rows = []
    for version in ['source', 'v14']:
        for cid in ['C-01', 'C-02', 'E-01', 'E-02']:
            suffix = version + ('-c02-corrected' if cid == 'C-02' else '')
            root = HERE / 'runs' / (RUN + '-' + suffix)
            output = root / 'live-ABCE/output'
            task = corrected if cid == 'C-02' else frozen['tasks'][cid]
            allowed = set(task['input']['materials'])
            row = {'version': version, 'case_id': cid, 'status': 'BLOCKED'}
            try:
                report = json.loads((output / ('data/workspace_team/' + cid + '.json')).read_text(encoding='utf-8'))
                ledgers = list((output / 'data/workspace_team/work' / cid).glob('*/evidence.json'))
                if len(ledgers) != 1:
                    raise ValueError('No unique final evidence ledger')
                claims = json.loads(ledgers[0].read_text(encoding='utf-8'))['claims']
                actual = {r['filename'] for r in claims}
                cited = set(re.findall(r'[A-Za-z0-9_.-]+\.pdf', report['markdown']))
                if actual - allowed or cited - allowed:
                    raise AssertionError('Unassigned PDF appeared in evidence or report')
                if cid.startswith('C') and len(cited) < 5:
                    raise AssertionError('Survey references fewer than five assigned PDFs')
                row.update(status='PASS', assigned=sorted(allowed), ledger_files=sorted(actual),
                           referenced_filenames=sorted(cited), independent_host_check=True)
            except FileNotFoundError as exc:
                row['error'] = type(exc).__name__ + ': report or ledger missing'
            except (ValueError, KeyError, AssertionError) as exc:
                row.update(status='FAIL', error=type(exc).__name__ + ': ' + str(exc))
            rows.append(row)
    write_json(BASE / 'report-authorization.json', rows)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
