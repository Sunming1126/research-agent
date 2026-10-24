"""Harness-driven, evidence-preserving acceptance runner for image 0.3.0.

Offline: python test_agent.py --run-id acceptance-20261022
After explicit data approval: add --allow-model-data
All Docker commands are fixed templates; model replies cannot execute commands.
"""
import argparse
from contextlib import contextmanager
from collections import Counter
import copy
from datetime import datetime,timezone,timedelta
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid

from harness_bridge import HarnessBridge,call_harness,parse_model_json,write_json
from public_oracles import verify_public,require

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parent
SHARE=PROJECT/'测试输入/交付镜像/team_share'
MATERIAL=PROJECT/'测试输入/最新要求-20261020'
IMAGE='research-assistant:0.3.0-source-v1'
DIRECT_CASE_HTTP=False


def command(args,timeout=60,input_bytes=None):
    return subprocess.run(args,input=input_bytes,capture_output=True,timeout=timeout,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def docker(*args,timeout=60,input_bytes=None):
    p=command(['docker',*map(str,args)],timeout,input_bytes)
    if p.returncode:raise RuntimeError('Docker '+str(args[0])+' failed: '+p.stderr.decode('utf-8',errors='replace')[-1800:])
    return p.stdout.decode('utf-8',errors='replace').strip()


def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


def http(url,payload=None,method=None,timeout=190,raw=None):
    data=raw if raw is not None else json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8') if payload is not None else None
    req=urllib.request.Request(url,data=data,method=method or ('POST' if data is not None else 'GET'),
                               headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:code,body=r.status,r.read()
    except urllib.error.HTTPError as exc:code,body=exc.code,exc.read()
    try:return code,json.loads(body.decode('utf-8'))
    except ValueError:return code,{'non_json_body':body.decode('utf-8',errors='replace')[:2000]}


class Recorder:
    def __init__(self,root):self.root=root;self.rows=[]
    def add(self,identifier,group,status,evidence=None,error=None,level='required'):
        row={'id':identifier,'group':group,'status':status,'level':level,
             'evidence':evidence,'error':error}
        self.rows.append(row);write_json(self.root/'checks.json',self.rows)
        print(json.dumps({'id':identifier,'status':status,'error':error},ensure_ascii=False),flush=True)
        return row
    def test(self,identifier,group,fn,level='required'):
        started=time.monotonic()
        try:row=self.add(identifier,group,'PASS',fn(),level=level)
        except Exception as exc:
            row=self.add(identifier,group,'FAIL',error=type(exc).__name__+': '+str(exc)[:1400],level=level)
            (self.root/'failures').mkdir(exist_ok=True)
            (self.root/'failures'/(re.sub(r'[^A-Za-z0-9_.-]','_',identifier)+'.txt')).write_text(traceback.format_exc(),encoding='utf-8')
        row['seconds']=round(time.monotonic()-started,3);write_json(self.root/'checks.json',self.rows)
        return row


@contextmanager
def target(root,*,bridge=None,case_id=None,local_legacy=False):
    root.mkdir(parents=True,exist_ok=True)
    output=root/'output';output.mkdir(exist_ok=True)
    config={'workspace':'/workspace','output':'/output','team':'workspace_team',
            'material_roots':['/workspace/papers'],'material_aliases':{'本地论文文件夹内的 PDF':['papers']},
            'experiment_mode':'platform','ocr':False,'llm_calls':30,'llm_tokens':40000,'case_timeout':180}
    if case_id:config['material_roots'].append('/data/experiments')
    if local_legacy:
        config.update(experiment_mode='local',local_experiment_root='/experiments')
        config['material_roots'].append('/experiments')
        config['material_aliases'].update({f'D-0{i}.json':[f'publicdata/D-0{i}.json'] for i in range(1,6)})
    write_json(root/'config.json',config)
    port=free_port();name='agent-acceptance-'+uuid.uuid4().hex[:12]
    args=['run','-d','--pull=never','--name',name,'--read-only','--cap-drop','ALL',
          '--security-opt','no-new-privileges','--cpus','1','--memory','1024m','--pids-limit','128',
          '--tmpfs','/tmp:rw,nosuid,size=256m','-p',f'127.0.0.1:{port}:8080',
          '-e','AGENTHACK_TELEMETRY_REQUIRED=false','-e','PYTHONDONTWRITEBYTECODE=1',
          '-e','RESEARCH_CONFIG=/test-config.json',
          '--mount',f'type=bind,source={root / "config.json"},target=/test-config.json,readonly',
          '--mount',f'type=bind,source={SHARE / "papers"},target=/workspace/papers,readonly',
          '--mount',f'type=bind,source={output},target=/output']
    if bridge:
        session=root.name
        args+=['-e',f'PLATFORM_LLM_URL=http://host.docker.internal:{bridge.port}/session/{session}/v1',
               '-e','PLATFORM_API_KEY='+bridge.token,'-e','PLATFORM_LLM_MODEL=deepseek-harness',
               '-e','CHALLENGE_SESSION_ID='+session]
        if os.environ.get('TEST_HARNESS_REASONING')=='off':
            args+=['-e','RESEARCH_LLM_THINKING=disabled']
    if case_id:
        args+=['-e','RESEARCH_EXPERIMENT_CASE='+case_id,'-e','RESEARCH_LAYERED_D=layered-d-v3',
               '-e','RESEARCH_D_SOURCE_PROVENANCE=d-model-source-v1',
               '--mount',f'type=bind,source={MATERIAL / case_id},target=/data/experiments/{case_id},readonly',
               '--mount',f'type=bind,source={MATERIAL / "run_experiment.py"},target=/data/experiments/run_experiment.py,readonly']
    if local_legacy:
        args+=['--mount',f'type=bind,source={SHARE / "experiments/research-experiments"},target=/experiments,readonly']
    created=False
    try:
        docker(*args,IMAGE,timeout=60);created=True
        base=f'http://127.0.0.1:{port}'
        for _ in range(45):
            try:
                code,value=http(base+'/health',timeout=2)
                if code==200:
                    write_json(root/'health.json',value);break
            except (OSError,TimeoutError):pass
            time.sleep(.4)
        else:raise RuntimeError('Target did not become healthy')
        yield base,output,name
    finally:
        if created:
            try:(root/'container.log').write_text(docker('logs',name),encoding='utf-8')
            except Exception:pass
            try:
                stats=json.loads(docker('inspect',name))[0]
                # Do not archive model keys, including the ephemeral bridge key.
                stats['Config']['Env']=[v.split('=',1)[0]+'=[REDACTED]' if re.search(r'KEY|TOKEN|SECRET',v.split('=',1)[0],re.I) else v for v in stats['Config'].get('Env',[])]
                write_json(root/'container-inspect.json',stats)
            except Exception:pass
            docker('rm','-f',name)


class FaultGateway:
    """Explicit HTTP compatibility fixtures, never recorded as real LLM calls."""
    def __init__(self,mode):
        self.mode=mode;self.calls=0;self.token='fixture-no-real-key';owner=self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length','0')));owner.calls+=1
                if owner.mode=='slow':time.sleep(0.4)
                code=200
                if owner.mode=='auth':code=401;body={'error':{'message':'fixture unauthorized'}}
                elif owner.mode=='rate_limit' and owner.calls==1:code=429;body={'error':{'message':'fixture rate limited'}}
                else:
                    content=json.dumps({'answer':'这是确定性 HTTP 兼容测试夹具的回答。','clarifying_question':'研究对象的空间尺度是什么？'},ensure_ascii=False)
                    if owner.mode=='invalid_json':content='not-json'
                    body={'id':'fixture','object':'chat.completion','created':int(time.time()),'model':'fixture',
                          'choices':[{'index':0,'finish_reason':'length' if owner.mode=='truncated' else 'stop',
                                      'message':{'role':'assistant','content':content}}],
                          'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
                data=json.dumps(body,ensure_ascii=False).encode()
                self.send_response(code);self.send_header('Content-Type','application/json')
                if code==429:self.send_header('Retry-After','0')
                self.send_header('Content-Length',str(len(data)));self.end_headers()
                try:self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError):pass
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('0.0.0.0',0),Handler);self.port=self.server.server_address[1]
    def __enter__(self):threading.Thread(target=self.server.serve_forever,daemon=True).start();return self
    def __exit__(self,*args):self.server.shutdown();self.server.server_close()


def request_for(cid):
    value=json.loads((SHARE/'requests'/f'{cid}.json').read_text(encoding='utf-8-sig'))
    value['constraints']['timeout_ms']=180000
    return value


def unpack_answer(body):
    if isinstance(body,dict) and isinstance(body.get('case_id'),str):
        return body
    require(body.get('status')=='success','SDK execution failed: '+json.dumps(body,ensure_ascii=False)[:1000])
    answer=body.get('output',{}).get('answer')
    require(isinstance(answer,str),'SDK output.answer must contain a JSON string')
    return json.loads(answer)


def solve_http(base,task,request_path):
    payload=task['input'] if DIRECT_CASE_HTTP else task
    write_json(request_path,payload)
    code,body=http(base+'/solve',payload)
    require(code==200,'Solve HTTP '+str(code)+': '+json.dumps(body,ensure_ascii=False)[:800])
    if DIRECT_CASE_HTTP:
        require(body.get('case_id')==payload['case_id'],'Expected direct unwrapped case JSON')
    return code,body


def independent_contract(task,result,output):
    case=task['input'];cid=case['case_id'];kind=case['function']
    require(result.get('case_id')==cid,'case_id changed')
    if kind=='Chat':
        require(set(result)=={'case_id','answer','clarifying_question'},'Wrong Chat fields')
        require(all(isinstance(result[k],str) and result[k].strip() for k in ['answer','clarifying_question']),'Empty Chat answer')
    elif kind=='Literature_Retrieval':
        require(set(result)=={'case_id','papers'},'Wrong retrieval fields')
        seen=set()
        for paper in result['papers']:
            require(set(paper)=={'filename','reason'},'Wrong paper fields')
            require(paper['filename'] not in seen,'Duplicate paper');seen.add(paper['filename'])
            require((SHARE/'papers'/paper['filename']).is_file(),'Nonexistent paper')
            require(paper['reason'].strip(),'Empty retrieval reason')
    elif kind in ['Survey','Idea']:
        require(set(result)=={'case_id','path','markdown'},'Wrong report fields')
        body=result['markdown'].split('## 参考文献')[0]
        require(len(re.sub(r'\s','',body))>=800,'Report below 800 non-space characters')
        require(result['markdown'].count('## 参考文献')==1,'References section missing/duplicated')
        target_path=(output/result['path']).resolve();require(target_path.is_relative_to(output.resolve()),'Report path escapes output')
        require(target_path.read_text(encoding='utf-8')==result['markdown'],'Report differs from file')
    else:
        require(set(result)=={'case_id','status','metrics','figures'},'Wrong final D fields')
        require(len(result['metrics'])==3,'D requires 3 full metric objects')
        for item in result['metrics']:
            require(set(item)=={'name','unit','reference','observed','relative_error'},'Wrong metric fields')
            if result['status']!='completed':require(item['observed'] is None,'Failure claims a measurement')
    saved=output/'data/workspace_team'/f'{cid}.json'
    require(saved.is_file(),'Final JSON not saved')
    require(json.loads(saved.read_text(encoding='utf-8'))==result,'Saved JSON differs from HTTP result')
    return {'case_id':cid,'kind':kind,'status':result.get('status','answered'),'artifact':str(saved)}


def inventory(rec):
    def manifests():
        package=json.loads((MATERIAL/'PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))
        count=0
        for name,entry in package['files'].items():
            p=MATERIAL/name;require(p.is_file(),'Missing required material '+name)
            require(hashlib.sha256(p.read_bytes()).hexdigest()==entry['sha256'],'Material digest mismatch '+name)
            require(p.stat().st_size==entry['bytes'],'Material length mismatch '+name);count+=1
        for cid in ['D-01','D-02','D-05']:
            m=json.loads((MATERIAL/cid/'manifest.json').read_text(encoding='utf-8'))
            for name,digest in m['files'].items():require(hashlib.sha256((MATERIAL/cid/name).read_bytes()).hexdigest()==digest,'Case manifest mismatch '+cid+'/'+name)
        return {'checked_files':count,'version':package['document_revision']}
    rec.test('inputs.new_requirement_manifests','input-integrity',manifests)
    def share_hashes():
        checked=0
        for line in (SHARE/'SHA256SUMS.txt').read_text(encoding='utf-8-sig').splitlines():
            m=re.match(r'^([a-fA-F0-9]{64})\s+\*?(.+)$',line.strip())
            if not m:continue
            name=m[2].replace('\\','/');name=name.removeprefix('team_share/').removeprefix('./')
            p=(SHARE/name).resolve();require(p.is_relative_to(SHARE.resolve()),'Manifest path escapes input')
            require(p.is_file(),'Missing delivery file '+name)
            require(hashlib.file_digest(p.open('rb'),'sha256').hexdigest().lower()==m[1].lower(),'Delivery digest mismatch '+name);checked+=1
        require(checked>0,'No delivery hashes parsed');return {'checked_files':checked}
    rec.test('inputs.delivery_hashes','input-integrity',share_hashes)
    def inspect_image():
        info=json.loads(docker('image','inspect',IMAGE))[0];write_json(rec.root/'image-inspect.json',info)
        require(info['Architecture']=='amd64' and info['Os']=='linux','Wrong target architecture')
        require(info['Config'].get('User') not in ('','root','0'),'Root image')
        require('8080/tcp' in info['Config'].get('ExposedPorts',{}),'Missing declared port')
        require(info['Config']['Labels']['org.agenthack.version']==IMAGE.rsplit(':',1)[-1],'Wrong target version')
        return {'image_id':info['Id'],'architecture':info['Architecture'],'user':info['Config']['User']}
    rec.test('image.metadata','runtime',inspect_image)


def offline(rec):
    out=rec.root/'offline';out.mkdir(exist_ok=True)
    def run_suite():
        log=docker('run','--rm','--pull=never','--network','none','--read-only','--cap-drop','ALL',
                   '--security-opt','no-new-privileges','--cpus','1','--memory','1024m','--pids-limit','64',
                   '--tmpfs','/tmp:rw,nosuid,size=128m','-e','PYTHONDONTWRITEBYTECODE=1',
                   '-e','AGENTHACK_TELEMETRY_REQUIRED=false','--mount',f'type=bind,source={HERE},target=/testkit,readonly',
                   '--mount',f'type=bind,source={MATERIAL},target=/data/experiments,readonly',
                   '--mount',f'type=bind,source={out},target=/results','--entrypoint','python',IMAGE,'/testkit/container_checks.py')
        (out/'stdout.jsonl').write_text(log,encoding='utf-8')
        rows=json.loads((out/'offline-checks.json').read_text(encoding='utf-8'))
        for row in rows:rec.add('container.'+row['id'],row['group'],row['status'],evidence=row.get('evidence'),error=row.get('error'),level=row.get('level','required'))
        return {'checks':len(rows)}
    rec.test('container.suite_executed','runtime',run_suite)


def http_checks(rec):
    root=rec.root/'http-no-model'
    with target(root) as (base,out,name):
        rec.test('http.health','http',lambda:require(http(base+'/health')[0]==200,'Health not 200'))
        rec.test('http.unknown_route','http',lambda:require(http(base+'/not-a-route')[0]==404,'Unknown route accepted'))
        for label,payload in [('empty',{}),('array',[]),('wrong_case_function',dict(request_for('A-01'),input=dict(request_for('A-01')['input'],function='Survey')))]:
            def run(payload=payload,label=label):
                code,body=http(base+'/solve',payload);write_json(root/(label+'.json'),{'http':code,'response':body})
                require(code>=400 or body.get('status') in ['error','timeout'],'Malformed input accepted')
                return {'http':code,'sdk_status':body.get('status')}
            rec.test('http.reject.'+label,'input-contract',run)
        def malformed():
            code,body=http(base+'/solve',raw=b'{not json');write_json(root/'malformed-json.json',{'http':code,'response':body})
            require(code>=400,'Malformed JSON not rejected');return {'http':code}
        rec.test('http.reject.invalid_json','http',malformed)
        def missing_key():
            code,body=http(base+'/solve',request_for('A-01'));write_json(root/'missing-gateway.json',{'http':code,'response':body})
            require(body.get('status')=='error' or code>=400,'Missing model credentials produced an answer')
            return {'http':code,'sdk_status':body.get('status')}
        rec.test('gateway.missing_credentials_honest_error','gateway-compatibility',missing_key)
        rec.test('http.health_after_errors','http',lambda:require(http(base+'/health')[0]==200,'Errors killed service'))
    for mode in ['auth','invalid_json','truncated','rate_limit']:
        def run(mode=mode):
            location=rec.root/('http-fixture-'+mode)
            with FaultGateway(mode) as gateway,target(location,bridge=gateway) as (base,out,name):
                code,body=http(base+'/solve',request_for('A-01'));write_json(location/'response.json',{'http':code,'response':body})
                if mode=='rate_limit':
                    result=unpack_answer(body);independent_contract(request_for('A-01'),result,out)
                    require(gateway.calls>=2,'Rate limit was not retried')
                else:require(body.get('status')=='error' or code>=400,'Invalid model result was accepted')
                return {'fixture_not_real_llm':True,'gateway_calls':gateway.calls,'sdk_status':body.get('status')}
        rec.test('gateway.http_fixture.'+mode,'gateway-compatibility',run)


def public_samples(rec,cid,source):
    sample_root=MATERIAL/'公开样例请求'
    requests=[sample_root/(cid+'.json'),*sorted((sample_root/cid).glob('*.json'))]
    run_selected=None
    for request_path in requests:
        label=request_path.relative_to(sample_root).as_posix()
        def run(request_path=request_path,label=label):
            nonlocal run_selected
            data=json.loads(request_path.read_text(encoding='utf-8-sig'))
            if data['payload']['phase']=='rank' and run_selected is not None:data['payload']['data']['selected']=run_selected
            folder=rec.root/'public-workers'/cid/(request_path.stem+'-'+uuid.uuid4().hex[:6]);folder.mkdir(parents=True)
            write_json(folder/'request.json',data)
            p=command(['docker','run','--rm','--init','-i','--pull=never','--platform','linux/amd64',
                       '--network','none','--read-only','--user','65534:65534','--cap-drop','ALL',
                       '--security-opt','no-new-privileges','--cpus','1','--memory','1024m','--pids-limit','64',
                       '--ulimit','cpu=20:20','-e','PYTHONHASHSEED=42','-e','PYTHONDONTWRITEBYTECODE=1',
                       '--mount',f'type=bind,source={MATERIAL/cid},target=/experiment,readonly',
                       '--mount',f'type=bind,source={source},target=/experiment/implementation.py,readonly',
                       '--workdir','/experiment','--entrypoint','python','python:3.9-slim','-B','-c',
                       "import signal,runpy; signal.alarm(20); runpy.run_path('worker.py',run_name='__main__')"],
                      timeout=30,input_bytes=json.dumps(data,ensure_ascii=False).encode())
            (folder/'stdout.txt').write_bytes(p.stdout);(folder/'stderr.txt').write_bytes(p.stderr)
            require(p.returncode==0,'Python3.9 public worker failed: '+p.stderr.decode('utf-8',errors='replace')[-1200:])
            result=json.loads(p.stdout.decode('utf-8'));write_json(folder/'result.json',result)
            # worker prints the raw algorithm result (not a final submission).
            if 'result' in result and 'baseline' not in result and isinstance(result['result'],dict):result=result['result']
            evidence=verify_public(data['payload'],result)
            if cid=='D-02' and data['payload']['phase']=='run':run_selected=result.get('details',{}).get('selected')
            return dict(evidence,request=label,python='3.9',network='none',artifact=str(folder))
        rec.test('public_algorithm.'+label,'D-public-algorithm',run)


def live(rec):
    def plan():
        requirements='D01/D02/D05 require complete model-generated source, byte-preserved, Python3.9 stdlib, 180-second whole-case budget. A/B/C/E keep strict schemas and file artifacts.'
        prompt=('你是本次科研助手镜像验收的测试规划 Agent。根据给定要求给出测试关注点与风险。'
                '不得调用工具，不得给出执行命令，不得声称已经运行测试。只返回严格JSON对象，字段为 '
                'focus（字符串数组）、risks（字符串数组）、extra_chat_inputs（最多2条科研问答字符串）。'
                '额外输入不得要求访问凭据、网络、其他队伍文件或隐藏答案。\n'+requirements)
        text,meta=call_harness(prompt,rec.root/'harness-plan',max_tokens=2400);value=parse_model_json(text)
        require(isinstance(value,dict),'Planner did not produce object');write_json(rec.root/'test-plan.json',value)
        return {'actual_backend':'DeepSeek Harness','seconds':meta['seconds'],'plan':value}
    plan_row=rec.test('agent.harness_test_planning','LLM-orchestration',plan)
    with HarnessBridge(rec.root/'harness-model-calls') as bridge:
        with target(rec.root/'live-ABCE',bridge=bridge) as (base,out,name):
            for cid in ['A-01','B-01','C-01','E-01']:
                def solve(cid=cid):
                    task=request_for(cid);started=time.monotonic();code,body=http(base+'/solve',task)
                    elapsed=time.monotonic()-started;write_json(rec.root/'live-ABCE'/(cid+'-response.json'),{'http':code,'seconds':elapsed,'response':body})
                    result=unpack_answer(body);evidence=independent_contract(task,result,out)
                    require(elapsed<=180,'Whole case exceeded 180 seconds')
                    require(bridge.records,'No real Harness model call recorded')
                    return dict(evidence,seconds=elapsed,real_model=True)
                rec.test('live.'+cid,'A-E-live',solve)
            extras=((plan_row.get('evidence') or {}).get('plan') or {}).get('extra_chat_inputs',[])
            for i,text in enumerate(extras[:2]):
                if not isinstance(text,str) or not 1<=len(text)<=500:continue
                def extra(text=text,i=i):
                    task=request_for('A-01');task['task_id']='harness-extra-'+str(i);task['input']['case_id']='A-0'+str(i+2);task['input']['input']=text
                    code,body=http(base+'/solve',task);write_json(rec.root/'live-ABCE'/('extra-'+str(i)+'.json'),body)
                    return independent_contract(task,unpack_answer(body),out)
                rec.test('live.harness_generated_chat.'+str(i),'LLM-generated-tests',extra)
        for cid in ['D-01','D-02','D-05']:
            source_box={}
            def generate(cid=cid):
                directory=rec.root/('live-'+cid)
                protocol=json.loads((MATERIAL/cid/'protocol.json').read_text(encoding='utf-8'))
                task=request_for('D-01');task['task_id']='source-acceptance-'+cid
                task['input'].update(case_id=cid,title='新版源码来源公开测试 '+cid,
                    input='根据本题公开方法和协议生成完整算法，交由官方客户端执行。三个指标：'+
                          '; '.join('name='+x['name']+', unit='+x['unit'] for x in protocol['metrics'])+'。',
                    materials=[cid+'/'+x for x in ['method.md','protocol.json','sample.json']])
                before=len(bridge.records)
                with target(directory,bridge=bridge,case_id=cid) as (base,out,name):
                    started=time.monotonic();code,body=http(base+'/solve',task)
                    elapsed=time.monotonic()-started;write_json(directory/'response.json',{'http':code,'seconds':elapsed,'response':body})
                    result=unpack_answer(body);independent_contract(task,result,out)
                    sources=list((out/'data/workspace_team/work'/cid).glob('*/implementation.py'))
                    require(len(sources)==1,'No unique model-generated implementation artifact')
                    source=sources[0];source_box['path']=source
                    generation=json.loads((source.parent/'generation-response.json').read_text(encoding='utf-8'))
                    content=generation['choices'][0]['message']['content']
                    normalized=source.read_text(encoding='utf-8').replace('\r\n','\n').rstrip('\n')
                    candidates=[content,*re.findall(r'^```(?:python|py)?[ \t]*\r?\n(.*?)^```[ \t]*$',content,re.M|re.S)]
                    require(any(c.replace('\r\n','\n').replace('\r','\n').rstrip('\n')==normalized for c in candidates),'Generated source is not byte-preserving normalization of model reply')
                    calls=bridge.records[before:]
                    require(calls,'No real current-case Harness generation call')
                    require(elapsed<=180,'D source flow exceeded 180 seconds')
                    require(result['status']!='completed','Without official runtime, D must not pretend completion')
                    return {'case_id':cid,'source':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                            'real_model_calls':len(calls),'seconds':elapsed,'formal_experiment':'BLOCKED: competition Unix socket/credential service not supplied'}
            rec.test('live.'+cid+'.model_source_generation','D-source-live',generate)
            if 'path' in source_box:public_samples(rec,cid,source_box['path'])
            else:rec.add('public_algorithm.'+cid,'D-public-algorithm','BLOCKED',error='Target did not produce a valid current-case source')
        write_json(rec.root/'harness-model-calls'/'calls-index.json',bridge.records)
    rec.add('official.source_admission_and_full_scoring','official-platform','BLOCKED',error='Requires competition current-case Gateway audit, signed source service, private cases and trusted runner; local Harness does not replace them')


def review(rec):
    evidence=[{k:v for k,v in row.items() if k in ['id','group','status','level','error']} for row in rec.rows]
    prompt=('你是测试结果复核 Agent。以下是已经执行的检查结果。禁止调用工具。'
            '只依据这些结果写中文复核意见，区分必需项失败、质量改进和未测/阻塞。'
            '不能把夹具当成真实模型、不能把本地测试当成官方准入或成绩、不能声称全部通过。'
            '输出严格JSON对象：summary、confirmed_failures（数组）、quality_risks（数组）、blocked（数组）、next_actions（数组）。\n'
            +json.dumps(evidence,ensure_ascii=False))
    text,meta=call_harness(prompt,rec.root/'harness-review',timeout=150,max_tokens=3200)
    value=parse_model_json(text);write_json(rec.root/'harness-review.json',value)
    return {'real_model':True,'review_file':str(rec.root/'harness-review.json'),'seconds':meta['seconds']}


def report(rec):
    counts=Counter(x['status'] for x in rec.rows)
    summary={'target':IMAGE,'run_directory':str(rec.root),'counts':dict(counts),
             'mandatory_failures':[x['id'] for x in rec.rows if x['status']=='FAIL' and x['level']=='required'],
             'quality_failures':[x['id'] for x in rec.rows if x['status']=='FAIL' and x['level']=='quality'],
             'official_score':None,'all_requirements_verified':False,
             'note':'Real image tests and actual DeepSeek Harness calls; fixtures explicitly labelled. Official signed provenance/private full scoring unavailable.'}
    write_json(rec.root/'summary.json',summary)
    lines=['**科研助手 0.3.0 自动化验收报告**','',f'目标：`{IMAGE}`',f'结果：{dict(counts)}','',
           '真实模型后端：DeepSeek Harness 桌面账号，通过独立本地桥接调用；不导出账号凭据。',
           '正式来源凭证、私有测试与官方成绩需赛事平台执行，本次不产生官方分数。','',
           '| 检查项 | 类别 | 级别 | 结果 | 说明 |','|---|---|---|---|---|']
    for x in rec.rows:
        reason=(x.get('error') or '').replace('|','/').replace('\n',' ')[:350]
        lines.append('| '+ ' | '.join([x['id'],x['group'],x['level'],x['status'],reason])+' |')
    lines+=['','详细输入、响应、容器日志、生成源码、Python3.9断网执行输出、Harness调用事件均在本次目录。',
            '测试未修改目标镜像；函数级测试在独立测试引用中解除 SDK 装饰器，HTTP 用例另行验证实际 Runtime。',
            '初次开发探针日志不属于本次最终计数；以 checks.json 和 summary.json 为准。']
    (rec.root/'验收报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return summary


def main():
    global IMAGE
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',default=IMAGE,help='Explicit image tag under test, preserved in reports')
    parser.add_argument('--run-id',default='acceptance-'+datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S'))
    parser.add_argument('--offline-only',action='store_true')
    parser.add_argument('--allow-model-data',action='store_true',help='Explicit authorization to send the documented public test data to Harness models')
    parser.add_argument('--live-only',action='store_true',help='Reuse checks.json in the same run directory and execute the approved model phase')
    args=parser.parse_args()
    require(re.fullmatch(r'[A-Za-z0-9_./:-]+',args.image) is not None,'Unsafe image reference')
    IMAGE=args.image
    require(re.fullmatch(r'[A-Za-z0-9_.-]+',args.run_id) is not None,'Unsafe run-id')
    root=HERE/'runs'/args.run_id;root.mkdir(parents=True,exist_ok=True)
    rec=Recorder(root)
    if args.live_only and (root/'checks.json').is_file():
        rec.rows=json.loads((root/'checks.json').read_text(encoding='utf-8'))
        rec.rows=[x for x in rec.rows if x['id']!='live.real_model_tests']
    try:
        if not args.live_only:
            inventory(rec);offline(rec);http_checks(rec)
        if args.allow_model_data and not args.offline_only:
            live(rec)
            rec.test('agent.harness_result_review','LLM-orchestration',lambda:review(rec),level='quality')
        else:rec.add('live.real_model_tests','LLM-orchestration','BLOCKED',error='External model data authorization not enabled; only local fixtures executed')
    except Exception as exc:
        rec.add('runner.environment','test-environment','BLOCKED',error=type(exc).__name__+': '+str(exc)[:1800])
        (root/'runner-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
    finally:
        summary=report(rec)
        print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
