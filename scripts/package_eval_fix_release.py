"""Package the measured 0.4.12 image; never replace a previously delivered archive."""
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.4.12-fix-20261026'
IMAGE = 'research-assistant:' + VERSION
CE = 'output/official-live-0412-ce-v4-20261026'
D = 'output/official-live-0412-d-source-v5-20261026'
AUDIT = ROOT/'output/release-0412'


def load(path):
    return json.loads((ROOT/path).read_text(encoding='utf-8'))


def docker(*args):
    return subprocess.check_output(['docker',*args],text=True,encoding='utf-8')


inspection = json.loads(docker('image','inspect',IMAGE))[0]
verification = load('output/release-0412/image-verification.json')
provenance = load(CE+'/provenance.json')
assert inspection['Id'] == verification['image_id']
ce_verification = load('output/release-0412/ce-image-verification.json')
assert ce_verification['image_id'] == provenance['image_id']
# C/E实测后仅修改D源码提示词、自检与基线保留流程。
allowed = {'src/research_agent/workflows/source_generation.py','src/research_agent/workflows/sample_validation.py',
           'src/research_agent/workflows/layered_d_runner.py'}
assert ce_verification['source_hashes'].keys() == verification['source_hashes'].keys()
assert all(value == verification['source_hashes'][path] for path,value in ce_verification['source_hashes'].items() if path not in allowed)
assert provenance['official_api'] and not provenance['oom_killed']
rows = load(CE+'/summary.json')['rows']
assert {r['case_id'] for r in rows} == {'C-01','C-03','E-01','E-02'}
assert all(r['format_and_files_passed'] and r['local_session_trace_received'] and r['seconds'] < 180 for r in rows)
for row in rows:
    body = load(CE+'/cases/'+row['label']+'/response.json')
    saved = load(CE+'/agent-output/data/workspace-live-audit/'+row['case_id']+'.json')
    assert set(body) == {'case_id','path','markdown'} and body == saved
    assert (ROOT/CE/'agent-output'/body['path']).read_text(encoding='utf-8') == body['markdown']
sources = load(D+'/summary.json')
assert len(sources) == 3 and all(r['status']=='passed_public_sample' and r['source_matches_current_response'] for r in sources)
assert all(r['image_id'] == inspection['Id'] for r in sources)
workers = load(D+'/official-worker/validated-summary.json')
assert all(r['returncode']==0 and r['invariants'] for r in workers)
tests = []
for name in ('core-release.xml','sdk-release.xml'):
    root = ET.parse(AUDIT/name).getroot()
    suites = root.findall('testsuite')
    assert all(int(s.get('errors','0'))==0 and int(s.get('failures','0'))==0 for s in suites)
    tests += [dict(file=name,tests=sum(int(s.get('tests','0')) for s in suites),
                   skipped=sum(int(s.get('skipped','0')) for s in suites))]
secret = next(line.split('=',1)[1] for line in (ROOT/'.env.local').read_text(encoding='utf-8-sig').splitlines() if line.startswith('PLATFORM_API_KEY='))
assert secret
for directory in (ROOT/CE, ROOT/D, AUDIT):
    assert not any(secret.encode() in p.read_bytes() for p in directory.rglob('*') if p.is_file())
target = ROOT/'team_share/image'/f'research-assistant-{VERSION}.tar'
partial = target.with_suffix('.tar.partial')
assert not target.exists() and not partial.exists()
docker('save','-o',str(partial),IMAGE)
with partial.open('rb') as stream:
    digest = hashlib.file_digest(stream,'sha256').hexdigest()
docker('load','-i',str(partial))
assert json.loads(docker('image','inspect',IMAGE))[0]['Id'] == inspection['Id']
partial.rename(target)
target.with_suffix('.tar.sha256').write_text(digest+'  '+target.name+'\n',encoding='ascii')
release = ROOT/'team_share/releases'/VERSION
release.mkdir(parents=True,exist_ok=False)
manifest = dict(version=VERSION,image=IMAGE,image_id=inspection['Id'],tar=target.name,
    bytes=target.stat().st_size,sha256=digest,regressions=tests,ce_provenance=provenance,ce_cases=rows,
    d_source_cases=sources,public_worker_checks=workers,material_comparison=load('output/release-0412/material-comparison.json'),
    official_sdk_unchanged=True,official_hidden_questions=False,platform_admission_verified=False,
    limitations=['C/E格式和文件通过不代表事实或内容满分；语义自审意见保留在每题证据中。',
                 'D已验证公开输入及官方客户端调用流程；平台正式来源签发及隐藏题需重新评测。',
                 'D类候选生成失败或时间不足时使用本轮已验证真实基线，可能只获部分分。'])
(release/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
(release/'README.md').write_text(f'''# {VERSION}

镜像 `{IMAGE}`，Linux/AMD64。导出tar后已docker load回读确认镜像ID。

- 文件：`{target.name}`
- 字节：{target.stat().st_size}
- SHA-256：`{digest}`
- 镜像ID：`{inspection['Id']}`

C/E直接从授权连续原文生成模型正文，启用JSON模式，兼容内部引文空格；独立校验正式三字段、真实引用和文件一致性。可选语义审查失败保留格式合格完整稿及未解决意见，不声称内容满分。

D为正式入口保留时间；三题先生成本轮真实基线，预算允许时升级候选。升级失败保留先前已验证原样源码和对应模型完整回复，正式指标/图表仍仅使用平台客户端结果。

实测记录见manifest。公开自检和本地Unix RPC夹具不代表平台正式来源凭证验收；需要在平台重新评测。新下载local材料与旧包运行代码/三题协议相同，差异是说明文档和整包清单。
''',encoding='utf-8')
print(json.dumps(dict(tar=str(target),sha256=digest,bytes=target.stat().st_size,image_id=inspection['Id']),ensure_ascii=False))
