"""Independent archive readback, OCI graph/source/SDK/credential audit.

Static checks are deliberately not counted as Docker import or Linux execution.
"""
import ast
import email
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / '提交材料/科研助手-P0-CE-a5fa363-20261031'
BASE = ROOT / '提交材料/科研助手-0.4.20-C修复-20261030/research-assistant-0.4.20-c-recovery-20261030.tar'


def digest(value):
    return hashlib.sha256(value).hexdigest()


def interested(name):
    return name.startswith('app/') or ('/site-packages/' in name and ('/agenthack/' in name or name.endswith('.dist-info/METADATA')))


def collect(archive_path, secrets):
    effective = {}
    seen = set()
    with tarfile.open(archive_path) as outer:
        index = json.load(outer.extractfile('index.json'))
        assert len(index['manifests']) == 1
        descriptor = index['manifests'][0]
        manifest_bytes = outer.extractfile('blobs/sha256/' + descriptor['digest'].split(':')[1]).read()
        assert 'sha256:' + digest(manifest_bytes) == descriptor['digest'] and len(manifest_bytes) == descriptor['size']
        manifest = json.loads(manifest_bytes)
        config_bytes = outer.extractfile('blobs/sha256/' + manifest['config']['digest'].split(':')[1]).read()
        assert 'sha256:' + digest(config_bytes) == manifest['config']['digest']
        assert len(config_bytes) == manifest['config']['size']
        config = json.loads(config_bytes)
        assert not any(s.encode() in config_bytes for s in secrets if len(s) >= 8)
        docker_manifest = json.load(outer.extractfile('manifest.json'))
        assert len(docker_manifest) == 1 and docker_manifest[0]['Config'].endswith(digest(config_bytes))
        expected_layers = ['blobs/sha256/' + row['digest'].split(':')[1] for row in manifest['layers']]
        assert docker_manifest[0]['Layers'] == expected_layers
        assert len(config['rootfs']['diff_ids']) == len(expected_layers)
        for descriptor, diffid in zip(manifest['layers'], config['rootfs']['diff_ids']):
            data = outer.extractfile('blobs/sha256/' + descriptor['digest'].split(':')[1]).read()
            assert 'sha256:' + digest(data) == descriptor['digest'] and len(data) == descriptor['size']
            raw = gzip.decompress(data) if data[:2] == b'\x1f\x8b' else data
            assert 'sha256:' + digest(raw) == diffid
            assert not any(s.encode() in raw for s in secrets if len(s) >= 8), 'Credential found; no contents echoed'
            with tarfile.open(fileobj=io.BytesIO(raw)) as layer:
                for member in layer:
                    name = member.name.removeprefix('./').rstrip('/')
                    path = PurePosixPath(name)
                    assert not path.is_absolute() and '..' not in path.parts
                    if path.name == '.wh..wh..opq':
                        prefix = str(path.parent) + '/'
                        effective = {k: v for k, v in effective.items() if not k.startswith(prefix)}
                    elif path.name.startswith('.wh.'):
                        target = str(path.parent / path.name[4:])
                        effective = {k: v for k, v in effective.items() if k != target and not k.startswith(target + '/')}
                    elif interested(name):
                        if member.isfile():
                            effective[name] = layer.extractfile(member).read()
                        elif member.issym() or member.islnk():
                            if name.startswith('app/'):
                                raise AssertionError('Unexpected application symlink')
                    if name.startswith('app/'):
                        seen.add(name)
        return effective, config, manifest, docker_manifest, digest(config_bytes)


def main():
    m = json.loads((OUT / 'delivery-manifest.json').read_text(encoding='utf-8'))
    expected = json.loads((OUT / '队内交接/source-sha256.json').read_text(encoding='utf-8'))
    sys.path.insert(0, str(ROOT / '镜像测试'))
    from magic_api_bridge import MagicAPI
    config = MagicAPI()._config
    secrets = [config['api_key']] + [str(v) for k, v in config.get('headers', {}).items()
                                     if any(word in k.lower() for word in ('authorization', 'token', 'secret', 'key'))]
    base, _, base_manifest, _, _ = collect(BASE, secrets)
    current, config, manifest, docker_manifest, config_hash = collect(OUT / m['image_archive'], secrets)
    assert m['image_id'] == 'sha256:' + config_hash
    assert config['os'] == 'linux' and config['architecture'] == 'amd64'
    run = config['config']
    assert run['User'] == 'agent' and run['Cmd'] == ['python', 'agent.py'] and not run.get('Entrypoint')
    assert run['WorkingDir'] == '/app' and '8080/tcp' in run['ExposedPorts']
    env = dict(row.split('=', 1) for row in run['Env'])
    assert env['RESEARCH_WORKSPACE_ROOT'] == '/data' and env['RESEARCH_RESULT_ROOT'] == '/output'
    assert 'RESEARCH_WORKSPACE_TEAM' not in env and not env.get('TEAM_ID')
    assert all(not value for name, value in env.items() if any(key in name for key in ('API_KEY', 'AUTH_TOKEN')))
    assert docker_manifest[0]['RepoTags'] == [m['image']]
    app = {name[4:]: value for name, value in current.items() if name.startswith('app/')}
    assert set(app) == set(expected) and len(app) == 43
    assert not any('d_algorithms' in name or '.env' in PurePosixPath(name).parts for name in app)
    meta = json.loads((ROOT / '.session-audit/p0-ce-real-study-20261031.json').read_text(encoding='utf-8'))
    protocol = json.loads((Path(meta['directory']) / 'protocol.json').read_text(encoding='utf-8'))
    proof = protocol['source']['candidate']['files']
    python_count = 0
    for name, data in app.items():
        assert digest(data) == expected[name]
        if name.endswith('.py'):
            ast.parse(data, filename=name, feature_version=(3, 11))
            assert digest(data) == proof[name]
            python_count += 1
    assert python_count == 41
    entry = ast.parse(app['agent.py'])
    assert any(isinstance(node, ast.ImportFrom) and node.module == 'agenthack' and
               any(alias.name == 'AgentRuntime' for alias in node.names) for node in ast.walk(entry))
    assert any(isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and
               isinstance(node.value.func, ast.Name) and node.value.func.id == 'AgentRuntime' for node in ast.walk(entry))
    assert 'runtime.run()' in app['agent.py'].decode() and 'uvicorn.run' not in app['agent.py'].decode()
    sdk = {k: digest(v) for k, v in current.items() if '/agenthack/' in k}
    base_sdk = {k: digest(v) for k, v in base.items() if '/agenthack/' in k}
    assert sdk and sdk == base_sdk
    installed = {}
    for name, data in current.items():
        if not name.endswith('.dist-info/METADATA'):
            continue
        metadata = email.message_from_bytes(data)
        package = re.sub(r'[-_.]+', '-', metadata['Name']).lower()
        installed[package] = metadata['Version']
    pins = dict(re.findall(r'^([A-Za-z0-9_.-]+)==([^\s#]+)', app['requirements.txt'].decode(), re.M))
    for name, version in pins.items():
        key = re.sub(r'[-_.]+', '-', name).lower()
        assert installed.get(key) == version, 'Pinned installed dependency mismatch: ' + name
    assert manifest['layers'][:-1] == base_manifest['layers']
    with zipfile.ZipFile(OUT / m['source_archive']) as z:
        assert z.testzip() is None
        for name in app:
            assert z.read(name) == app[name]
    for path in OUT.rglob('*'):
        if path.is_file() and path.suffix not in ('.tar', '.zip'):
            assert not any(s.encode() in path.read_bytes() for s in secrets if len(s) >= 8)
    report = {'audit': 'Independent static OCI/archive readback, not execution',
              'source_revision': m['source_revision'], 'image_id': m['image_id'], 'complete_oci_graph': True,
              'compressed_blob_hashes_and_uncompressed_diffids_verified': len(manifest['layers']),
              'application_files': len(app), 'python_3_11_syntax_checked': python_count,
              'exact_python_bytes_match_real_model_candidate': python_count,
              'no_stale_application_files': True, 'official_runtime_entry_retained': True,
              'official_sdk_files_unchanged': len(sdk), 'pinned_dependencies_verified_from_dist_metadata': len(pins),
              'non_root': True, 'runtime_and_dependency_layers_unchanged': True, 'source_zip_readback': True,
              'existing_credentials_compared_in_memory': True, 'credentials_logged': False,
              'docker_load_tested': False, 'linux_startup_tested': False, 'container_solve_tested': False,
              'real_model_calls_from_audit': 0, 'P0_overall_accepted': False}
    (OUT / '队内交接/independent-static-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
