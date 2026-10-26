"""验证最终实测对应镜像，再导出唯一 tar、回读、保存发布清单。"""
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.4.9-b-20261025'
IMAGE = 'research-assistant:' + VERSION


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, encoding='utf-8')


def load(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


inspection = json.loads(docker('image', 'inspect', IMAGE))[0]
verification = load('output/b-failure-matrix-049/image-verification.json')
assert verification['image_id'] == inspection['Id']
runs = ['official-live-049-b-faults-release-20261025', 'official-live-049-b-100papers-release-20261025']
rows = []
for run in runs:
    provenance = load(f'output/{run}/provenance.json')
    assert provenance['image_id'] == inspection['Id'] and not provenance['oom_killed']
    results = load(f'output/{run}/summary.json')['rows']
    assert all(row['format_and_files_passed'] and row['local_session_trace_received'] and row['llm_calls'] > 0
               and row['seconds'] < 180 for row in results)
    for row in results:
        row = dict(row, evidence_directory=f'output/{run}')
        answer = load(f"output/{run}/cases/{row['label']}/response.json")
        assert answer['papers']
        row['paper_count'] = len(answer['papers'])
        rows.append(row)
assert len(rows) == 4
test_totals = dict(passed=0, skipped=0)
for name in ('release-sdk.xml', 'release-core.xml'):
    suite = ET.parse(ROOT / 'output/b-failure-matrix-049' / name).getroot().find('testsuite')
    assert suite.get('failures') == '0' and suite.get('errors') == '0'
    test_totals['passed'] += int(suite.get('tests')) - int(suite.get('skipped'))
    test_totals['skipped'] += int(suite.get('skipped'))
target = ROOT / 'team_share/image' / f'research-assistant-{VERSION}.tar'
assert not target.exists(), 'Refuse to overwrite delivered tar'
partial = target.with_suffix('.tar.partial')
assert not partial.exists(), 'Unfinished export exists; inspect before retrying'
docker('save', '-o', str(partial), IMAGE)
with partial.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
docker('load', '-i', str(partial))
assert json.loads(docker('image', 'inspect', IMAGE))[0]['Id'] == inspection['Id']
partial.rename(target)
target.with_suffix('.tar.sha256').write_text(f'{digest}  {target.name}\n',encoding='ascii')
release = ROOT / 'team_share/releases' / VERSION
release.mkdir(parents=True, exist_ok=True)
manifest = dict(version=VERSION, image=IMAGE, image_id=inspection['Id'],
    tar=target.relative_to(ROOT).as_posix(),tar_bytes=target.stat().st_size,tar_sha256=digest,
    tar_readback_verified=True,official_sdk_unchanged=True,source_matches_local=True,
    credential_files_excluded=True,offline_tests=test_totals,live_rows=rows,
    official_model_api=True,official_hidden_questions=False,platform_accepted=False,
    source_eval='eval-20261024-fee4379f',online_exception_confirmed=False,
    scope='B only; C/D/E have not been retested or declared fixed')
(release/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
table = '\n'.join(f"| {row['case_id']} / {'100篇冷库' if '100papers' in row['evidence_directory'] else '5篇+故障文件'} | {row['http_status']} | {row['seconds']}s | {row['llm_calls']} | {row['paper_count']} |" for row in rows)
readme = f'''# {VERSION}：B 类修复交付

文件：`{target.name}`，{target.stat().st_size} 字节。

SHA-256：`{digest}`

镜像：`{IMAGE}`；ID：`{inspection['Id']}`。

已用 docker load 回读验证；SDK 与官方基础镜像相同，源码哈希一致，未包含密钥文件或固定队伍标识。

修复 B 目录混入文件/坏 PDF 中断、明确 PDF 范围、重复副本、损坏缓存、失败答卷落盘和大库初筛耗时。失败空答卷保留真实失败追踪与诊断；不能写入或派题不合法时仍报错。大库前页初筛后按候选和预算深入正文，不能称为全库全文精读。

## 最终镜像使用官方 API 实测

| 题号 / 本地场景 | HTTP | 耗时 | 模型调用 | 返回论文 |
| --- | --- | --- | --- | --- |
{table}

以上均非空答卷、字段和实际 PDF 文件名通过独立校验，HTTP 与 `/output/data/<RESEARCH_WORKSPACE_TEAM>/<case_id>.json` 一致，收到本地原生 SDK 追踪。运行条件为 1 CPU/1 GiB、只读材料/派题和运行时目录标识。{test_totals['passed']} 项不同相关回归通过、{test_totals['skipped']} 项条件跳过。

## 使用与边界

上传本目录指向的新 tar；旧 0.4.8 tar 保留作对照。平台注入 RESEARCH_WORKSPACE_TEAM 和模型/会话/追踪参数，不填写报名队名。没有替用户上传平台。

线上 `eval-20261024-fee4379f` 的具体异常仍待组委会返回；本地题为构造案例，以上通过不代表隐藏题已验收或保证得分。C/D/E 本次未重新修复验收。两个中间候选的 100 篇超时/失败记录已保留，未冒充成功结果。

详细规范核对、遗漏与原始测试位置见 `docs/2026-10-25-B类调用失败修复与规范核对.md`。
'''
(release/'README.md').write_text(readme,encoding='utf-8')
print(json.dumps(dict(tar=str(target),bytes=target.stat().st_size,sha256=digest,image_id=inspection['Id'],
    live_checks=len(rows),model_calls=sum(row['llm_calls'] for row in rows)),ensure_ascii=False))
