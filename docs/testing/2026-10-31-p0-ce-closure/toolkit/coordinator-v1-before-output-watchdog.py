"""User-authorized offline C/E comparison; never calls a target model."""
import argparse
import hashlib
import io
import json
import os
import subprocess
import tarfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / '镜像测试'
SOURCE = ROOT / '协同开发/research-agent-stability-20261027'
BASE_REV = '316c2d2c1b5c96c59b09fe906356f18d8d0364d8'
BASE = HERE / '审核/p0-ce-contract-20261031-316c2d2/source'
PYTHON = HERE / 'runtime/windows-python312/python.exe'
AUTH = ROOT / '.session-audit/p0-ce-offline-authorization-20261031.json'
LATEST = ROOT / '.session-audit/p0-ce-contract-latest-20261031.json'

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [root / 'agent.py', *sorted((root / 'src').rglob('*.py'))]}

def freeze():
    if BASE.exists():
        return
    data = subprocess.check_output(['git', 'archive', '--format=tar', BASE_REV,
                                   '--', 'src', 'tests', 'agent.py', 'pyproject.toml'], cwd=SOURCE)
    BASE.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for item in archive:
            if not item.isfile():
                continue
            path = BASE / item.name
            if not path.resolve().is_relative_to(BASE.resolve()):
                raise RuntimeError('Unsafe frozen archive path')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.extractfile(item).read())

def run(source, out, tests):
    out.mkdir(parents=True, exist_ok=False)
    paths = [str(source), str(source / 'src'), str(source / 'tests'), str(HERE / 'runtime/py312-windows'), str(HERE)]
    args = ['-q', '-c', str(source / 'pyproject.toml'), '-o', 'pythonpath=', '-o', 'addopts=',
            '--junitxml=' + str(out / 'junit.xml'), '--basetemp=' + str(out / 'temp'), *map(str, tests)]
    boot = ('import sys;sys.path[:0]=' + repr(paths) + ';import c_no_model_guard_20261030;'
            'import research_agent;from pathlib import Path;'
            'assert Path(research_agent.__file__).resolve().is_relative_to(Path(' + repr(str(source)) + ').resolve());'
            'import pytest;raise SystemExit(pytest.main(' + repr(args) + '))')
    env = os.environ.copy()
    env.pop('ANIC_OFFLINE_TEST_AUTHORIZATION', None)
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
               RESEARCH_RUNTIME='local', AGENTHACK_TELEMETRY_REQUIRED='false',
               ANIC_C_TEST_GUARD_LOG=str(out / 'network-guard.json'),
               P0_CASE_EVIDENCE_DIR=str(out / 'cases'),
               RESEARCH_TEST_PAPERS=str(ROOT / '测试输入/交付镜像/team_share/papers'),
               PUBLIC_EXPERIMENT_ROOT=str(ROOT / '测试输入/交付镜像/team_share/experiments/research-experiments'))
    started = time.monotonic()
    result = subprocess.run([str(PYTHON), '-X', 'utf8', '-c', boot], cwd=source, env=env,
                            capture_output=True, timeout=600)
    (out / 'stdout.txt').write_bytes(result.stdout)
    (out / 'stderr.txt').write_bytes(result.stderr)
    record = {'returncode': result.returncode, 'seconds': round(time.monotonic() - started, 3),
              'execution': 'Codex offline with explicit 2026-10-31 C/E authorization',
              'native_harness_execution': False, 'target_model_calls': 0}
    if (out / 'junit.xml').is_file():
        suites = list(ET.parse(out / 'junit.xml').getroot().iter('testsuite'))
        record.update({key: sum(int(s.get(key, '0')) for s in suites) for key in ['tests', 'failures', 'errors', 'skipped']})
        record['passed'] = record['tests'] - record['failures'] - record['errors'] - record['skipped']
    save(out / 'execution.json', record)
    return record

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=['baseline', 'candidate', 'regression'], required=True)
    args = parser.parse_args()
    auth = json.loads(AUTH.read_text(encoding='utf-8'))
    if auth.get('user_reply') != '允许本轮C/E离线测试' or auth.get('target_model_calls_allowed') is not False:
        raise RuntimeError('Explicit scoped authorization is required')
    freeze()
    out = HERE / 'runs' / ('p0-ce-' + args.stage + '-20261031-' + time.strftime('%H%M%S') + '-' + uuid.uuid4().hex[:6])
    out.mkdir(parents=True, exist_ok=False)
    frozen = hashes(BASE)
    current = hashes(SOURCE)
    save(out / 'authorization.json', auth)
    save(out / 'baseline-source-files.json', frozen)
    save(out / 'candidate-source-files.json', current)
    tests = [SOURCE / 'tests/test_ce_final_contract_recovery.py'] if args.stage != 'regression' else [SOURCE / 'tests']
    source = BASE if args.stage == 'baseline' else SOURCE
    record = run(source, out / args.stage, tests)
    if hashes(BASE) != frozen or hashes(SOURCE) != current:
        raise RuntimeError('Source changed during execution')
    summary = {'stage': args.stage, 'run': str(out), 'baseline_revision': BASE_REV,
               'application_source_unchanged_during_run': True, 'source_files': len(current),
               'target_model_calls': 0, 'native_harness_execution': False, 'result': record}
    save(out / 'summary.json', summary)
    save(LATEST, summary)
    print(json.dumps(summary, ensure_ascii=True), flush=True)
    if args.stage != 'baseline' and record['returncode'] != 0:
        raise SystemExit(record['returncode'])

if __name__ == '__main__':
    main()
