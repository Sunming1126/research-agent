"""Export the verified C/D release without replacing any delivered tar."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.4.10-cd-20261025'
IMAGE = 'research-assistant:'+VERSION
def load(path):
    return json.loads((ROOT/path).read_text(encoding='utf-8'))
def docker(*args):
    return subprocess.check_output(['docker',*args],text=True,encoding='utf-8')

inspection = json.loads(docker('image','inspect',IMAGE))[0]
verification = load('output/cd-release-0410/image-verification.json')
assert verification['image_id'] == inspection['Id']
run = 'output/official-live-0410-cd-release-20261025'
provenance = load(run+'/provenance.json')
assert provenance['image_id']==inspection['Id'] and not provenance['oom_killed']
rows = load(run+'/summary.json')['rows']
assert len(rows)==5 and all(r['format_and_files_passed'] and r['local_session_trace_received'] and r['seconds']<180 for r in rows)
for row in rows:
    body = load(run+'/cases/'+row['label']+'/response.json')
    if row['case_id'].startswith('D-'):
        assert body['status']=='materials_missing' and all(m['observed'] is None for m in body['metrics'])
    else:
        assert row['llm_calls']>0 and all(a['ok'] is True for a in row['report_audits'])
sources = load('output/official-live-0410-d-source-20261025/summary.json')
assert len(sources)==3 and all(r['status']=='passed_public_sample' and r['source_matches_current_response'] for r in sources)
workers = load('output/official-live-0410-d-source-20261025/official-worker/validated-summary.json')
assert len(workers)==13 and all(r['returncode']==0 and r['invariants'] for r in workers)
secret = next(line.split('=',1)[1] for line in (ROOT/'.env.local').read_text(encoding='utf-8-sig').splitlines() if line.startswith('PLATFORM_API_KEY='))
for directory in (ROOT/run,ROOT/'output/official-live-0410-d-source-20261025'):
    assert not any(secret.encode() in p.read_bytes() for p in directory.rglob('*') if p.is_file())
target = ROOT/'team_share/image'/f'research-assistant-{VERSION}.tar'
partial = target.with_suffix('.tar.partial')
assert not target.exists() and not partial.exists()
docker('save','-o',str(partial),IMAGE)
with partial.open('rb') as stream:
    digest = hashlib.file_digest(stream,'sha256').hexdigest()
docker('load','-i',str(partial))
assert json.loads(docker('image','inspect',IMAGE))[0]['Id']==inspection['Id']
partial.rename(target)
target.with_suffix('.tar.sha256').write_text(f'{digest}  {target.name}\n',encoding='ascii')
release = ROOT/'team_share/releases'/VERSION
release.mkdir(parents=True,exist_ok=True)
manifest = dict(version=VERSION,image=IMAGE,image_id=inspection['Id'],tar=target.relative_to(ROOT).as_posix(),
    tar_bytes=target.stat().st_size,tar_sha256=digest,tar_readback_verified=True,official_sdk_unchanged=True,
    source_matches_local=True,credentials_excluded=True,offline_tests=dict(passed=202,skipped=4),
    official_api_http_rows=rows,d_public_source_runs=sources,official_worker_public_inputs=len(workers),
    official_hidden_questions=False,formal_d_provenance_admission_tested=False,platform_accepted=False,
    source_eval='eval-20261024-fee4379f',online_exception_confirmed=False)
(release/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
(release/'README.md').write_text(f'''# {VERSION}

交付：`{target.name}`，{target.stat().st_size}字节。

SHA-256：`{digest}`

镜像ID：`{inspection['Id']}`。已docker load回读；官方SDK未修改，源码和依赖校验通过，未内置密钥或固定队伍名。

保留0.4.9 B修复；C阶段时间预留、全文解析与引用恢复；D明确指标解析、协议交叉核对、实验目录读取、真实失败部分结果及回执保留。

官方模型C-01/C-03实测82.256/73.331秒，均HTTP200且正文/文件/本地追踪通过。D缺少正式会话时三题如实返回materials_missing并落盘；实际官方模型生成的三个D实现通过公开样例及Python3.9断网官方worker的13个公开输入检查。202项不同相关回归通过，4项条件跳过。

这些不是隐藏题或正式D凭证/实验服务验收，不能保证平台得分；线上具体异常仍待查。E未重测。完整核对见`docs/2026-10-25-CD类修复与官方材料核对.md`，本清单保存实际测试和限制。请上传新的0.4.10 tar，不要误传旧0.4.8。
''',encoding='utf-8')
print(json.dumps(dict(tar=str(target),sha256=digest,bytes=target.stat().st_size,image_id=inspection['Id']),ensure_ascii=False))
