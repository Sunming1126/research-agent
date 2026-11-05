"""Summarize saved native public B/E runs without calling a model or assigning scores."""
import argparse
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def summarize(study):
    protocol = load(study / 'protocol.json')
    summary = load(study / 'summary.json')
    native = load(study / 'native-proof.json')
    rows = []
    for original in summary['results']:
        row = {key: original.get(key) for key in (
            'variant', 'case_id', 'status', 'http_status', 'trace_id', 'seconds',
            'model_calls', 'total_tokens', 'quota_exhausted')}
        row['papers'] = original.get('result', {}).get('papers', [])
        directory = Path(original['directory'])
        if original['case_id'].startswith('E-'):
            artifacts = list((directory / 'output').rglob('evidence.json'))
            row['evidence_artifacts'] = len(artifacts)
            if len(artifacts) == 1:
                evidence = load(artifacts[0])
                row['audit'] = evidence['audit']
                row['evidence_sha256'] = hashlib.sha256(artifacts[0].read_bytes()).hexdigest()
            markdown = original.get('result', {}).get('markdown')
            if isinstance(markdown, str):
                row['report_sha256'] = hashlib.sha256(markdown.encode()).hexdigest()
        rows.append(row)
    return {
        'scope': 'native Harness, actual target model, public self-authored fixtures; no official scores',
        'study': study.name, 'complete': summary['complete'],
        'sources': protocol['sources'], 'input_revision': protocol.get('input_revision'),
        'public_case_inputs': [c for c in protocol['cases'] if c['case_id'] in {'B-02', 'E-04'}],
        'public_control_inputs': protocol.get('control_cases', {}),
        'input_sha256': protocol['input_sha256'], 'material_sha256': protocol['files'],
        'contrasts': protocol.get('contrasts'), 'coordinator_sha256': protocol.get('coordinator_sha256'),
        'native_tool_execution_verified': native.get('native_tool_execution_verified'),
        'native_proof_sha256': hashlib.sha256((study / 'native-proof.json').read_bytes()).hexdigest(),
        'official_score': None, 'results': rows,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('study', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = summarize(args.study.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'complete': result['complete'], 'rows': len(result['results']),
                      'output': str(args.output)}, ensure_ascii=True))
