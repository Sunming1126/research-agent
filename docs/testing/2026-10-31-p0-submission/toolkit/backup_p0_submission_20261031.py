"""Back up this exact candidate submission folder; never replace differing files."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / '提交材料/科研助手-P0-CE-a5fa363-20261031'
DEST = Path(r'D:\Agent挑战赛\提交材料\科研助手-P0-CE-a5fa363-20261031')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


assert DEST.resolve().is_relative_to(Path(r'D:\Agent挑战赛\提交材料').resolve())
manifest = json.loads((SOURCE / 'delivery-manifest.json').read_text(encoding='utf-8'))
assert sha(SOURCE / manifest['image_archive']) == manifest['image_archive_sha256']
items = [(p, p.relative_to(SOURCE)) for p in SOURCE.rglob('*') if p.is_file()]
for name in ('package_p0_candidate_20261031.py', 'audit_p0_submission_20261031.py',
             'publish_p0_candidate_files_20261031.py', 'mark_p0_candidate_partial_release_20261031.py',
             'backup_p0_submission_20261031.py', 'p0-candidate-docker-preflight-20261031.json',
             'p0-candidate-publish-error-20261031.json', 'p0-candidate-asset-recovery-20261031.json',
             'github-p0-candidate-files-20261031.json'):
    items.append((ROOT / '.session-audit' / name, Path('备份审计') / name))
files = {}
for source, relative in items:
    assert source.name not in ('.env', '.env.local', 'workspace.json', 'cordis.patch.yml')
    target = DEST / relative
    expected = sha(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        assert sha(target) == expected, 'Existing backup differs; no overwrite'
    else:
        shutil.copy2(source, target)
    assert sha(target) == expected
    files[relative.as_posix()] = {'sha256': expected, 'bytes': source.stat().st_size}
record = {'directory': str(DEST), 'source_revision': manifest['source_revision'],
          'image_archive': manifest['image_archive'], 'image_archive_sha256': manifest['image_archive_sha256'],
          'verified_files': len(files), 'bytes': sum(r['bytes'] for r in files.values()),
          'linux_startup_verified': False, 'P0_overall_accepted': False,
          'github_tar_upload_complete': False, 'target_model_calls': 0, 'files': files}
data = json.dumps(record, ensure_ascii=False, indent=2) + '\n'
(DEST / '备份校验回执.json').write_text(data, encoding='utf-8')
(ROOT / '.session-audit/p0-candidate-submission-backup-20261031.json').write_text(data, encoding='utf-8')
print(json.dumps({k: v for k, v in record.items() if k != 'files'}, ensure_ascii=False))
