"""Run real-paper tests only after a fresh tool call in the original native chat."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
HERE=ROOT/'镜像测试'
SID='session-9c903d0e-5884-44fe-bc7f-3719da00864b'
WID='c61ec5e8-9932-4ef5-9709-fed302ad7355'
PYTHON=HERE/'runtime/windows-python312/python.exe'
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def proof_from_rows(rows,request,study,stage,number):
    for row in rows:
        if row.get('seq',0)<=request['before_seq'] or row.get('type')!='assistant/message':continue
        for item in row.get('data',{}).get('message',{}).get('content',[]):
            text=json.dumps(item,ensure_ascii=False)
            if (item.get('type')=='tool-call' and 'p0_ce_real_20261031.py' in text
                    and study in text and '--stage '+stage in text and '--round '+str(number) in text):
                # Native v4 emits a config header when the active configuration
                # is established, not necessarily once per user turn. Keep the
                # latest actual header before this fresh tool call; never invent one.
                headers=[r for r in rows if r.get('type')=='request/header' and r.get('seq',0)<=row['seq']]
                assert headers,'No native configuration evidence'
                header=max(headers,key=lambda r:r['seq']);config=header['data']['header']['config']
                assert config.get('provider')=='magic' and config.get('model')=='deepseek-v4.1-flash'
                return {'session_id':SID,'workspace_id':WID,'study':study,'stage':stage,'round':number,
                        'native_tool_seq':row['seq'],'native_tool_name':item.get('name'),
                        'native_tool_call_sha256':hashlib.sha256(text.encode()).hexdigest(),
                        'model_config_header_seq':header['seq'],
                        'model_config_inherited':header['seq']<=request['before_seq'],
                        'native_tool_execution_verified':True,'magic_selection_verified':True,
                        'ui_binding_sha256':request['ui_binding_sha256']}
    return None
def native_proof(study,stage,number):
    from native_workspace_harness import workspace,selection,native_rows
    assert SID in workspace()['sessionIds'];selection()
    request_path=ROOT/'.session-audit/p0-ce-real-requests-20261031'/f'{study}-{stage}-{number}.json'
    request=json.loads(request_path.read_text(encoding='utf-8'))
    assert request['session_id']==SID and request['workspace_id']==WID and request['study']==study
    for _ in range(50):
        rows,_=native_rows(SID)
        evidence=proof_from_rows(rows,request,study,stage,number)
        if evidence:return evidence
        time.sleep(.2)
    raise RuntimeError('No fresh original native tool call proof; no models invoked')
def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('--study',required=True)
    parser.add_argument('--stage',choices=['precheck','pair'],required=True);parser.add_argument('--round',type=int,required=True)
    args=parser.parse_args()
    assert args.study.startswith('p0-ce-real-20261031-') and '/' not in args.study and '\\' not in args.study
    assert (args.stage=='precheck' and args.round==0) or (args.stage=='pair' and 1<=args.round<=3)
    study=HERE/'runs'/args.study;protocol=json.loads((study/'protocol.json').read_text(encoding='utf-8'))
    proof=native_proof(args.study,args.stage,args.round)
    phase=study/(args.stage+'-'+str(args.round)+'-'+time.strftime('%H%M%S')+'-'+uuid.uuid4().hex[:4])
    phase.mkdir(parents=True,exist_ok=False);save(phase/'native-proof.json',proof)
    save(ROOT/'.session-audit/p0-ce-real-phase-latest-20261031.json',{'study':args.study,'phase':str(phase)})
    if args.stage=='pair':
        ready=json.loads((study/'precheck-result.json').read_text(encoding='utf-8'))
        assert ready['complete'] and ready['candidate_precheck_passed']
    tools=[Path(__file__),HERE/'p0_ce_real_worker_20261031.py',HERE/'magic_api_bridge.py',HERE/'read_magic_connection.cjs']
    tool_hashes={str(p.relative_to(ROOT)):sha(p) for p in tools};save(phase/'toolkit-fingerprints.json',tool_hashes)
    cases=[r for r in protocol['cases'] if args.stage!='precheck' or r['case_id'] in protocol['precheck']]
    schedule=[]
    for i,task in enumerate(cases):
        order=['candidate'] if args.stage=='precheck' else (['baseline','candidate'] if (args.round+i)%2 else ['candidate','baseline'])
        schedule.extend((task,variant) for variant in order)
    results=[]
    for task,variant in schedule:
        directory=phase/(task['case_id']+'-'+variant);directory.mkdir()
        config={'study_directory':str(study),'native_proof':str(phase/'native-proof.json'),
                'task':task,'variant':variant,'phase':args.stage,'round':args.round,
                'transport_id':str(uuid.uuid5(uuid.NAMESPACE_URL,args.study+'/'+phase.name+'/'+task['case_id']))}
        save(directory/'job.json',config)
        source=Path(protocol['source'][variant]['path'])
        paths=[str(source),str(source/'src'),str(HERE/'runtime/py312-windows'),str(HERE)]
        boot='import sys,runpy;sys.path[:0]='+repr(paths)+';runpy.run_path('+repr(str(HERE/'p0_ce_real_worker_20261031.py'))+',run_name="__main__")'
        env=os.environ.copy();env.pop('RESEARCH_RUNTIME',None);env.pop('ANIC_C_TEST_GUARD_LOG',None)
        env.update(ANIC_NATIVE_ORCHESTRATOR_SESSION_ID=SID,AGENTHACK_TELEMETRY_REQUIRED='false',PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
        print(json.dumps({'event':'case_start','case_id':task['case_id'],'variant':variant,'phase':args.stage,'round':args.round}),flush=True)
        with (directory/'stdout.txt').open('wb') as stdout,(directory/'stderr.txt').open('wb') as stderr:
            child=subprocess.Popen([str(PYTHON),'-X','utf8','-c',boot,'--directory',str(directory)],cwd=source,env=env,stdout=stdout,stderr=stderr)
            try:code=child.wait(timeout=225)
            except subprocess.TimeoutExpired:child.kill();child.wait();code=124
        if (directory/'result.json').is_file():row=json.loads((directory/'result.json').read_text(encoding='utf-8'))
        else:
            calls=[json.loads(p.read_text(encoding='utf-8')) for p in (directory/'model-calls').glob('*/call.json')]
            known=[c['usage']['total_tokens'] for c in calls if isinstance(c.get('usage'),dict) and type(c['usage'].get('total_tokens')) is int]
            row={'case_id':task['case_id'],'variant':variant,'status':'worker_timeout' if code==124 else 'worker_setup_failed',
                 'returncode':code,'target_real_model_calls':len(calls),'total_tokens':sum(known) if len(known)==len(calls) else None,
                 'quota_exhausted':any(c.get('error_code') in {'AccountQuotaExceeded','insufficient_quota'} for c in calls)}
            save(directory/'result.json',row)
        row['directory']=directory.name;results.append(row)
        summary={'study':args.study,'stage':args.stage,'round':args.round,'results':results,'complete':False,
                 'native_harness_orchestrated':True,'official_score':None,'image_acceptance':False}
        save(phase/'summary.json',summary)
        print(json.dumps(row,ensure_ascii=False),flush=True)
        assert all(sha(ROOT/path)==digest for path,digest in tool_hashes.items()),'Toolkit changed during actual calls'
        if row.get('quota_exhausted') or (variant=='candidate' and row.get('status')!='completed'):
            summary['stopped_reason']='quota_exhausted' if row.get('quota_exhausted') else 'candidate_hard_failure'
            save(phase/'summary.json',summary)
            if args.stage=='precheck':save(study/'precheck-result.json',{**summary,'candidate_precheck_passed':False})
            return
    summary.update(complete=True,candidate_precheck_passed=all(r.get('status')=='completed' for r in results))
    save(phase/'summary.json',summary)
    if args.stage=='precheck':save(study/'precheck-result.json',summary)
    else:save(study/f'pair-{args.round}-result.json',summary)
    print(json.dumps({'event':'phase_complete','phase':str(phase),'complete':True,'cases':len(results)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
