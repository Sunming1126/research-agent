"""Immutable-image paired comparison using public inputs and real Harness calls.

The fixed coordinator owns execution. Models plan, answer and review; they never
receive hidden oracles, Docker archives, credentials or arbitrary shell access.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from collections import Counter

import test_agent as T
from harness_bridge import HarnessBridge, call_harness, parse_model_json, write_json

RUN = 'comparison-20261023-v041'
IMAGES = {'source': 'research-assistant:0.4.1-source-v1',
          'v14': 'research-assistant:0.4.1-v14-20261023'}
BASE = T.HERE / 'runs' / RUN


def frozen_inputs():
    tasks = {c: T.request_for(c) for c in ['A-01', 'B-01', 'C-01', 'E-01']}
    variants = {
        'B-02': ('B-01', '稠密检索原始方法检索',
                 '只找提出 Dense Passage Retrieval 双编码器方法的原始论文。排除 RAG、FiD、BEIR、RETRO、InstructGPT 和 Self-RAG；不要因为引用 DPR 就返回其他论文。', None),
        'B-03': ('B-01', '异域检索评测基准检索',
                 '只找提出 BEIR 零样本异域信息检索评测基准的原始论文，排除检索器、检索增强生成方法和指令微调论文。', None),
        'C-02': ('C-01', '检索方法与评测基准的边界',
                 '只基于 DPR 与 BEIR 两篇材料，综述双编码器检索方法与零样本异域评测基准分别解决什么问题、训练与评测的区别、可核验的局限。不要把基准当成生成器，区分论文事实与推断。',
                 ['2004.04906-DPR.pdf', '2104.08663-BEIR.pdf']),
        'E-02': ('E-01', '检索预算与证据融合的消融方案',
                 '模拟观察：检索预算增大可能提升覆盖率但也增加无关证据，此观察尚未被实验验证。仅依据 RAG、FiD、Self-RAG 提出可证伪方案；给出基线、消融、量化指标与失败判据，所有新结果必须标为待验证。',
                 ['2005.11401-RAG.pdf', '2007.01282-FiD.pdf', '2310.11511-Self-RAG.pdf'])}
    for cid, (template, title, question, materials) in variants.items():
        task = json.loads(json.dumps(tasks[template]))
        task['task_id'] = 'comparison-public-' + cid
        task['input'].update(case_id=cid, title=title, input=question)
        if materials is not None:
            task['input']['materials'] = materials
        tasks[cid] = task
    for cid in ['D-01', 'D-02', 'D-05']:
        protocol = json.loads((T.MATERIAL / cid / 'protocol.json').read_text(encoding='utf-8'))
        task = T.request_for('D-01')
        task['task_id'] = 'comparison-public-' + cid
        task['input'].update(case_id=cid, title='新版源码来源公开测试 ' + cid,
                             input='根据本题公开方法和协议生成完整算法，交由官方客户端执行。三个指标：' +
                             '; '.join('name=' + x['name'] + ', unit=' + x['unit'] for x in protocol['metrics']) + '。',
                             materials=[cid + '/' + x for x in ['method.md', 'protocol.json', 'sample.json']])
        tasks[cid] = task
    value = {'scope': 'Public-data local 11-case suite; not official hidden cases or official scores',
             'seed': 42, 'reasoning': 'off', 'cpu': 1, 'memory_mb': 1024,
             'case_seconds': 180, 'tasks': tasks,
             'retrieval_expected': {
                 'B-01': ['2005.11401-RAG.pdf', '2007.01282-FiD.pdf', '2310.11511-Self-RAG.pdf'],
                 'B-02': ['2004.04906-DPR.pdf'], 'B-03': ['2104.08663-BEIR.pdf']}}
    path = BASE / 'frozen-inputs.json'
    if path.exists():
        T.require(json.loads(path.read_text(encoding='utf-8')) == value, 'Frozen input changed')
    else:
        write_json(path, value)
    return value


def metadata(rec, tag):
    info = json.loads(T.docker('image', 'inspect', tag))[0]
    # Pin actual container launches to the immutable ID, while preserving tag.
    T.IMAGE = info['Id']
    clean = json.loads(json.dumps(info))
    clean['Config']['Env'] = [e.split('=', 1)[0] + '=[REDACTED]' if re.search(r'KEY|TOKEN|SECRET', e.split('=', 1)[0], re.I) else e for e in clean['Config'].get('Env', [])]
    write_json(rec.root / 'image-inspect.json', clean)
    def check():
        T.require(info['Os'] == 'linux' and info['Architecture'] == 'amd64', 'Architecture mismatch')
        T.require(info['Config']['User'] not in ('', 'root', '0'), 'Image runs as root')
        T.require('8080/tcp' in info['Config'].get('ExposedPorts', {}), 'Missing port')
        return {'tag': tag, 'id': info['Id'], 'user': info['Config']['User'], 'platform': 'linux/amd64'}
    rec.test('image.immutable_identity', 'runtime', check)
    return info['Id']


def inspect_source(rec):
    """Read installed sources from each image, never replace with local checkout."""
    script = """import pathlib,json,hashlib,sys,importlib.metadata
root=pathlib.Path('/app');files={};texts={}
for p in sorted(root.rglob('*')):
 if p.is_file() and (p.suffix=='.py' or p.name in ['requirements.txt','pyproject.toml','Dockerfile']):
  data=p.read_bytes();rel=str(p.relative_to(root));files[rel]=hashlib.sha256(data).hexdigest();texts[rel]=data.decode('utf-8',errors='replace')
print(json.dumps({'files':files,'texts':texts,'python':sys.version,'packages':{p.metadata['Name']:p.version for p in importlib.metadata.distributions()}},ensure_ascii=False))"""
    raw = T.docker('run', '--rm', '--pull=never', '--network', 'none', '--read-only',
                   '-e', 'PYTHONDONTWRITEBYTECODE=1', '--entrypoint', 'python', T.IMAGE, '-c', script)
    value = json.loads(raw)
    write_json(rec.root / 'installed-source.json', value)
    return {'installed_files': len(value['files']), 'python': value['python']}


def offline(rec):
    out = rec.root / 'offline'; out.mkdir(exist_ok=True)
    log = T.docker('run', '--rm', '--pull=never', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges', '--cpus', '1', '--memory', '1024m', '--pids-limit', '64',
                   '--tmpfs', '/tmp:rw,nosuid,size=128m', '-e', 'PYTHONDONTWRITEBYTECODE=1',
                   '-e', 'AGENTHACK_TELEMETRY_REQUIRED=false',
                   '--mount', f'type=bind,source={T.HERE},target=/testkit,readonly',
                   '--mount', f'type=bind,source={T.MATERIAL},target=/data/experiments,readonly',
                   '--mount', f'type=bind,source={out},target=/results', '--entrypoint', 'python',
                   T.IMAGE, '/testkit/comparison_container_checks.py', timeout=90)
    (out / 'stdout.jsonl').write_text(log, encoding='utf-8')
    rows = json.loads((out / 'offline-checks.json').read_text(encoding='utf-8'))
    for row in rows:
        rec.add('container.' + row['id'], row['group'], row['status'],
                evidence=row.get('evidence'), error=row.get('error'), level=row.get('level', 'required'))
    return {'checks': len(rows)}


def outer_timeout(rec):
    root = rec.root / 'outer-timeout'
    with T.FaultGateway('slow') as gateway, T.target(root, bridge=gateway) as (base, output, name):
        task = T.request_for('A-01'); task['constraints']['timeout_ms'] = 100
        started = time.monotonic(); code, body = T.http(base + '/solve', task, timeout=10)
        elapsed = time.monotonic() - started
        write_json(root / 'response.json', {'http': code, 'seconds': elapsed, 'response': body})
        T.require(body.get('status') == 'timeout', 'Outer deadline was not enforced')
        time.sleep(1)
        T.require(T.http(base + '/health', timeout=2)[0] == 200, 'Unhealthy after timeout')
        T.require(not (output / 'data/workspace_team/A-01.json').exists(), 'Late submission persisted')
        return {'fixture_not_real_llm': True, 'seconds': elapsed, 'no_late_submission': True}


def live(rec, inputs):
    with HarnessBridge(rec.root / 'harness-model-calls') as bridge:
        with T.target(rec.root / 'live-ABCE', bridge=bridge) as (base, output, name):
            for cid in ['A-01', 'B-01', 'B-02', 'B-03', 'C-01', 'C-02', 'E-01', 'E-02']:
                def solve(cid=cid):
                    task = inputs['tasks'][cid]; before = len(bridge.records)
                    started = time.monotonic(); code, body = T.solve_http(base, task, rec.root / 'live-ABCE' / (cid + '-wire-request.json'))
                    elapsed = time.monotonic() - started
                    write_json(rec.root / 'live-ABCE' / (cid + '-response.json'),
                               {'http': code, 'seconds': elapsed, 'response': body})
                    result = T.unpack_answer(body)
                    evidence = T.independent_contract(task, result, output)
                    T.require(elapsed <= 180, 'Case exceeded budget')
                    T.require(len(bridge.records) > before, 'No actual current-case model call')
                    return dict(evidence, seconds=elapsed, real_model_calls=len(bridge.records) - before)
                row = rec.test('live.' + cid, 'A-E-live', solve)
                if cid.startswith('B') and row['status'] == 'PASS':
                    def relevant(cid=cid):
                        data = json.loads((output / ('data/workspace_team/' + cid + '.json')).read_text(encoding='utf-8'))
                        actual = sorted(p['filename'] for p in data['papers']); expected = sorted(inputs['retrieval_expected'][cid])
                        T.require(actual == expected, 'Actual ' + repr(actual) + '; expected ' + repr(expected))
                        return {'matched': actual, 'frozen_public_relevance_expectation': True}
                    rec.test('retrieval.exact.' + cid, 'retrieval-quality', relevant, level='quality')
        for cid in ['D-01', 'D-02', 'D-05']:
            source_box = {}
            def generate(cid=cid):
                directory = rec.root / ('live-' + cid); task = inputs['tasks'][cid]
                before = len(bridge.records)
                with T.target(directory, bridge=bridge, case_id=cid) as (base, output, name):
                    started = time.monotonic(); code, body = T.solve_http(base, task, directory / 'wire-request.json')
                    elapsed = time.monotonic() - started
                    write_json(directory / 'response.json', {'http': code, 'seconds': elapsed, 'response': body})
                    result = T.unpack_answer(body); T.independent_contract(task, result, output)
                    sources = list((output / 'data/workspace_team/work' / cid).glob('*/implementation.py'))
                    T.require(len(sources) == 1, 'No unique generated source')
                    source = sources[0]
                    generation = json.loads((source.parent / 'generation-response.json').read_text(encoding='utf-8'))
                    content = generation['choices'][0]['message']['content']
                    normalized = source.read_text(encoding='utf-8').replace('\r\n', '\n').rstrip('\n')
                    candidates = [content, *re.findall(r'^```(?:python|py)?[ \t]*\r?\n(.*?)^```[ \t]*$', content, re.M | re.S)]
                    T.require(any(c.replace('\r\n', '\n').replace('\r', '\n').rstrip('\n') == normalized for c in candidates), 'Source changed beyond newline normalization')
                    calls = bridge.records[before:]
                    T.require(calls, 'No actual current-case model call')
                    T.require(elapsed <= 180, 'Case exceeded 180 seconds')
                    T.require(result['status'] != 'completed', 'D falsely completed without formal service')
                    history_file = source.parent / 'public-selftest.json'
                    if history_file.exists():
                        history = json.loads(history_file.read_text(encoding='utf-8'))
                        T.require(history.get('attempts') and history['attempts'][-1].get('status') not in ['failed', 'error'], 'Final internal self-test failed; source is rejected')
                    source_box['path'] = source
                    return {'case_id': cid, 'source': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                            'seconds': elapsed, 'real_model_calls': len(calls), 'official_execution': 'BLOCKED'}
            rec.test('live.' + cid + '.model_source_generation', 'D-source-live', generate)
            if 'path' in source_box:
                T.public_samples(rec, cid, source_box['path'])
            else:
                rec.add('public_algorithm.' + cid, 'D-public-algorithm', 'BLOCKED', error='No accepted source from this run')
    rec.add('official.source_admission_and_full_scoring', 'official-platform', 'BLOCKED',
            error='Official Gateway/source signature/private cases/trusted Unix-socket runner are not supplied; public tests cannot replace them')


def quote_checks(rec):
    out = rec.root / 'report-quote-audit'; out.mkdir(exist_ok=True)
    text = T.docker('run', '--rm', '--pull=never', '--network', 'none', '--read-only',
                    '-e', 'PYTHONDONTWRITEBYTECODE=1',
                    '--mount', f'type=bind,source={T.HERE},target=/testkit,readonly',
                    '--mount', f'type=bind,source={rec.root / "live-ABCE/output"},target=/output,readonly',
                    '--mount', f'type=bind,source={T.SHARE / "papers"},target=/papers,readonly',
                    '--mount', f'type=bind,source={out},target=/results',
                    '--entrypoint', 'python', T.IMAGE, '/testkit/comparison_report_evidence.py', timeout=60)
    (out / 'stdout.json').write_text(text, encoding='utf-8')
    for row in json.loads((out / 'report-evidence.json').read_text(encoding='utf-8')):
        # Missing report is already a live failure; no report is not a quote PASS.
        rec.add('quotes.' + row['case_id'], 'report-evidence', row['status'], evidence=row,
                error=row.get('reason'), level='quality')


def write_summary(rec, tag, identity):
    value = {'target': tag, 'immutable_id': identity, 'run_directory': str(rec.root),
             'counts': dict(Counter(r['status'] for r in rec.rows)),
             'official_score': None, 'all_requirements_verified': False,
             'single_stochastic_round': True, 'frozen_input_sha256': hashlib.sha256((BASE / 'frozen-inputs.json').read_bytes()).hexdigest()}
    write_json(rec.root / 'summary.json', value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True, choices=IMAGES)
    parser.add_argument('--stage', choices=['all', 'live', 'offline', 'finalize'], default='all')
    args = parser.parse_args(); os.environ['TEST_HARNESS_REASONING'] = 'off'
    inputs = frozen_inputs()
    root = T.HERE / 'runs' / (RUN + '-' + args.version); root.mkdir(parents=True, exist_ok=True)
    rec = T.Recorder(root)
    if (root / 'checks.json').exists():
        rec.rows = json.loads((root / 'checks.json').read_text(encoding='utf-8'))
    tag = IMAGES[args.version]; identity = metadata(rec, tag)
    if args.stage in ('all', 'offline'):
        rec.test('image.installed_source_inventory', 'runtime', lambda: inspect_source(rec))
        rec.test('container.suite_executed', 'runtime', lambda: offline(rec))
        T.http_checks(rec)
        rec.test('runtime.outer_timeout_and_no_late_submission', 'time-budget', lambda: outer_timeout(rec))
    if args.stage in ('all', 'live'):
        live(rec, inputs)
        rec.test('reports.independent_quote_audit_executed', 'report-evidence', lambda: quote_checks(rec))
        p = T.command(['python', str(T.HERE / 'check_reproducibility.py'), '--run-id', root.name], timeout=120)
        (root / 'reproducibility-stdout.txt').write_bytes(p.stdout + p.stderr)
        if p.returncode:
            rec.add('replay.runner', 'D-reproducibility', 'FAIL', error=p.stderr.decode('utf-8', errors='replace')[-1000:])
        else:
            for row in json.loads((root / 'reproducibility.json').read_text(encoding='utf-8')):
                rec.add(row['id'], row['group'], row['status'], evidence=row, error=row.get('error'), level='quality')
        rec.test('agent.harness_evidence_review', 'LLM-orchestration', lambda: T.review(rec))
    print(json.dumps(write_summary(rec, tag, identity), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
