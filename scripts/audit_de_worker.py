"""Python 3.9 原版公开worker复核；读取当前生成源码，不调用正式平台。"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from check_d_generated_worker import inputs

root=Path('/materials');live=Path('/live');out=Path('/results');out.mkdir(exist_ok=True)
spec=importlib.util.spec_from_file_location('boundaries','/code/research_agent/workflows/d_boundary_cases.py')
boundaries=importlib.util.module_from_spec(spec);spec.loader.exec_module(boundaries)
summary=[]
for cid in os.environ.get('AUDIT_CASE_IDS','D-02,D-05').split(','):
    work=Path('/tmp')/cid;shutil.copytree(root/cid,work)
    shutil.copyfile(live/cid/'implementation.py',work/'implementation.py')
    request=json.loads((work/'sample.json').read_text())
    protocol=json.loads((work/'protocol.json').read_text());request['protocol']=protocol
    cases=list(inputs(cid,request))+list(boundaries.boundary_cases(cid,request))
    for path in sorted((root/'公开样例请求'/cid).glob('*.json')):
        envelope=json.loads(path.read_text());payload=envelope['payload'];payload['protocol']=protocol
        if payload['phase']=='run':cases.append(('published_'+path.stem,payload))
    for index,(name,payload) in enumerate(cases):
        def execute(stage,data):
            proc=subprocess.run([sys.executable,str(work/'worker.py')],input=json.dumps(dict(stage=stage,payload=data)),
                                capture_output=True,text=True,timeout=20,cwd=work)
            if proc.returncode:raise RuntimeError(proc.stderr or proc.stdout)
            return json.loads(proc.stdout)
        started=time.monotonic();result=execute('run',payload);repeat=execute('run',payload)
        evidence=dict(case_id=cid,scenario=name,request=payload,result=result,repeat_equal=result==repeat,
            source_sha256=hashlib.sha256((work/'implementation.py').read_bytes()).hexdigest(),
            worker_sha256=hashlib.sha256((work/'worker.py').read_bytes()).hexdigest(),python=sys.version)
        assert evidence['repeat_equal']
        if cid=='D-02':
            evidence['ranks']=[]
            configurations=[('baseline',protocol['default_config'])]
            if result['candidate'] is not None:
                configurations.append(('candidate',result['details']['selected']))
            for kind,config in configurations:
                ranked=dict(payload,phase='rank',data=dict(payload['data'],selected=config,dev_queries=[]))
                actual=execute('rank',ranked)
                evidence['ranks'].append(dict(kind=kind,config=config,result=actual))
            if index==0:
                evidence['benchmarks']=[]
                for rank in evidence['ranks']:
                    ranked=dict(payload,phase='rank',data=dict(payload['data'],selected=rank['config'],dev_queries=[]))
                    for i in range(3):
                        measured=execute('benchmark_'+rank['kind']+'_'+str(i+1),dict(request=ranked,calls=protocol['timing_calls']))
                        assert measured['completed_queries']==len(ranked['data']['test_queries'])*protocol['timing_calls']
                        evidence['benchmarks'].append(dict(kind=rank['kind'],repeat=i+1,**measured))
        evidence['seconds']=time.monotonic()-started
        (out/(cid+'-'+str(index)+'.json')).write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
        summary.append(dict(case_id=cid,scenario=name,repeat_equal=True))
(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print('Public worker checks:',len(summary),'each independently repeated')
