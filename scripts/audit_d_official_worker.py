"""Run generated sources with the unmodified official worker and public requests."""
import hashlib
import json
import subprocess
import shutil
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--audit', default='output/official-live-0410-d-source-20261025')
parser.add_argument('--verified-materials', default='build/official-materials-0410-verified')
args = parser.parse_args()
original = ROOT/'科研助手赛题材料/research-experiments-20261020-source-v1'
package = ROOT/args.verified_materials
assert not package.exists(), 'Do not overwrite verified materials'
shutil.copytree(original,package)
restored = []
manifest = json.loads((original/'PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))
for name,row in manifest['files'].items():
    path = package/name
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row['sha256']:
        raw = raw.replace(b'\r\n',b'\n')
        assert hashlib.sha256(raw).hexdigest() == row['sha256'], ('material differs beyond line endings',name)
        path.write_bytes(raw)
        restored.append(name)
audit = ROOT/args.audit
out = audit/'official-worker'
out.mkdir(exist_ok=True)
(out/'material-verification.json').write_text(json.dumps(dict(official_hashes_match=True,restored_crlf_files=restored,original_unchanged=True),ensure_ascii=False,indent=2),encoding='utf-8')
checks = []
for case_id in ('D-01','D-02','D-05'):
    case_root = package/case_id
    manifest = json.loads((case_root/'manifest.json').read_text(encoding='utf-8'))
    for name,digest in manifest['files'].items():
        assert hashlib.sha256((case_root/name).read_bytes()).hexdigest() == digest, (case_id,name)
    source = audit/case_id/'implementation.py'
    inputs = [package/'公开样例请求'/f'{case_id}.json', *sorted((package/'公开样例请求'/case_id).glob('*.json'))]
    for request in inputs:
        command = ['docker','run','--rm','-i','--pull=never','--platform','linux/amd64',
            '--network','none','--read-only','--user','65534:65534','--cap-drop','ALL',
            '--security-opt','no-new-privileges','--cpus','1','--memory','1024m','--pids-limit','64',
            '--ulimit','cpu=20:20','-e','PYTHONHASHSEED=42','-e','PYTHONDONTWRITEBYTECODE=1',
            '--mount',f'type=bind,source={case_root},target=/experiment,readonly',
            '--mount',f'type=bind,source={source},target=/experiment/implementation.py,readonly',
            '--workdir','/experiment','--entrypoint','python','python:3.9-slim','-B','-c',
            "import signal,runpy;signal.alarm(20);runpy.run_path('worker.py',run_name='__main__')"]
        result = subprocess.run(command,input=request.read_bytes(),capture_output=True,timeout=35)
        row = dict(case_id=case_id,request=request.relative_to(package).as_posix(),returncode=result.returncode,
            python='3.9',public_worker_only=True)
        try:
            value = json.loads(result.stdout)
            phase = json.loads(request.read_text(encoding='utf-8'))['stage']
            assert set(value) == ({'candidate'} if phase=='rank' else {'baseline','candidate','details'})
            row['valid_internal_envelope'] = True
        except (ValueError,AssertionError):
            row['valid_internal_envelope'] = False
        label = case_id+'-'+request.stem
        (out/(label+'.stdout.json')).write_bytes(result.stdout)
        (out/(label+'.stderr.txt')).write_bytes(result.stderr)
        checks.append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
        (out/'summary.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
assert all(row['returncode']==0 and row['valid_internal_envelope'] for row in checks)
