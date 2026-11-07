"""Explicitly labelled Codex C/E offline checks; zero target-model calls."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / '协同开发/research-agent-friend-development'
HERE = ROOT / '镜像测试'
BASE = 'cd733b20d034ba0b02b900e65c4d7590a50d4964'
TIME_BASE = '08b28f01bfe759dc3d7bd1d46d026bbd87005878'
PYTHON = HERE / 'runtime/windows-python312/python.exe'


def native_proof(request_id, stage):
    from native_workspace_harness import workspace, selection, native_rows
    sid = 'session-9c903d0e-5884-44fe-bc7f-3719da00864b'
    request = json.loads((ROOT / '.session-audit/score-native-requests-20261102' / (request_id + '.json')).read_text(encoding='utf-8'))
    allowed = request['stage'] == stage or (request['stage'] == 'time-paired' and
              request['factor'] == 'time' and stage in ('baseline','candidate','full-regression','sdk'))
    assert request['session_id'] == sid and allowed and sid in workspace()['sessionIds']
    selection()
    for _ in range(40):
        rows, _ = native_rows(sid)
        for row in rows:
            if row.get('seq', 0) <= request['before_seq'] or row.get('type') != 'assistant/message':
                continue
            for part in row.get('data', {}).get('message', {}).get('content', []):
                text = json.dumps(part, ensure_ascii=False)
                if (part.get('type') == 'tool-call' and 'score_compat_20261102.py' in text
                        and request_id in text and '--stage ' + request['stage'] in text):
                    header = max((r for r in rows if r.get('type') == 'request/header' and r['seq'] <= row['seq']), key=lambda r: r['seq'])
                    cfg = header['data']['header']['config']
                    assert cfg.get('provider') == 'magic' and cfg.get('model') == 'deepseek-v4.1-flash'
                    return {**request, 'native_tool_seq': row['seq'],
                            'native_tool_call_sha256': hashlib.sha256(text.encode()).hexdigest(),
                            'config_header_seq': header['seq'], 'native_tool_execution_verified': True}
        time.sleep(.2)
    raise RuntimeError('No fresh original native tool call; no test launched')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def hashes(source):
    return {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [source / 'agent.py', *sorted((source / 'src').rglob('*.py'))]}


def freeze(base):
    target = HERE / ('审核/score-compat-20261102-' + base[:7] + '/source')
    if not target.exists():
        target.mkdir(parents=True, exist_ok=False)
        data = subprocess.check_output(['git', 'archive', '--format=tar', base, '--',
                                       'src', 'tests', 'agent.py', 'pyproject.toml'], cwd=REPO)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for item in archive:
                if not item.isfile():
                    continue
                path = target / item.name
                assert path.resolve().is_relative_to(target.resolve())
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.extractfile(item).read())
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=['baseline', 'baseline-path', 'candidate', 'ce-regression', 'full-regression', 'sdk', 'time-paired', 'e2e-full', 'e2e-d02', 'e2e-d02-prompt', 'platform-canary', 'd05-diagnosis', 'academic-regression', 'academic-be-pair', 'academic-e-pair', 'academic-b-score', 'academic-e-three', 'academic-linux', 'academic-score-preflight', 'academic-score-final', 'be-focus', 'be-release', 'be-platform', 'be-submit', 'd-contract'], required=True)
    parser.add_argument('--factor', choices=['ce', 'time', 'e2e'], default='ce')
    parser.add_argument('--native-request')
    args = parser.parse_args()
    from baseline_submission_guard_20261106 import enforce as enforce_baseline_preservation
    candidate_ref = None
    if args.native_request:
        request_path = ROOT / '.session-audit/score-native-requests-20261102' / (args.native_request + '.json')
        if request_path.is_file():
            candidate_ref = json.loads(request_path.read_text(encoding='utf-8')).get('candidate_commit')
    enforce_baseline_preservation(args.stage, candidate_ref)
    proof = native_proof(args.native_request, args.stage) if args.native_request else None
    if args.stage == 'd-contract':
        assert proof, 'D contract tests require original native coordination'
        from d_contract_20261106 import run
        run(proof)
        return
    if args.stage == 'academic-regression':
        assert proof, 'Academic regression requires original native coordination'
        from academic_regression_20261104 import run
        run(proof)
        return
    if args.stage == 'd05-diagnosis':
        assert proof, 'D05 diagnosis requires original native orchestration'
        from d05_diagnosis_20261103 import run
        run(proof)
        return
    if args.stage == 'platform-canary':
        assert proof, 'Official evaluation requires original native coordination'
        from score_platform_canary_20261102 import run
        run(proof)
        return
    if args.stage == 'be-focus':
        from academic_regression_20261104 import run as regression
        from score_target_e2e_20261102 import run as e2e
        controls = regression(proof)
        if controls['results']['candidate']['returncode'] != 0:
            raise RuntimeError('B/E regression failed before real model validation')
        e2e(proof)
        return
    if args.stage == 'be-release':
        from be_release_20261105 import run
        run(proof)
        return
    if args.stage == 'be-platform':
        from be_platform_20261105 import run
        run(proof)
        return
    if args.stage == 'be-submit':
        from be_release_20261105 import run as prepare
        from be_platform_20261105 import run as submit
        prepare(proof)
        submit(proof)
        return
    if args.stage in ('academic-score-preflight', 'academic-score-final'):
        from academic_regression_20261104 import run as regression
        from academic_linux_20261105 import run as linux
        from score_target_e2e_20261102 import run as e2e
        controls = regression(proof)
        if controls['results']['candidate']['returncode'] != 0:
            raise RuntimeError('Candidate host regression failed; target-model runs were not started')
        e2e({**proof, 'stage': 'academic-b-score'})
        try:
            linux({**proof, 'stage': 'academic-linux'})
        except (RuntimeError, subprocess.SubprocessError, AssertionError) as exc:
            print(f'Linux gate remains closed: {exc}', flush=True)
        if args.stage == 'academic-score-preflight':
            e2e({**proof, 'stage': 'academic-e-three'})
        return
    if args.stage == 'academic-linux':
        from academic_linux_20261105 import run
        run(proof)
        return
    if args.stage in ('e2e-full','e2e-d02','e2e-d02-prompt','academic-be-pair','academic-e-pair','academic-b-score','academic-e-three'):
        assert proof, 'Real target calls require the original native coordinator'
        if args.stage in {'academic-be-pair', 'academic-e-pair', 'academic-e-three'}:
            from academic_regression_20261104 import run as run_regression
            regression = run_regression(proof)
            if regression['results']['candidate']['returncode'] != 0:
                raise RuntimeError('Candidate unit regression failed; live comparison was not started')
        from score_target_e2e_20261102 import run
        run(proof)
        return
    if args.stage == 'time-paired':
        assert args.factor == 'time' and proof, 'D controls require original native orchestration'
        before = hashes(REPO)
        batch = HERE/'runs'/('score-time-paired-20261102-'+time.strftime('%H%M%S')+'-'+uuid.uuid4().hex[:5])
        batch.mkdir()
        save(batch/'native-proof.json',proof)
        results=[]
        save(batch/'batch.json',{'status':'running','results':[],'target_model_calls':0,'platform_acceptance':False})
        # Fixed alternating order, three repetitions per source; no real model.
        for ordinal, stage in enumerate(['baseline','candidate','candidate','baseline','baseline','candidate','full-regression','sdk']):
            p=subprocess.run([sys.executable,'-X','utf8',str(Path(__file__).resolve()),'--stage',stage,'--factor','time',
                              '--native-request',args.native_request],capture_output=True,timeout=700)
            (batch/f'{ordinal:02}-{stage}.stdout.txt').write_bytes(p.stdout)
            (batch/f'{ordinal:02}-{stage}.stderr.txt').write_bytes(p.stderr)
            if p.returncode:
                raise RuntimeError('Batch child coordinator failed; retain earlier runs, no automatic retry')
            result=json.loads(p.stdout.decode('utf-8'))
            results.append(result)
            save(batch/'batch.json',{'status':'running','results':results,'target_model_calls':0,'platform_acceptance':False})
            if result.get('blocked_permission'):
                break
        assert hashes(REPO)==before
        save(batch/'batch.json',{'status':'completed' if len(results)==8 else 'blocked',
             'baseline':TIME_BASE,'order':[r.get('directory') for r in results],
             'results':results,'target_model_calls':0,'platform_acceptance':False})
        print(json.dumps({'directory':str(batch),'results':results,'target_model_calls':0},ensure_ascii=False))
        return
    base = TIME_BASE if args.factor == 'time' else BASE
    source = freeze(base) if args.stage in ('baseline', 'baseline-path') else REPO
    out = HERE / 'runs' / ('score-' + args.factor + '-' + args.stage + '-20261102-' + time.strftime('%H%M%S') + '-' + uuid.uuid4().hex[:5])
    out.mkdir(parents=True, exist_ok=False)
    if proof:
        save(out / 'native-proof.json', proof)
    paths = [str(source), str(source / 'src'), str(REPO / 'tests'), str(source / 'tests'),
             str(HERE / 'runtime/py312-windows'), str(HERE)]
    if args.stage == 'baseline-path':
        tests = [str(source / 'tests/test_contract_044.py') + '::test_output_mounted_library_is_selected_without_searching_team_directories']
    elif args.stage in ('baseline', 'candidate'):
        tests = [str(REPO / 'tests' / ('test_current_ce_limits.py' if args.factor == 'ce' else 'test_current_time_limits.py'))]
    elif args.stage == 'ce-regression':
        tests = [str(p) for p in (REPO / 'tests').glob('test_*.py') if any(x in p.name for x in
                 ('report', 'e4', 'idea', 'c200', 'c201', 'citation', 'evidence', 'contracts', 'validation', 'http_runtime', 'current_ce'))]
    elif args.stage == 'sdk':
        tests = [str(REPO / 'tests' / name) for name in ('test_c201_http.py', 'test_e4_http.py', 'test_b5_http.py')]
    else:
        tests = [str(REPO / 'tests')]
    before = hashes(source)
    save(out / 'source-files.json', before)
    execution = 'Original Harness native coordinator, no target model' if proof else 'Codex offline; existing scoped C/E source regression authorization'
    save(out / 'plan.json', {'stage': args.stage, 'factor': args.factor, 'source': str(source), 'tests': tests,
                             'baseline': base, 'target_model_calls': 0, 'execution': execution})
    params = ['-q', '-c', str(source / 'pyproject.toml'), '-o', 'pythonpath=', '-o', 'addopts=',
              '-o', 'faulthandler_timeout=25', '--durations=8', '--junitxml=' + str(out / 'junit.xml'),
              '--basetemp=' + str(out / 'temp'), *tests]
    boot = ('import sys;sys.path[:0]=' + repr(paths) + ';import c_no_model_guard_20261030;'
            'import research_agent;from pathlib import Path;assert Path(research_agent.__file__).resolve().is_relative_to(Path('
            + repr(str(source)) + '));import pytest;raise SystemExit(pytest.main(' + repr(params) + '))')
    env = os.environ.copy()
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
               AGENTHACK_TELEMETRY_REQUIRED='false', ANIC_C_TEST_GUARD_LOG=str(out / 'network-guard.json'),
               RESEARCH_TEST_PAPERS=str(REPO / 'team_share/papers'),
               PUBLIC_EXPERIMENT_ROOT=str(ROOT / '测试输入/交付镜像/team_share/experiments/research-experiments'),
               LAYERED_PUBLIC_EXPERIMENT_ROOT=str(REPO / '科研助手赛题材料/research-experiments-20261020-source-v1'))
    temporary = out / 'temporary'
    temporary.mkdir()
    env.update(TMP=str(temporary), TEMP=str(temporary))
    # Detect the native sandbox's private-directory restriction before hundreds
    # of setup errors. This probe does not grant access or relax any protection.
    probe = ('import os,pathlib; p=pathlib.Path(' + repr(str(temporary / 'permission-probe')) + ');'
             'p.mkdir();os.chmod(p,0o700);list(p.iterdir());(p/"probe.txt").write_text("local probe");'
             'assert (p/"probe.txt").read_text()=="local probe"')
    check = subprocess.run([str(PYTHON), '-X', 'utf8', '-c', probe], env=env, capture_output=True, timeout=15)
    save(out / 'permission-preflight.json', {'passed': check.returncode == 0, 'returncode': check.returncode,
                                           'purpose': 'Detect own private-directory read/write, never alter sandbox policy'})
    if check.returncode:
        (out / 'permission-preflight-stderr.txt').write_bytes(check.stderr)
        result = {'returncode': check.returncode, 'blocked_permission': True, 'tests_executed': 0,
                  'execution': execution, 'native_harness_execution': bool(proof),
                  'platform_acceptance': False, 'target_model_calls': 0}
        save(out / 'result.json', result)
        save(ROOT / '.session-audit/score-compat-latest-20261102.json', {'run': str(out), 'stage': args.stage, 'factor': args.factor})
        print(json.dumps({'directory': str(out), **result}, ensure_ascii=False))
        return
    if args.stage == 'sdk':
        env.pop('RESEARCH_RUNTIME', None)
    else:
        env['RESEARCH_RUNTIME'] = 'local'
    started = time.monotonic()
    with (out / 'stdout.txt').open('wb') as stdout, (out / 'stderr.txt').open('wb') as stderr:
        child = subprocess.Popen([str(PYTHON), '-X', 'utf8', '-c', boot], cwd=source, env=env, stdout=stdout, stderr=stderr)
        save(out / 'owned-process.json', {'child_pid': child.pid, 'run_directory': str(out)})
        try:
            code = child.wait(timeout=600)
        except subprocess.TimeoutExpired:
            child.kill(); child.wait(); code = 124
    assert hashes(source) == before, 'Source changed during run'
    result = {'returncode': code, 'seconds': round(time.monotonic() - started, 3),
              'execution': execution, 'native_harness_execution': bool(proof),
              'platform_acceptance': False, 'target_model_calls': 0}
    if (out / 'junit.xml').exists():
        suites = list(ET.parse(out / 'junit.xml').getroot().iter('testsuite'))
        result.update({key: sum(int(s.get(key, '0')) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')})
        result['passed'] = result['tests'] - result['failures'] - result['errors'] - result['skipped']
    save(out / 'result.json', result)
    save(ROOT / '.session-audit/score-compat-latest-20261102.json', {'run': str(out), 'stage': args.stage, 'factor': args.factor})
    print(json.dumps({'directory': str(out), **result}, ensure_ascii=False))


if __name__ == '__main__':
    main()
