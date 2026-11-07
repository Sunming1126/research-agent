"""Seal status/evidence overlay; preserve the original image and old D backup."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import uuid

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / '协同开发/research-agent-d-contract-20261106'
ROUND = REPO / 'team_share/rounds/2026-11-06-d30'
CONTROL = ROUND / 'control/2026-11-07-baseline-preservation'
DELIVERY = ROOT / '交付物/2026-11-06-d30-2e6f51a643ac'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


parser = argparse.ArgumentParser()
parser.add_argument('--backup', action='store_true')
args = parser.parse_args()
for relative in ('.session-audit/baseline-preservation-guard-checks-20261107.json',
                 '.session-audit/dispatch_score_native_20261102.py',
                 '镜像测试/score_compat_20261102.py',
                 '.session-audit/seal_baseline_preservation_20261107.py'):
    shutil.copyfile(ROOT / relative, CONTROL / Path(relative).name)
save(DELIVERY / '当前采用状态-20261107.json', {
    'eligible': False, 'decision': 'rejected', 'reason': 'D-02=0; E-04 displayed 5.6; 80.9 source identity unbound',
    'source_commit': '2e6f51a643acf66920612f83555a1c3a59d9d8b8',
    'original_tar_retained': True,
    'report': str(CONTROL / '诊断与单模块验证矩阵.md'),
})
save(ROUND / 'archive-sha256.json', {
    p.relative_to(ROUND).as_posix(): {'sha256': digest(p), 'bytes': p.stat().st_size}
    for p in sorted(ROUND.rglob('*')) if p.is_file() and p.name != 'archive-sha256.json'
})
save(DELIVERY / '交付材料校验.json', {
    p.name: {'sha256': digest(p), 'bytes': p.stat().st_size}
    for p in DELIVERY.iterdir() if p.is_file() and p.name != '交付材料校验.json'
})
result = {'archive_entries': len(json.loads((ROUND / 'archive-sha256.json').read_text(encoding='utf-8'))),
          'candidate_adopted': False, 'target_model_calls': 0, 'runtime_changes': 0}
if args.backup:
    backup_root = Path('D:/Agent挑战赛').resolve()
    target_root = backup_root / '2026-11-07-baseline-preservation' / ('overlay-' + uuid.uuid4().hex[:8])
    assert target_root.resolve().is_relative_to(backup_root)
    target_root.mkdir(parents=True, exist_ok=False)
    sources = [(p, Path('round-control') / p.relative_to(CONTROL)) for p in CONTROL.rglob('*') if p.is_file()]
    sources += [(ROUND / p, Path('round') / p) for p in ('README.md', 'archive-sha256.json')]
    sources += [(DELIVERY / p, Path('submission') / p)
                for p in ('manifest.json', '提交说明.md', '交付材料校验.json', '当前采用状态-20261107.json')]
    manifest = []
    for source, relative in sources:
        target = target_root / relative
        assert target.resolve().is_relative_to(target_root.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        expected = digest(source)
        assert digest(target) == expected
        manifest.append({'path': relative.as_posix(), 'sha256': expected, 'bytes': target.stat().st_size})
    save(target_root / 'backup-manifest.json', manifest)
    result.update(backup=str(target_root), backup_files_verified=len(manifest),
                  previous_backup_preserved='D:/Agent挑战赛/2026-11-06-d30-release/complete-d539dca9')
    save(ROOT / '.session-audit/baseline-preservation-backup-20261107.json', result)
print(json.dumps(result, ensure_ascii=False))
