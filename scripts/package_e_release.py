"""Export E release only after matching-image API, file, trace and regression checks."""
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.4.11-e-20261026'
IMAGE = 'research-assistant:' + VERSION
RUN = 'output/official-live-0411-e-v8-20261026'


def load(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, encoding='utf-8')


inspection = json.loads(docker('image', 'inspect', IMAGE))[0]
verification = load('output/e-release-0411/image-verification.json')
provenance = load(RUN + '/provenance.json')
assert verification['image_id'] == provenance['image_id'] == inspection['Id']
assert provenance['official_api'] and not provenance['oom_killed']
rows = load(RUN + '/summary.json')['rows']
assert len(rows) == 2 and {r['case_id'] for r in rows} == {'E-01', 'E-02'}
for row in rows:
    assert row['format_and_files_passed'] and row['local_session_trace_received'] and row['seconds'] < 180
    assert row['llm_calls'] > 0 and row['report_audits']
    assert all((a['ok'] is True and a['issues'] == []) or
               (a['ok'] is False and a['mode'] == 'model_report_needs_review' and a['local_validation'])
               for a in row['report_audits'])
    body = load(RUN + '/cases/' + row['label'] + '/response.json')
    assert set(body) == {'case_id', 'path', 'markdown'} and body['case_id'] == row['case_id']
    result = load(RUN + '/agent-output/data/workspace-live-audit/' + row['case_id'] + '.json')
    assert result == body
    assert (ROOT / RUN / 'agent-output' / body['path']).read_text(encoding='utf-8') == body['markdown']

counts = dict(passed=0, skipped=0)
for name in ('core-final.xml', 'sdk-final.xml'):
    suites = ET.parse(ROOT / 'output/e-release-0411' / name).getroot().iter('testsuite')
    for suite in suites:
        assert int(suite.get('failures', 0)) == int(suite.get('errors', 0)) == 0
        skipped = int(suite.get('skipped', 0))
        counts['passed'] += int(suite.get('tests', 0)) - skipped
        counts['skipped'] += skipped
assert counts['passed'] >= 221
secret = next(line.split('=', 1)[1] for line in (ROOT / '.env.local').read_text(encoding='utf-8-sig').splitlines()
              if line.startswith('PLATFORM_API_KEY='))
assert not any(secret.encode() in p.read_bytes() for p in (ROOT / RUN).rglob('*') if p.is_file())

target = ROOT / 'team_share/image' / f'research-assistant-{VERSION}.tar'
partial = target.with_suffix('.tar.partial')
assert not target.exists() and not partial.exists(), 'Never overwrite a delivered tar'
docker('save', '-o', str(partial), IMAGE)
with partial.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
docker('load', '-i', str(partial))
assert json.loads(docker('image', 'inspect', IMAGE))[0]['Id'] == inspection['Id']
partial.rename(target)
target.with_suffix('.tar.sha256').write_text(f'{digest}  {target.name}\n', encoding='ascii')
release = ROOT / 'team_share/releases' / VERSION
release.mkdir(parents=True, exist_ok=True)
manifest = dict(version=VERSION, image=IMAGE, image_id=inspection['Id'], tar=target.relative_to(ROOT).as_posix(),
    tar_bytes=target.stat().st_size, tar_sha256=digest, tar_readback_verified=True,
    official_sdk_unchanged=True, source_matches_local=True, credentials_excluded=True,
    offline_tests=counts, official_api_http_rows=rows, provenance=provenance,
    manual_content_review=load('output/e-release-0411/manual-content-review.json'),
    inherited_release='0.4.10-cd-20261025', official_hidden_questions=False,
    platform_accepted=False, online_exception_confirmed=False,
    limitations=['本地自拟题/真实PDF并非平台隐藏题；模型自审不能替代专家复核。',
                 '本轮未重跑正式D会话；保留上一版B/C/D修复及已记录测试。'])
(release / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
(release / 'README.md').write_text(f'''# {VERSION}

交付：`{target.name}`，{target.stat().st_size} 字节。已 docker load 回读并确认镜像 ID。

SHA-256：`{digest}`

镜像 ID：`{inspection['Id']}`

保留 0.4.10 的 B/C/D 修复；E 增加阶段与传输时间预留、完整文本免表格网格重建、数学区间/组合引用准确定位、完整修订上下文。模型语义自审不再直接触发接口500：只有通过硬检查的完整模型稿才能交付，自审问题保留为model_report_needs_review，不伪造通过、不拼接模板。没有完整稿或硬检查失败仍报错。

官方模型 E 两题 HTTP 200，逐项检查三字段、JSON/Markdown 落盘及逐字一致、平台目录标识、篇幅、引用与真实 SDK 追踪。耗时分别 {rows[0]['seconds']} / {rows[1]['seconds']} 秒。相关回归 {counts['passed']} 通过、{counts['skipped']} 条件跳过。官方 SDK 未修改，未内置密钥或固定队伍名。

本地自拟题使用真实 PDF 和官方 API，不是线上隐藏题，也没有重新取得正式 D 实验会话。语义自审尚有疑虑的题数：{sum(any(a.get('ok') is False for a in r['report_audits']) for r in rows)}；详见manifest原始审查。正常交付不表示内容满分或事实均无误。详细原始过程见 `docs/2026-10-26-E类修复与官方材料核对.md`，实测在 `{RUN}`。

人工抽查仍发现论文描述超出当前证据支持等内容问题（manifest.manual_content_review）。此包修复接口交付链路，不代表E科学内容已全部合格，不承诺正式分数。
''', encoding='utf-8')
print(json.dumps(dict(tar=str(target), sha256=digest, bytes=target.stat().st_size,
                      image_id=inspection['Id'], tests=counts), ensure_ascii=False))
