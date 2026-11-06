"""Prepare a pinned submission image; offline checks only, no platform submit."""
import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import time
import uuid
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent.parent
REPO=ROOT/'协同开发/research-agent-d-contract-20261106'
REF='2e6f51a643acf66920612f83555a1c3a59d9d8b8'
BASE='dc9ef0f2c9d257d14536389cbf72fa0b3e89b9bd'
OLD=ROOT/'交付物/2026-11-05-be-dc9ef0f2c9d2'
MATERIALS=ROOT/'镜像测试/runs/academic-linux-20261105-11c45136/source/科研助手赛题材料/research-experiments-20261020-source-v1'

def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def run():
    closure=json.loads((REPO/'docs/testing/2026-11-06-d-contract/closure.json').read_text())
    assert closure['candidate_commit']==REF and closure['linux_full']['passed']==636
    assert all(closure['linux_full'][k]==0 for k in ('failure','error','skipped'))
    output=ROOT/'镜像测试/runs'/('d30-package-20261106-'+uuid.uuid4().hex[:8])
    output.mkdir()
    save(ROOT/'.session-audit/d30-package-latest-20261106.json',{'directory':str(output)})
    state={'source_commit':REF,'execution_origin':'Codex offline','target_model_calls':0,
           'official_evaluation_started':False,'steps':{},'complete':False}
    docker=Path(os.environ['LOCALAPPDATA'])/'Programs/DockerDesktop/resources/bin/docker.exe'
    image='research-assistant:d30-'+REF[:12]
    def step(name,args,timeout=60,check=True):
        began=time.monotonic()
        with (output/(name+'.stdout')).open('wb') as stdout,(output/(name+'.stderr')).open('wb') as stderr:
            result=subprocess.run([str(docker),*map(str,args)],stdout=stdout,stderr=stderr,timeout=timeout)
        state['steps'][name]={'returncode':result.returncode,'seconds':round(time.monotonic()-began,3)}
        save(output/'summary.json',state)
        if check and result.returncode:raise RuntimeError(name+' failed; see '+str(output))
        return (output/(name+'.stdout')).read_text(encoding='utf-8').strip()
    def export(ref,target,paths):
        raw=subprocess.check_output(['git','-c','filter.lfs.smudge=','-c','filter.lfs.process=',
                                     'archive',ref,*paths],cwd=REPO,timeout=90)
        target.mkdir()
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for member in archive:
                if not member.isfile():continue
                path=target/member.name
                assert path.resolve().is_relative_to(target.resolve())
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(archive.extractfile(member).read())
    assert step('engine',['version','--format','{{.Server.Os}} {{.Server.Arch}}'])=='linux amd64'
    old=json.loads((OLD/'manifest.json').read_text())
    base_id=step('base',['image','inspect','--format','{{.Id}}','agenthack/base:1.1.1'])
    assert base_id==old['base_image_id']
    source=output/'source'
    export(REF,source,['src','agent.py','Dockerfile','.dockerignore','requirements.txt','README.md'])
    step('build',['build','--pull=false','--platform','linux/amd64',
                  '--label','org.opencontainers.image.revision='+REF,
                  '--label','org.opencontainers.image.version=2026-11-06-d30-'+REF[:12],'-t',image,source],900)
    info=json.loads(step('structure',['image','inspect',image]))[0]
    assert info['Os']=='linux' and info['Architecture']=='amd64' and info['Config']['User']=='agent'
    assert info['Config']['Cmd']==['python','agent.py'] and '8080/tcp' in info['Config']['ExposedPorts']
    assert info['Config']['Labels']['org.opencontainers.image.revision']==REF
    state['image']=image;state['image_id']=info['Id']
    base_info=json.loads(step('base-structure',['image','inspect',base_id]))[0]
    layers=base_info['RootFS']['Layers'];assert info['RootFS']['Layers'][:len(layers)]==layers
    scan=("import hashlib,json,platform,agenthack;from pathlib import Path;from importlib import metadata;"
          "p=Path('/app');sdk=Path(agenthack.__file__);print(json.dumps({'files':{f.relative_to(p).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in [p/'agent.py',*sorted((p/'src').rglob('*.py'))] if f.is_file()},"
          "'requirements_sha256':hashlib.sha256((p/'requirements.txt').read_bytes()).hexdigest() if (p/'requirements.txt').is_file() else None,"
          "'python':platform.python_version(),'packages':{d.metadata['Name']:d.version for d in metadata.distributions()},"
          "'sdk_module':str(sdk),'sdk_sha256':hashlib.sha256(sdk.read_bytes()).hexdigest()}))")
    actual=json.loads(step('image-content',['run','--rm','--network','none','--entrypoint','python',image,'-c',scan]))
    inherited=json.loads(step('base-content',['run','--rm','--network','none','--entrypoint','python',base_id,'-c',scan]))
    rules=[r.strip() for r in (source/'.dockerignore').read_text().splitlines() if r.strip() and not r.lstrip().startswith('#')]
    selected={};excluded=[]
    for path in [source/'agent.py',*sorted((source/'src').rglob('*.py'))]:
        name=path.relative_to(source).as_posix();included=True
        for rule in rules:
            negate=rule.startswith('!');pattern=rule[1:] if negate else rule
            if pattern.endswith('/'):pattern+='**'
            if fnmatch.fnmatchcase(name,pattern):included=negate
        if included:selected[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        else:excluded.append(name)
    assert len(selected)==59 and sorted(excluded)==sorted(old['excluded_practice_files'])
    assert actual['files']=={**inherited['files'],**selected}
    assert actual['requirements_sha256']==hashlib.sha256((source/'requirements.txt').read_bytes()).hexdigest()
    assert actual['sdk_sha256']==inherited['sdk_sha256']
    previous=json.loads((OLD/'environment.json').read_text())
    assert actual['python']==previous['python'] and actual['packages']==previous['packages']
    changed=[p for p,d in actual['files'].items() if previous['files'].get(p)!=d]
    assert changed==['src/research_agent/workflows/layered_d_runner.py'],changed
    save(output/'image-source-proof.json',{'actual':actual,'base_image_id':base_id,'excluded':excluded,'changed_from_BE':changed})
    # Confirm actual imports in the submitted image come from /app, not a test overlay.
    origin="import json,research_agent.workflows.layered_d_runner as d;assert d.__file__.startswith('/app/src/');print(json.dumps({'d_module':d.__file__}))"
    step('module-origin',['run','--rm','--network','none','--entrypoint','python',image,'-c',origin])
    health=output/'health.py'
    health.write_text("import time,json\nfrom urllib.request import urlopen\nfor _ in range(30):\n    try:\n        with urlopen('http://127.0.0.1:8080/health',timeout=1) as r: assert r.status==200; break\n    except OSError: time.sleep(1)\nelse: raise SystemExit('health unavailable')\nrows=[]\nfor i in range(10):\n    with urlopen('http://127.0.0.1:8080/health',timeout=2) as r:\n        assert r.status==200\n        rows.append(json.loads(r.read(4096)))\nprint(json.dumps({'count':len(rows),'responses':rows}))\n",encoding='utf-8')
    name='anic-d30-health-'+output.name.rsplit('-',1)[-1]
    container=step('health-start',['run','-d','--name',name,'--network','none','--cpus','1','--memory','1g',
                                  '-e','AGENTHACK_TELEMETRY_REQUIRED=false','--mount',f'type=bind,source={health},target=/tmp/check_health.py,readonly',image])
    assert re.fullmatch('[0-9a-f]{64}',container)
    try:
        step('health',['exec',container,'python','/tmp/check_health.py'],60)
        step('server-logs',['logs',container])
        status=json.loads(step('container-inspect',['inspect',container]))[0]
        assert status['State']['Running'] and not status['State']['OOMKilled']
    finally:
        step('health-stop',['rm','-f',container],check=False)
    suite=output/'suite'
    export(REF,suite,['tests','pyproject.toml','requirements-test.txt'])
    test_build=output/'test-build';test_build.mkdir()
    (test_build/'requirements-test.txt').write_bytes((suite/'requirements-test.txt').read_bytes())
    (test_build/'Dockerfile').write_text(f'FROM {image}\nUSER root\nCOPY requirements-test.txt /tmp/requirements-test.txt\nRUN pip install --no-cache-dir -r /tmp/requirements-test.txt\nUSER agent\n',encoding='utf-8')
    test_image='research-assistant-test:d30-'+REF[:12]
    step('test-build',['build','--pull=false','-t',test_image,test_build],360)
    nodes=['tests/test_d_contract_recovery.py','tests/test_d_official_client_flow.py',
           'tests/test_db206.py::test_isolated_worker_computes_and_denies_files_network_and_processes',
           'tests/test_db206.py::test_wrong_algorithm_is_detected_and_regressing_repair_rejected',
           'tests/test_bd208.py::test_d_object_string_confusion_is_caught_by_generated_contract_test']
    mount=lambda src,dst:f'type=bind,source={src},target={dst},readonly'
    step('image-contract',['run','--rm','--network','none','--cpus','1','--memory','1g',
                          '-e','PYTHONPATH=/app/src:/suite/tests','-e','PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
                          '-e','PYTHONDONTWRITEBYTECODE=1','-e','AGENTHACK_TELEMETRY_REQUIRED=false',
                          '--mount',mount(suite,'/suite'),'--mount',mount(MATERIALS,'/data/experiments'),
                          '--mount',f'type=bind,source={output},target=/results',
                          '--workdir','/suite','--entrypoint','python',test_image,'-m','pytest','-q',
                          '-c','/dev/null','-p','no:cacheprovider','--junitxml=/results/image-contract.xml',*nodes],180)
    cases=ET.parse(output/'image-contract.xml').findall('.//testcase')
    assert len(cases)==10 and all(all(c.find(k) is None for k in ('failure','error','skipped')) for c in cases)
    delivery=ROOT/'交付物'/('2026-11-06-d30-'+REF[:12]);delivery.mkdir(exist_ok=False)
    archive=delivery/('research-assistant-d30-'+REF[:12]+'.tar')
    step('save',['save','-o',archive,image],240)
    with archive.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    with tarfile.open(archive) as tar:
        manifest=json.load(tar.extractfile('manifest.json'))
        assert len(manifest)==1 and manifest[0]['RepoTags']==[image]
    step('reload',['load','-i',archive],240)
    assert step('reload-id',['image','inspect','--format','{{.Id}}',image])==info['Id']
    record={'source_commit':REF,'image':image,'image_id':info['Id'],'archive':str(archive),
            'sha256':digest,'bytes':archive.stat().st_size,'source_files_verified':len(actual['files']),
            'candidate_files_verified':len(selected),'base_image_id':base_id,
            'local_health':'10/10 HTTP 200','official_runtime_entry':'agenthack.AgentRuntime(ResearchAgent()).run()',
            'image_contract_tests':10,'image_contract_failures':0,'image_contract_skips':0,
            'source_regression':{'passed':636,'failure':0,'error':0,'skipped':0,'source':'Codex offline Linux'},
            'native_pair_repetitions_per_variant':3,'new_target_model_calls':0,
            'platform_health':'not run','official_score':None,'D02_D05_algorithm_score_gain_verified':False,
            'AGENT_VERSION_metadata':'2.0.10-bd7 (preserved from source; distinguish this build by SHA256 and revision label)',
            'package_evidence':str(output),'baseline_BE_image':old['image_id']}
    save(delivery/'manifest.json',record)
    save(delivery/'environment.json',actual)
    (delivery/'SHA256SUMS.txt').write_text(digest+'  '+archive.name+'\n',encoding='utf-8')
    state.update(complete=True,gate_passed=True,delivery=str(delivery),manifest=record)
    save(output/'summary.json',state)
    print(json.dumps({'delivery':str(delivery),'sha256':digest,'bytes':archive.stat().st_size,'all_package_checks_passed':True},ensure_ascii=False),flush=True)

if __name__=='__main__':run()
