"""Archive the offline D-02 mechanism diagnosis; never package or submit an Agent."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / '协同开发/research-agent-d-contract-20261106'
ROUND = REPO / 'team_share/rounds/2026-11-06-d30'
SOURCE = ROOT / '.session-audit/d02-rca-20261107'
DEST = ROUND / 'control/2026-11-07-d02-quality'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


parser = argparse.ArgumentParser()
parser.add_argument('--backup', action='store_true')
args = parser.parse_args()
production_diff = subprocess.check_output(['git', '-C', str(REPO), 'diff', 'HEAD', '--name-only', '--',
    'src', 'agent.py', 'Dockerfile', '.dockerignore', 'requirements.txt', 'pyproject.toml'], text=True)
assert not production_diff.strip(), production_diff
prior_hashes = json.loads((ROUND / 'archive-sha256.json').read_text(encoding='utf-8'))
for relative, row in prior_hashes.items():
    assert digest(ROUND / relative) == row['sha256'], relative
DEST.mkdir(parents=True, exist_ok=True)
for source in SOURCE.rglob('*'):
    if not source.is_file() or '__pycache__' in source.parts:
        continue
    target = DEST / source.relative_to(SOURCE)
    assert target.resolve().is_relative_to(DEST.resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    assert digest(source) == digest(target)
shutil.copyfile(Path(__file__), DEST / Path(__file__).name)
save(DEST / 'history-loss-update.json', {
    'date_china': '2026-11-07', 'origin': 'user statement and read-only repository audit',
    'original_experiment_work_directory': 'deleted by historical cleanup; unavailable',
    'formal_D_questions_public': False,
    'online_exact_failure_cause': 'unrecoverable from current evidence; no fabricated attribution',
    'request_deleted_logs_again': False,
    'official_80p9_D02_score': 31.055555555555554,
    'official_80p9_source_commit': None,
    'fresh_teammate_ref': '046324bae9425aa0a1aeff49b9d8128831d18479',
    'fresh_teammate_ref_is_proven_80p9': False,
    'source_inventory_C_tar_count': 99, 'source_inventory_D_tar_count': 22,
    'matching_expected_size_tar_found': False,
    'runtime_edits': 0, 'target_model_calls': 0, 'official_score_delta': None,
    'next_single_factor': 'D-02 revision promotion requires valid comparison and preserves every verified original pass',
})
readme_path = ROUND / 'README.md'
text = readme_path.read_text(encoding='utf-8')
note = ('\n## 2026-11-07 D-02质量诊断补充\n\n'
        '原始实验目录已清理、正式题不公开，旧轮次具体首错仍未知。'
        '新增control/2026-11-07-d02-quality保存旧新三版候选选择机制对照、固定合成输入、全部中间失败及源码指纹。'
        '已复现“冲突测试剔除成空集后仍采用退化修订”的组合风险；不等于复现官方题。'
        '后续只计划D-02候选采用门槛这一因素；80.9源码身份尚缺，运行源码/依赖/镜像未改，未提交新评测。\n')
if '## 2026-11-07 D-02质量诊断补充' not in text:
    readme_path.write_text(text + note, encoding='utf-8')
for relative, row in prior_hashes.items():
    if relative.startswith('raw/'):
        assert digest(ROUND / relative) == row['sha256'], relative
save(ROUND / 'archive-sha256.json', {
    p.relative_to(ROUND).as_posix(): {'sha256': digest(p), 'bytes': p.stat().st_size}
    for p in sorted(ROUND.rglob('*')) if p.is_file() and p.name != 'archive-sha256.json'
})
result = {'runtime_edits': 0, 'target_model_calls': 0,
          'historical_raw_unchanged': True, 'archive_folder': str(DEST)}
if args.backup:
    backup_root = Path('D:/Agent挑战赛').resolve()
    backup = backup_root / '2026-11-07-d02-quality' / ('evidence-' + uuid.uuid4().hex[:8])
    assert backup.resolve().is_relative_to(backup_root)
    backup.mkdir(parents=True, exist_ok=False)
    manifest = []
    files = [(p, Path('control') / p.relative_to(DEST)) for p in DEST.rglob('*') if p.is_file()]
    files += [(ROUND / name, Path(name)) for name in ('README.md', 'archive-sha256.json')]
    for source, relative in files:
        target = backup / relative
        assert target.resolve().is_relative_to(backup.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        expected = digest(source)
        assert digest(target) == expected
        manifest.append({'path': relative.as_posix(), 'sha256': expected, 'bytes': target.stat().st_size})
    save(backup / 'backup-manifest.json', manifest)
    result.update(backup=str(backup), files_verified=len(manifest))
    save(ROOT / '.session-audit/d02-quality-backup-20261107.json', result)
print(json.dumps(result, ensure_ascii=False))
