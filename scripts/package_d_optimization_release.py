"""校验D优化证据并导出新镜像；保留所有旧版本及失败运行记录。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit', default='output/d-optimization-0414/final-image-v4')
    parser.add_argument('--allow-baseline', action='store_true', help='Mark and package a partial candidate validation explicitly')
    args = parser.parse_args()
    version = '0.4.14-d-20261026'
    image = 'research-assistant:' + version
    audit = ROOT / args.audit
    parent = audit.parent
    load = lambda path: json.loads(path.read_text(encoding='utf-8'))
    inspection = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], text=True))[0]
    verification = load(parent / 'release-image-verification.json')
    assert verification['image_id'] == inspection['Id']
    assert verification['source_matches'] and verification['official_sdk_unchanged']
    protected = load(parent / 'protected-contract-verification.json')
    assert protected['protected_files_unchanged']
    baseline = load(ROOT / 'team_share/releases/0.4.13-c-20261026/manifest.json')
    baseline_tar = ROOT / 'team_share/image/research-assistant-0.4.13-c-20261026.tar'
    with baseline_tar.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == baseline['sha256'], '0.4.13 archive changed'
    tests = []
    for name in ('release-core-regressions.xml', 'release-sdk-regressions.xml'):
        suites = ET.parse(parent / name).getroot().findall('testsuite')
        assert suites and all(int(s.get('errors', '0')) == 0 and int(s.get('failures', '0')) == 0 for s in suites)
        tests.append({'file': name, 'tests': sum(int(s.get('tests', '0')) for s in suites),
                      'skipped': sum(int(s.get('skipped', '0')) for s in suites)})
    source_rows = load(audit / 'summary.json')
    assert {r['case_id'] for r in source_rows} == {'D-01', 'D-02', 'D-05'}
    for row in source_rows:
        assert row['image_id'] == inspection['Id'] and row['source_matches_current_response']
        assert row['status'] == 'passed_public_sample'
        report = row['public_selftest']
        chosen = next(r for r in report['attempts'] if r['attempt'] == report['selected_attempt'])
        row['candidate_present'] = chosen['candidate_present']
        assert row['candidate_present'] or args.allow_baseline, 'Do not present a baseline-only result as a completed candidate validation'
    differential = load(audit / 'differential/summary.json')
    assert differential['rows'] and all(row['passed'] for row in differential['rows'])
    assert {row['case_id'] for row in differential['rows']} == {'D-01', 'D-02', 'D-05'}
    secret = next(line.split('=', 1)[1] for line in (ROOT / '.env.local').read_text(encoding='utf-8-sig').splitlines()
                  if line.startswith('PLATFORM_API_KEY='))
    for path in parent.rglob('*'):
        if path.is_file() and path.suffix in {'.json', '.py', '.xml', '.log', '.txt'}:
            assert secret.encode() not in path.read_bytes(), 'Credential found in audit artifacts'
    release = ROOT / 'team_share/releases' / version
    target = ROOT / 'team_share/image' / ('research-assistant-' + version + '.tar')
    partial = target.with_suffix('.tar.partial')
    assert not release.exists() and not target.exists() and not partial.exists(), 'Never overwrite a release'
    subprocess.run(['docker', 'save', '-o', str(partial), image], check=True)
    with partial.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    subprocess.run(['docker', 'load', '-i', str(partial)], check=True)
    loaded = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], text=True))[0]
    assert loaded['Id'] == inspection['Id']
    partial.rename(target)
    target.with_suffix('.tar.sha256').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
    release.mkdir(parents=True)
    counts = {key: sum(r['case_id'] == key for r in differential['rows']) for key in ('D-01', 'D-02', 'D-05')}
    baseline_cases = [row['case_id'] for row in source_rows if not row['candidate_present']]
    manifest = {'version': version, 'image': image, 'image_id': inspection['Id'],
                'tar': str(target.relative_to(ROOT)), 'bytes': target.stat().st_size, 'sha256': digest,
                'docker_load_verified': True, 'regressions': tests, 'public_and_synthetic_checks': counts,
                'real_model_cases': source_rows, 'official_score': None, 'official_sdk_unchanged': True,
                'solve_contract_unchanged': True, 'protected_file_count': protected['protected_file_count'],
                'baseline_0_4_13_archive_preserved': True,
                'evidence_directory': str(audit.relative_to(ROOT)),
                'baseline_only_cases': baseline_cases,
                'limitations': ['公开及合成测试不代表正式隐藏金标、来源凭证验收或新的官方成绩。',
                                '代码来自每题当前模型响应；线上模型质量、延迟仍会影响结果。',
                                'D-02候选生成/校验失败时回退本轮已验证基线，可能只有部分分。',
                                '全部中间失败轮次保留在output/d-optimization-0414，成功轮次不能代表总成功率。']}
    (release / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (release / 'README.md').write_text(
        f'# {version}\n\n基于0.4.13的D类优化候选版本。`/solve`入参、最终输出Schema、原生SDK及其他题型实现保持原样。\n\n'
        f'镜像：`{image}`，Linux/AMD64，非root。\n\n'
        f'- 镜像包：`{target.name}`\n- SHA-256：`{digest}`\n- 字节：{target.stat().st_size}\n'
        f'- 镜像ID：`{inspection["Id"]}`\n\n'
        'D-01/D-05优先生成完整候选；D-02先保留验证后的真实基线，再争取候选。'
        '自检增加公式、分组统计和调度边界检查，修复使用当前模型重新生成的完整源码。\n\n'
        f'最终真实模型三题均通过公开自检；仅基线题目：{baseline_cases}（这些题目没有验证完整候选，不承诺保持旧版效果分）。'
        f'Python3.9、无网络、1CPU/1GiB下公开及合成差分检查：{counts}；每项candidate_present另存于详细记录。'
        f'回归共{sum(t["tests"] - t["skipped"] for t in tests)}项通过，镜像已导出并重新载入核对。\n\n'
        '本轮未上传平台，未取得新的官方成绩。线上模型仍可能失败或超时；历史失败记录保留，不能将本地结果当作满分承诺。'
        '详细分析见docs/2026-10-26-D类优化与公开算法验证.md及manifest。\n', encoding='utf-8')
    print(json.dumps({'tar': str(target), 'sha256': digest, 'image_id': inspection['Id'], 'checks': counts}))


if __name__ == '__main__':
    main()
