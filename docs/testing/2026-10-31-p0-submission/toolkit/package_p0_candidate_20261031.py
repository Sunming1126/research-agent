"""Assemble a complete OCI archive from pinned layers and exact tested sources.

This does not run Docker, alter the SDK, or claim container acceptance.
"""
import ast
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / '协同开发/research-agent-stability-20261027'
REVISION = 'a5fa363449b71680fa9044ad80cc2aaa64c803dd'
VERSION = '0.4.20-p0-ce-a5fa363-20261031'
IMAGE = 'research-assistant:' + VERSION
OUT = ROOT / '提交材料/科研助手-P0-CE-a5fa363-20261031'
BASE = ROOT / '提交材料/科研助手-0.4.20-C修复-20261030/research-assistant-0.4.20-c-recovery-20261030.tar'
BASE_SHA = 'd58521992b981d03f3b5833021e4099a98a71e1d1a37cca5c0f421748aafbc0c'


def digest(value):
    return hashlib.sha256(value).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def add(tar, name, value, uid=0, gid=0, directory=False):
    info = tarfile.TarInfo(name)
    info.uid = uid
    info.gid = gid
    info.mode = 0o755 if directory else 0o644
    info.mtime = 0
    if directory:
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
    else:
        info.size = len(value)
        tar.addfile(info, io.BytesIO(value))


def main():
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip() == REVISION
    assert sha(BASE) == BASE_SHA
    OUT.mkdir(parents=True, exist_ok=False)
    support = OUT / '队内交接'
    support.mkdir()
    meta = json.loads((ROOT / '.session-audit/p0-ce-real-study-20261031.json').read_text(encoding='utf-8'))
    study = Path(meta['directory'])
    protocol = json.loads((study / 'protocol.json').read_text(encoding='utf-8'))
    frozen = Path(protocol['source']['candidate']['path'])
    sources = {}
    excluded = []
    for name, expected in protocol['source']['candidate']['files'].items():
        value = (frozen / name).read_bytes()
        assert digest(value) == expected
        tracked = subprocess.check_output(['git', 'show', REVISION + ':' + name], cwd=REPO)
        assert tracked.replace(b'\r\n', b'\n') == value.replace(b'\r\n', b'\n'), name
        if '/d_algorithms/' in name:
            excluded.append(name)
            continue
        ast.parse(value, feature_version=(3, 11))
        sources[name] = value
    sources['requirements.txt'] = (frozen / 'requirements.txt').read_bytes()
    sources['README.md'] = subprocess.check_output(['git', 'show', REVISION + ':README.md'], cwd=REPO)
    assert len(sources) == 43 and len(excluded) == 4
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    destination = OUT / ('research-assistant-' + VERSION + '.tar')
    with tarfile.open(BASE) as base:
        index = json.load(base.extractfile('index.json'))
        assert len(index['manifests']) == 1
        original_manifest = json.load(base.extractfile('blobs/sha256/' + index['manifests'][0]['digest'].split(':')[1]))
        original_config = base.extractfile('blobs/sha256/' + original_manifest['config']['digest'].split(':')[1]).read()
        config = json.loads(original_config)
        assert config['os'] == 'linux' and config['architecture'] == 'amd64'
        assert config['config']['Cmd'] == ['python', 'agent.py'] and config['config']['User'] == 'agent'
        assert len(original_manifest['layers']) == len(config['rootfs']['diff_ids'])
        uid = gid = None
        base_requirements = None
        passwd = None
        for descriptor, diffid in zip(original_manifest['layers'], config['rootfs']['diff_ids']):
            value = base.extractfile('blobs/sha256/' + descriptor['digest'].split(':')[1]).read()
            assert len(value) == descriptor['size'] and 'sha256:' + digest(value) == descriptor['digest']
            raw = gzip.decompress(value) if value[:2] == b'\x1f\x8b' else value
            assert 'sha256:' + digest(raw) == diffid
            with tarfile.open(fileobj=io.BytesIO(raw)) as layer:
                for member in layer:
                    name = member.name.lstrip('./')
                    if name == 'app/agent.py' and member.isfile():
                        uid, gid = member.uid, member.gid
                    elif name == 'app/requirements.txt' and member.isfile():
                        base_requirements = layer.extractfile(member).read()
                    elif name == 'etc/passwd' and member.isfile():
                        passwd = layer.extractfile(member).read().decode()
        assert uid is not None and uid != 0 and gid is not None
        account = next(line.split(':') for line in passwd.splitlines() if line.startswith('agent:'))
        assert int(account[2]) == uid and int(account[3]) == gid
        assert base_requirements.replace(b'\r\n', b'\n') == sources['requirements.txt'].replace(b'\r\n', b'\n')
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT) as layer:
            directories = {'app'}
            for name in sources:
                directories.update(str(p) for p in (Path('app') / name).parents if str(p) != '.')
            for name in sorted(directories, key=lambda n: (len(Path(n).parts), n)):
                add(layer, name.replace('\\', '/'), b'', uid, gid, directory=True)
            add(layer, 'app/.wh..wh..opq', b'', uid, gid)
            for name, value in sorted(sources.items()):
                add(layer, 'app/' + name, value, uid, gid)
        raw_layer = buffer.getvalue()
        compressed = gzip.compress(raw_layer, mtime=0)
        diffid = 'sha256:' + digest(raw_layer)
        layer_hash = digest(compressed)
        env = dict(v.split('=', 1) for v in config['config']['Env'])
        assert not env.get('PLATFORM_API_KEY') and not env.get('TEAM_ID') and 'RESEARCH_WORKSPACE_TEAM' not in env
        env['AGENT_VERSION'] = VERSION
        config['config']['Env'] = [k + '=' + v for k, v in env.items()]
        config['config']['Labels'].update({
            'org.agenthack.version': VERSION, 'org.opencontainers.image.revision': REVISION,
            'org.opencontainers.image.created': now, 'org.research-agent.acceptance': 'P0-not-accepted',
            'org.research-agent.assembly': 'exact-source-OCI-overlay'})
        config['created'] = now
        config['rootfs']['diff_ids'].append(diffid)
        config.setdefault('history', []).append({'created': now, 'created_by': 'Exact tested P0 application OCI overlay',
                                                 'comment': 'No Docker build or runtime validation claimed'})
        config_bytes = encoded(config)
        config_hash = digest(config_bytes)
        layers = original_manifest['layers'] + [{'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip',
                                                'digest': 'sha256:' + layer_hash, 'size': len(compressed)}]
        image_manifest = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                          'config': {'mediaType': 'application/vnd.oci.image.config.v1+json',
                                     'digest': 'sha256:' + config_hash, 'size': len(config_bytes)}, 'layers': layers}
        manifest_bytes = encoded(image_manifest)
        manifest_hash = digest(manifest_bytes)
        new_index = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json', 'manifests': [{
            'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'digest': 'sha256:' + manifest_hash,
            'size': len(manifest_bytes), 'platform': {'architecture': 'amd64', 'os': 'linux'},
            'annotations': {'io.containerd.image.name': 'docker.io/library/' + IMAGE,
                            'org.opencontainers.image.ref.name': VERSION}}]}
        compatible = [{'Config': 'blobs/sha256/' + config_hash, 'RepoTags': [IMAGE],
                       'Layers': ['blobs/sha256/' + d['digest'].split(':')[1] for d in layers]}]
        with tarfile.open(destination, 'w', format=tarfile.PAX_FORMAT) as archive:
            for descriptor in original_manifest['layers']:
                name = 'blobs/sha256/' + descriptor['digest'].split(':')[1]
                add(archive, name, base.extractfile(name).read())
            for name, value in [('blobs/sha256/' + layer_hash, compressed), ('blobs/sha256/' + config_hash, config_bytes),
                                ('blobs/sha256/' + manifest_hash, manifest_bytes), ('index.json', encoded(new_index)),
                                ('oci-layout', encoded({'imageLayoutVersion': '1.0.0'})), ('manifest.json', encoded(compatible))]:
                add(archive, name, value)
    assert destination.stat().st_size < 2 * 1024 ** 3
    context = dict(sources)
    dockerfile = subprocess.check_output(['git', 'show', REVISION + ':Dockerfile'], cwd=REPO)
    context['Dockerfile'] = dockerfile.replace(b'0.4.20-c-recovery-20261030', VERSION.encode())
    context['.dockerignore'] = subprocess.check_output(['git', 'show', REVISION + ':.dockerignore'], cwd=REPO)
    source_zip = support / 'research-agent-a5fa363-exact-build-context.zip'
    with zipfile.ZipFile(source_zip, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in sorted(context.items()):
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
    full_source = ROOT / '镜像测试/交接/P0-CE-20261031/research-agent-a5fa363-source-tests.zip'
    assert sha(full_source) == '788daaab6bb3e1e90589a6a1838ba1d123f6d181ae60c23604fc663007356029'
    shutil.copy2(full_source, support / full_source.name)
    save(support / 'source-sha256.json', {name: digest(value) for name, value in sources.items()})
    save(support / 'build-context-sha256.json', {name: digest(value) for name, value in context.items()})
    for folder, filename in [('2026-10-31-p0-ce-real', 'P0真实模型重复验证与阶段决策.md'),
                             ('2026-10-31-p0-ce-real', '定向内容复核.md'),
                             ('2026-10-31-p0-ce-real', 'metrics.json'),
                             ('2026-10-31-p0-ce-closure', 'P0四项修复闭环.md')]:
        shutil.copy2(REPO / 'docs/testing' / folder / filename, support / filename)
    manifest = {'version': VERSION, 'source_revision': REVISION,
                'production_fix_commit': '91d15f80d720476c9aee1f96a6320b97ecc97dea', 'image': IMAGE,
                'image_id': 'sha256:' + config_hash, 'oci_manifest_digest': 'sha256:' + manifest_hash,
                'image_archive': destination.name, 'image_archive_bytes': destination.stat().st_size,
                'image_archive_sha256': sha(destination), 'platform': 'linux/amd64', 'port': 8080,
                'base_archive_sha256': BASE_SHA, 'base_image_id': 'sha256:' + digest(original_config),
                'assembly': 'Complete OCI archive: original pinned runtime/dependency layers plus exact tested source overlay',
                'docker_build_executed': False, 'docker_load_verified': False, 'container_health_verified': False,
                'container_solve_verified': False, 'P0_overall_accepted': False, 'official_score': None,
                'uploaded_to_contest': False, 'ready_for_recommended_contest_submission': False,
                'user_requested_candidate_package': True, 'official_runtime_entry_verified_statically': True,
                'image_python_files': sum(n.endswith('.py') for n in sources),
                'excluded_local_d_algorithm_fixtures': excluded,
                'source_archive': '队内交接/' + source_zip.name, 'source_archive_sha256': sha(source_zip),
                'actual_source_bytes_match_real_model_candidate': True, 'dependencies_changed': False,
                'historical_source_regression_passed': 439, 'real_model_paired_candidate_delivery': '12/12',
                'real_model_paired_baseline_delivery': '11/12', 'content_correctness_improvement_proven': False,
                'remaining_risks': ['FiD/DPR attribution in E-02', 'Linux startup unverified',
                                    'persistent idempotency and restart recovery unverified', 'D wall-clock variance unchanged']}
    save(OUT / 'delivery-manifest.json', manifest)
    (OUT / 'SHA256SUMS.txt').write_text(manifest['image_archive_sha256'] + '  ' + destination.name + '\n', encoding='ascii')
    print(json.dumps({k: manifest[k] for k in ('version', 'image_archive', 'image_archive_bytes', 'image_archive_sha256', 'image_id')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
