"""核对交付镜像的源码、SDK、依赖和秘密文件隔离，不输出密钥。"""
import hashlib
import json
import subprocess
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--image', default='research-assistant:0.4.9-b-20261025')
parser.add_argument('--output', default='output/b-failure-matrix-049/image-verification.json')
args = parser.parse_args()
IMAGE = args.image
OUT = ROOT / args.output
OUT.parent.mkdir(parents=True, exist_ok=True)


def run(*args):
    return subprocess.check_output(['docker', *args], text=True, encoding='utf-8')


source_code = """
import hashlib,json
from pathlib import Path
print(json.dumps({str(p.relative_to('/app')):hashlib.sha256(p.read_bytes()).hexdigest()
 for p in Path('/app').rglob('*') if p.is_file() and (p.suffix=='.py' or p.name in {'README.md','requirements.txt'})}))
"""
sdk_code = """
import agenthack,hashlib,json
from pathlib import Path
root=Path(agenthack.__file__).parent
print(json.dumps({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*.py')}))
"""
actual = json.loads(run('run', '--rm', '--network', 'none', '--entrypoint', 'python', IMAGE, '-c', source_code))
expected = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT / 'src').rglob('*.py') if 'd_algorithms' not in p.parts}
for name in ('agent.py', 'README.md', 'requirements.txt'):
    expected[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
base_sources = json.loads(run('run', '--rm', '--network', 'none', '--entrypoint', 'python', 'agenthack/base:1.1.1', '-c', source_code))
expected.update({k: v for k, v in base_sources.items() if k not in expected})
assert actual == expected, str(dict(extra=sorted(set(actual)-set(expected)), missing=sorted(set(expected)-set(actual)),
    changed=[k for k in expected if k in actual and expected[k] != actual[k]]))
sdk = json.loads(run('run', '--rm', '--network', 'none', '--entrypoint', 'python', IMAGE, '-c', sdk_code))
base_sdk = json.loads(run('run', '--rm', '--network', 'none', '--entrypoint', 'python', 'agenthack/base:1.1.1', '-c', sdk_code))
assert sdk == base_sdk, 'Official SDK was changed'
inspection = json.loads(run('image', 'inspect', IMAGE))[0]
assert inspection['Architecture'] == 'amd64' and inspection['Os'] == 'linux'
assert inspection['Config']['User'] == 'agent'
env = dict(item.split('=', 1) for item in inspection['Config']['Env'])
assert not any(env.get(k) for k in ('PLATFORM_API_KEY','CHALLENGE_LLM_API_KEY','OPENAI_API_KEY','RESEARCH_WORKSPACE_TEAM'))
secret_files = json.loads(run('run','--rm','--network','none','--entrypoint','python',IMAGE,'-c',
    "import json;from pathlib import Path;print(json.dumps([str(p) for p in Path('/app').rglob('*') if p.is_file() and (p.name.startswith('.env') or 'local' in p.name)]))"))
assert not secret_files, 'Local config unexpectedly included'
run('run', '--rm', '--network', 'none', '--entrypoint', 'python', IMAGE, '-m', 'pip', 'check')
secret = next(line.split('=',1)[1] for line in (ROOT/'.env.local').read_text(encoding='utf-8-sig').splitlines() if line.startswith('PLATFORM_API_KEY='))
assert secret and all(secret.encode() not in (ROOT / path).read_bytes() for path in expected if (ROOT / path).is_file())
OUT.write_text(json.dumps(dict(image=IMAGE,image_id=inspection['Id'],source_matches=True,
    official_sdk_unchanged=True,credential_files_excluded=True,linux_amd64_nonroot=True,
    dependencies_consistent=True,source_hashes=actual,sdk_hashes=sdk),ensure_ascii=False,indent=2),encoding='utf-8')
print('Image source, official SDK, credentials isolation, architecture/user and dependencies verified.')
