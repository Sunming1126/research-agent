"""Archive and installed-byte verification, invoked by the native coordinator."""
from pathlib import Path
import json
import zipfile

import test_agent as T
from harness_bridge import write_json
from prepare_hybrid_v8 import DELIVERY,IMAGE,digest

def validate(main_run,repeat_run):
    manifest=json.loads((DELIVERY/'delivery-manifest.json').read_text(encoding='utf-8'))
    for item in manifest['files']:
        path=DELIVERY/item['file']
        assert path.stat().st_size==item['bytes'] and digest(path)==item['sha256']
    source=DELIVERY/'research-agent-0.4.2-hybrid-v8-build-source.zip'
    with zipfile.ZipFile(source) as z:
        assert z.testzip() is None
        assert len(z.namelist())==42
        assert not any(name.startswith('/') or '..' in Path(name).parts or '.env' in Path(name).parts for name in z.namelist())
    installed=json.loads((T.HERE/'runs'/main_run/'installed-source.json').read_text(encoding='utf-8'))
    snapshot=DELIVERY/'build-source'
    inherited={name:sha for name,sha in installed['files'].items() if not (snapshot/name).is_file()}
    previous=json.loads((T.HERE/'runs/comparison-20261023-v041-source/installed-source.json').read_text(encoding='utf-8'))
    assert set(inherited)=={'src/.___main__.py','src/._my_agent.py','src/Dockerfile','src/__main__.py','src/my_agent.py'}
    assert all(previous['files'].get(name)==sha for name,sha in inherited.items())
    own={name:sha for name,sha in installed['files'].items() if name not in inherited}
    assert len(own)==39 and all(digest(snapshot/name)==sha for name,sha in own.items())
    T.docker('load','-i',DELIVERY/'research-assistant-0.4.2-hybrid-v8.tar',timeout=120)
    info=json.loads(T.docker('image','inspect',IMAGE))[0]
    assert info['Id']==manifest['image_id']
    T.IMAGE=info['Id']
    code="import hashlib,pathlib,json;print(json.dumps({'README.md':hashlib.sha256(pathlib.Path('/app/README.md').read_bytes()).hexdigest()}))"
    actual=json.loads(T.docker('run','--rm','--pull=never','--network','none','--read-only','--entrypoint','python',T.IMAGE,'-c',code))
    assert actual['README.md']==digest(snapshot/'README.md')
    with T.target(DELIVERY/'reimport-health') as (base,output,name):
        status,body=T.http(base+'/health');assert status==200
    results=[]
    for run in [main_run,repeat_run]:
        root=T.HERE/'runs'/run
        summary=json.loads((root/'summary.json').read_text(encoding='utf-8'))
        checks=json.loads((root/'checks.json').read_text(encoding='utf-8'))
        assert summary['image_id']==manifest['image_id']
        assert not summary['mandatory_failures'] and not summary['quality_failures']
        assert all(row['status']=='PASS' or (row['status']=='BLOCKED' and row['group']=='official-platform') for row in checks)
        results.append(dict(run=run,counts=summary['counts']))
    audit=dict(status='PASS',archive_checksums_verified=True,source_zip_crc_valid=True,
        owned_installed_source_byte_matches=len(own),unchanged_inherited_base_example_files=inherited,
        frozen_readme_matches=True,reimport_same_image_id=True,fresh_readonly_health_http=200,
        native_main_run=main_run,native_repeat_run=repeat_run,local_results=results,
        official_gateway_admission_verified=False,official_score=None,uploaded_to_contest=False)
    write_json(DELIVERY/'candidate-audit.json',audit)
    manifest.update(status='LOCAL_PUBLIC_ACCEPTANCE_PASSED_READY_FOR_FIRST_PLATFORM_VALIDATION',
        ready_for_recommended_contest_submission=True,real_model_acceptance_complete=True,
        local_results=results,official_gateway_admission_verified=False)
    write_json(DELIVERY/'delivery-manifest.json',manifest)
    print(json.dumps(dict(archive_validation='PASS',image_id=info['Id'],local_results=results,
        official_score=None,uploaded_to_contest=False),ensure_ascii=False),flush=True)

