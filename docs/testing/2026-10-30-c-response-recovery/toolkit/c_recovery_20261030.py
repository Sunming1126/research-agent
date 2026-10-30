"""C recovery counterexamples/regression; fresh original Harness invocation required."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent.parent
HERE=ROOT/'镜像测试'
SOURCE=ROOT/'协同开发/research-agent-stability-20261027'
BASE=HERE/'审核/2026-10-30-c-recovery-8b949de/source'
SID='session-9c903d0e-5884-44fe-bc7f-3719da00864b'
WID='c61ec5e8-9932-4ef5-9709-fed302ad7355'
PYTHON=HERE/'runtime/windows-python312/python.exe'


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def native_proof():
    from native_workspace_harness import workspace,native_rows,selection
    assert SID in workspace()['sessionIds'];selection()
    request=json.loads((ROOT/'.session-audit/c-recovery-request-20261030.json').read_text(encoding='utf-8'))
    assert request['session_id']==SID and request['workspace_id']==WID
    for _ in range(30):
        rows,_=native_rows(SID)
        for row in rows:
            if row.get('seq',0)<=request['before_seq'] or row.get('type')!='assistant/message':continue
            for item in row.get('data',{}).get('message',{}).get('content',[]):
                text=json.dumps(item,ensure_ascii=False)
                if item.get('type')=='tool-call' and 'c_recovery_20261030.py' in text and '--stage all' in text:
                    return {'session_id':SID,'workspace_id':WID,'native_tool_execution_verified':True,
                            'tool_call_sha256':hashlib.sha256(text.encode()).hexdigest(),'provider':'magic'}
        time.sleep(.2)
    raise RuntimeError('No fresh required native Harness tool invocation')


def run(source,out,tests):
    out.mkdir(parents=True,exist_ok=False)
    paths=[str(source),str(source/'src'),str(source/'tests'),str(HERE/'runtime/py312-windows'),str(HERE)]
    boot=('import sys;sys.path[:0]='+repr(paths)+';import c_no_model_guard_20261030;'
          'import research_agent;from pathlib import Path;'
          'assert Path(research_agent.__file__).resolve().is_relative_to(Path('+repr(str(source))+').resolve());'
          'import pytest;raise SystemExit(pytest.main('+repr(['-q','-c',str(source/'pyproject.toml'),
          '-o','pythonpath=','-o','addopts=','--junitxml='+str(out/'junit.xml'),'--basetemp='+str(out/'temp'),*map(str,tests)])+'))')
    env=os.environ.copy()
    env.pop('ANIC_OFFLINE_TEST_AUTHORIZATION',None)
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONDONTWRITEBYTECODE='1',RESEARCH_RUNTIME='local',
        AGENTHACK_TELEMETRY_REQUIRED='false',ANIC_C_TEST_GUARD_LOG=str(out/'network-guard.json'),
        RESEARCH_TEST_PAPERS=str(ROOT/'测试输入/交付镜像/team_share/papers'),
        PUBLIC_EXPERIMENT_ROOT=str(ROOT/'测试输入/交付镜像/team_share/experiments/research-experiments'))
    start=time.monotonic()
    process=subprocess.run([str(PYTHON),'-X','utf8','-c',boot],cwd=source,env=env,capture_output=True,timeout=600)
    (out/'stdout.txt').write_bytes(process.stdout);(out/'stderr.txt').write_bytes(process.stderr)
    result={'returncode':process.returncode,'seconds':round(time.monotonic()-start,3),'target_model_calls':0}
    if (out/'junit.xml').is_file():
        suite=ET.parse(out/'junit.xml').getroot()
        suites=[suite] if suite.tag=='testsuite' else list(suite.iter('testsuite'))
        result.update({key:sum(int(s.attrib.get(key,0)) for s in suites) for key in ['tests','failures','errors','skipped']})
        result['passed']=result['tests']-result['failures']-result['errors']-result['skipped']
    save(out/'execution.json',result)
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',choices=['all'],required=True);parser.parse_args()
    proof=native_proof()
    out=HERE/'runs'/('c-recovery-20261030-'+time.strftime('%H%M%S')+'-'+uuid.uuid4().hex[:6])
    out.mkdir(parents=True,exist_ok=False)
    save(out/'native-proof.json',proof)
    save(ROOT/'.session-audit/c-recovery-latest-20261030.json',{'run':str(out)})
    hashes={p.relative_to(SOURCE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in SOURCE.rglob('*.py') if '__pycache__' not in p.parts and '.git' not in p.parts}
    save(out/'candidate-source-files.json',hashes)
    test=SOURCE/'tests/test_c_response_recovery.py'
    baseline=run(BASE,out/'baseline-counterexamples',[test])
    candidate=run(SOURCE,out/'candidate-counterexamples',[test])
    regression=run(SOURCE,out/'candidate-regression',[SOURCE/'tests'])
    assert all(hashlib.sha256((SOURCE/name).read_bytes()).hexdigest()==value for name,value in hashes.items()),'Source changed during tests'
    summary={'native_harness_execution':True,'proof':proof,'baseline':baseline,'candidate':candidate,
             'regression':regression,'target_model_calls':0,'official_eval_root_cause_confirmed':False,
             'linux_container_acceptance':False,'source_files':len(hashes)}
    summary['candidate_offline_checks_passed']=(baseline.get('failures')==3 and baseline.get('passed')==2
            and not baseline.get('errors') and candidate.get('passed')==5 and candidate.get('returncode')==0
            and regression.get('returncode')==0)
    save(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)
    if not summary['candidate_offline_checks_passed']:raise SystemExit(1)


if __name__=='__main__':main()
