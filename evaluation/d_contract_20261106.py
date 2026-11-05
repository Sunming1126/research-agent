"""Pinned D30 contract comparison, run only by the original native Harness."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import uuid
import xml.etree.ElementTree as ET

ROOT=next(p for p in Path(__file__).resolve().parents
          if (p/'AGENTS.md').is_file() and (p/'镜像测试').is_dir())
REPO=ROOT/'协同开发/research-agent-d-contract-20261106'
BASE='dc9ef0f2c9d257d14536389cbf72fa0b3e89b9bd'
FIXTURE=ROOT/'镜像测试/runs/academic-linux-20261105-11c45136'
SID='session-9c903d0e-5884-44fe-bc7f-3719da00864b'

def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def run(proof):
    assert proof
    offline=proof.get('execution_origin')=='Codex offline'
    if offline:
        authorization=json.loads((ROOT/'.session-audit/d-contract-offline-authorization-20261106.json').read_text(encoding='utf-8'))
        assert authorization['explicit_user_authorization'] is True
        assert authorization['scope']=='D zero-model fault injection and Linux regression'
        assert proof['target_model_calls']==0 and proof['native_tool_execution_verified'] is False
    else:
        assert proof['native_tool_execution_verified'] and proof['session_id']==SID
    candidate=proof['candidate_commit']
    output=ROOT/'镜像测试/runs'/('d-contract-20261106-'+uuid.uuid4().hex[:8])
    output.mkdir()
    claim=ROOT/'.session-audit'/('d-contract-claim-'+candidate[:12]+'.json')
    with claim.open('x',encoding='utf-8') as owned:
        json.dump({'candidate':candidate,'directory':str(output),'origin':'Codex offline' if offline else 'original Harness',
                   'purpose':'prevent duplicate execution of the same queued D comparison'},owned,indent=2)
    save(output/('codex-offline-proof.json' if offline else 'native-proof.json'),proof)
    save(ROOT/'.session-audit/d-contract-latest-20261106.json',{'directory':str(output)})
    state={'baseline_commit':BASE,'candidate_commit':candidate,'target_model_calls':0,
           'official_score':None,'steps':{},'scope':('Codex offline' if offline else 'original Harness')+' / Linux / current contract / synthetic fault injection'}
    docker=Path(os.environ['LOCALAPPDATA'])/'Programs/DockerDesktop/resources/bin/docker.exe'
    def step(name,args,timeout):
        began=time.monotonic()
        with (output/(name+'.stdout')).open('wb') as out,(output/(name+'.stderr')).open('wb') as err:
            try:
                result=subprocess.run([str(docker),*map(str,args)],stdout=out,stderr=err,timeout=timeout)
                code=result.returncode
            except subprocess.TimeoutExpired:
                code=124
        state['steps'][name]={'returncode':code,'seconds':round(time.monotonic()-began,3)}
        save(output/'summary.json',state)
        return code
    assert step('engine',['version','--format','{{.Server.Os}} {{.Server.Arch}}'],20)==0
    assert (output/'engine.stdout').read_text().strip()=='linux amd64'
    assert step('environment',['image','inspect','--format','{{.Id}} {{.Os}} {{.Architecture}}',
                               'research-agent-regression:dc9ef0f2c9d2'],20)==0
    materials=FIXTURE/'source/科研助手赛题材料/research-experiments-20261020-source-v1'
    environment={'image':(output/'environment.stdout').read_text().strip(),
                 'cpu':1,'memory':'1g','network':'none','seed':42,
                 'materials':{f'{cid}/{name}':hashlib.sha256((materials/cid/name).read_bytes()).hexdigest()
                              for cid in ('D-01','D-02','D-05') for name in ('method.md','protocol.json','sample.json')}}
    state['environment_fingerprint']=hashlib.sha256(json.dumps(environment,sort_keys=True).encode()).hexdigest()
    save(output/'environment.json',environment)
    snapshot=output/'candidate'
    shutil.copytree(FIXTURE/'source',snapshot)
    def overlay(ref,target,paths):
        raw=subprocess.check_output(['git','-c','filter.lfs.smudge=','-c','filter.lfs.process=',
                                     'archive',ref,*paths],cwd=REPO,timeout=90)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for member in archive:
                if not member.isfile():
                    continue
                destination=target/member.name
                assert destination.resolve().is_relative_to(target.resolve())
                destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_bytes(archive.extractfile(member).read())
    overlay(candidate,snapshot,['src','tests','agent.py','requirements.txt','pyproject.toml'])
    paired=output/'baseline-current-tests'
    shutil.copytree(snapshot,paired)
    overlay(BASE,paired,['src','agent.py','requirements.txt','pyproject.toml'])
    manifest=lambda root:{p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted((root/'src').rglob('*.py'))}
    baseline_hashes=manifest(paired);candidate_hashes=manifest(snapshot)
    changes=[k for k in sorted(set(baseline_hashes)|set(candidate_hashes)) if baseline_hashes.get(k)!=candidate_hashes.get(k)]
    assert changes==['src/research_agent/workflows/layered_d_runner.py'],changes
    assert (snapshot/'tests/test_d_contract_recovery.py').read_bytes()==(paired/'tests/test_d_contract_recovery.py').read_bytes()
    save(output/'source-manifest.json',{'baseline':baseline_hashes,'candidate':candidate_hashes,'production_changes':changes})
    history=Path(subprocess.check_output(['git','rev-parse','--path-format=absolute','--git-common-dir'],cwd=REPO,text=True).strip())
    mount=lambda src,dst: 'type=bind,source='+str(src)+',target='+dst+',readonly'
    command=['run','--rm','--network','none','--cpus','1','--memory','1g',
             '-e','RESEARCH_RUNTIME=local','-e','PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
             '-e','AGENTHACK_TELEMETRY_REQUIRED=false','-e','PYTHONDONTWRITEBYTECODE=1',
             '-e','LAYERED_PUBLIC_EXPERIMENT_ROOT=/data/experiments','-e','PUBLIC_EXPERIMENT_ROOT=/legacy-materials',
             '--mount',mount(FIXTURE/'source/科研助手赛题材料/research-experiments-20261020-source-v1','/data/experiments'),
             '--mount',mount(FIXTURE/'source/科研助手赛题材料/research-experiments-20261020-source-v1','/materials'),
             '--mount',mount(ROOT/'比赛材料/challenge-tbd/research-experiments','/legacy-materials'),
             '--mount',mount(ROOT/'协同开发/research-agent-friend-development/team_share/papers','/real-papers'),
             '--mount',mount(history,'/history'),'-e','GIT_DIR=/history','-e','GIT_WORK_TREE=/repo',
             '-e','GIT_OPTIONAL_LOCKS=0','-e','GIT_CONFIG_COUNT=1','-e','GIT_CONFIG_KEY_0=safe.directory',
             '-e','GIT_CONFIG_VALUE_0=/history','-e','PYTHONPATH=/repo/src:/repo',
             '--mount','type=bind,source='+str(output)+',target=/results','--workdir','/repo','--entrypoint','python']
    image='research-agent-regression:dc9ef0f2c9d2'
    def pytest(name,source,nodes):
        code=step(name,command+['--mount',mount(source,'/repo'),image,'-m','pytest','-q','-p','no:cacheprovider',
                               '--junitxml=/results/'+name+'.xml',*nodes],600)
        xml=output/(name+'.xml')
        if not xml.is_file():
            raise RuntimeError(name+' produced no JUnit')
        cases=ET.parse(xml).findall('.//testcase')
        counts={key:sum(c.find(key) is not None for c in cases) for key in ('failure','error','skipped')}
        failed=[c.attrib['classname']+'::'+c.attrib['name'] for c in cases if c.find('failure') is not None]
        result={**counts,'passed':len(cases)-sum(counts.values()),'failed_ids':failed,'returncode':code}
        state[name]=result
        save(output/'summary.json',state)
        return result
    order=['baseline','candidate','candidate','baseline','baseline','candidate']
    pair=[]
    for index,kind in enumerate(order):
        pair.append(pytest(f'pair-{index}-{kind}',paired if kind=='baseline' else snapshot,['tests/test_d_contract_recovery.py']))
    state['paired_gate']=all(r['failure']==(2 if kind=='baseline' else 0) and not r['error'] and not r['skipped']
                             for kind,r in zip(order,pair))
    full=pytest('full',snapshot,['tests'])
    state['gate_passed']=state['paired_gate'] and full['returncode']==0 and not any(full[k] for k in ('failure','error','skipped'))
    state['complete']=True
    save(output/'summary.json',state)
    print(json.dumps(state,ensure_ascii=False),flush=True)
    if not state['gate_passed']:
        raise RuntimeError('D contract gate failed; retained all raw evidence at '+str(output))
