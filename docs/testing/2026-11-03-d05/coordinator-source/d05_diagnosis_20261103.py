"""Original-Harness-only D05 diagnosis, without touching production sources."""
import hashlib,json,os,random,shutil,subprocess,sys,time,uuid
from pathlib import Path
from score_target_e2e_20261102 import ROOT,HERE,REPO,SID,save,freeze
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def validate(directory):
 assert os.environ.get('ANIC_NATIVE_ORCHESTRATOR_SESSION_ID')==SID
 from research_agent.workflows.sample_validation import validate_sample_output
 from research_agent.workflows.d_public_review import review_public_result
 plan=json.loads((directory/'inputs/inputs.json').read_text(encoding='utf-8'))
 cases={c['name']:c for c in plan['cases']};rows=json.loads((directory/'output/worker-results.json').read_text(encoding='utf-8'))
 for row in rows:
  base=directory/'output'/row['source']/row['case'];request=json.loads((directory/'inputs'/cases[row['case']]['file']).read_text(encoding='utf-8'))
  try:
   body=json.loads((base/'stdout.json').read_text(encoding='utf-8'))
   if row['returncode']:raise RuntimeError(str(body))
   row['isolation']=body['isolation']
   validate_sample_output('D-05',request,body['result']);review_public_result('D-05',request,body['result'])
   row['status']='passed_public_contract_and_replay'
  except Exception as e:row.update(status='failed',error_type=type(e).__name__,error=str(e)[:2200])
  row['input_sha256']=cases[row['case']]['sha256'];save(base/'validation.json',row)
 save(directory/'summary.json',{'rows':rows,'model_calls':0,'official_score':None,'platform_acceptance':False,
   'scope':'Saved real model source, public or synthetic inputs, Linux replay; not formal current case',
   'counts':{s['name']:{'passed':sum(r['source']==s['name'] and r['status'].startswith('passed') for r in rows),
    'failed':sum(r['source']==s['name'] and r['status']=='failed' for r in rows)} for s in plan['sources']}})

def run(proof):
 assert proof['native_tool_execution_verified'] and proof['session_id']==SID
 directory=HERE/'runs'/('d05-diagnosis-20261103-'+time.strftime('%H%M%S')+'-'+uuid.uuid4().hex[:5]);directory.mkdir()
 save(directory/'native-proof.json',proof);save(directory/'owned-process.json',{'pid':os.getpid()})
 save(ROOT/'.session-audit/d05-diagnosis-latest-20261103.json',{'directory':str(directory)})
 inputs=directory/'inputs';inputs.mkdir();(directory/'output').mkdir()
 ref=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
 files=freeze(ref,directory/'source');source=directory/'source'
 shutil.copyfile(source/'src/research_agent/workflows/sample_worker.py',inputs/'sample_worker.py')
 shutil.copyfile(HERE/'d05_linux_replay_worker_20261103.py',inputs/'worker.py')
 material=REPO/'科研助手赛题材料/research-experiments-20261020-source-v1'
 protocol=json.loads((material/'D-05/protocol.json').read_text(encoding='utf-8'))
 cases=[]
 def add(name,request,category):
  request=dict(request,protocol=protocol);p=inputs/(name+'.json');save(p,request)
  cases.append({'name':name,'file':p.name,'category':category,'sha256':sha(p)})
 add('sample',json.loads((material/'D-05/sample.json').read_text(encoding='utf-8')),'public_sample')
 for i,p in enumerate(sorted((material/'公开样例请求/D-05').glob('*.json'))):
  v=json.loads(p.read_text(encoding='utf-8'));add('public-'+str(i),v.get('payload',v),'public_boundary')
 rng=random.Random(42)
 for i in range(16):
  jobs=[]
  for j in range(rng.randint(0,80)):
   a=rng.randint(0,24)/2;jobs.append(dict(id=f'j{j:04}',flow=rng.choice(['A','B','C']),arrival_ms=a,
    service_ms=rng.choice([.25,.5,1,2,3]),deadline_ms=a+rng.choice([.25,1,3,10,100])))
  data=dict(jobs=jobs,flow_weights={'A':.5,'B':2,'C':1,'idle':3},servers=rng.choice([1,2,3]),
    setup_ms=rng.choice([0,.25,1]),horizon_ms=rng.choice([4,8,20,50]))
  add('seed42-'+str(i),dict(case_id='D-05',phase='run',data=data),'synthetic_method_combinations')
 for n in (256,1024,2048):
  jobs=[dict(id=f'j{j:05}',flow=['A','B','C'][j%3],arrival_ms=0,service_ms=1+(j%3)/4,deadline_ms=n*2) for j in range(n)]
  add('scale-'+str(n),dict(case_id='D-05',phase='run',data=dict(jobs=jobs,flow_weights={'A':.5,'B':2,'C':1,'idle':3},servers=3,setup_ms=.25,horizon_ms=n*2)),'synthetic_scale_not_formal_size')
 extension=json.loads((inputs/'sample.json').read_text(encoding='utf-8'))
 for j in extension['data']['jobs']:j.update(opaque_tag='public-probe',metadata={'unchanged':True})
 add('opaque-fields-probe',extension,'extension_probe_not_known_formal_contract')
 models=[]
 old=HERE/'runs/score-target-e2e-20261102-214711-699f1'
 for label,job in [('saved-A','D-05-baseline'),('saved-B','D-05-candidate')]:
  p=old/job/'output/experiment/implementation.py';name=label+'.py';shutil.copyfile(p,inputs/name)
  models.append({'name':label,'file':name,'sha256':sha(p),'origin':str(p.relative_to(ROOT))})
 plan={'source_commit':ref,'source_file_sha256':files,'materials_sha256':{p.name:sha(p) for p in (material/'D-05').iterdir() if p.name in ('sample.json','method.md','protocol.json')},
       'sources':models,'cases':cases,'seed':42,'model_calls':0,'official_score':None}
 save(inputs/'inputs.json',plan)
 image='python:3.9-slim';inspect=subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True).strip()
 save(directory/'environment-plan.json',{'image':image,'image_id':inspect,'cpus':1,'memory':'1g','network':'none','root_filesystem':'read_only','source_commit':ref})
 command=['docker','run','--rm','--name','anic-d05-replay-'+uuid.uuid4().hex[:8],'--network','none','--read-only','--cpus','1','--memory','1g','--pids-limit','64','--cap-drop','ALL','--security-opt','no-new-privileges',
   '--tmpfs','/tmp:rw,nosuid,nodev,size=32m','--mount','type=bind,source='+str(inputs)+',target=/input,readonly',
   '--mount','type=bind,source='+str(directory/'output')+',target=/output',image,'python','-B','-X','utf8','/input/worker.py']
 with (directory/'docker.stdout.txt').open('wb') as out,(directory/'docker.stderr.txt').open('wb') as err:
  p=subprocess.run(command,stdout=out,stderr=err,timeout=900)
 if p.returncode:raise RuntimeError('Linux replay failed; see retained Docker logs')
 paths=[str(source/'src'),str(HERE/'runtime/py312-windows'),str(HERE)]
 boot='import sys,runpy;sys.path[:0]='+repr(paths)+';runpy.run_path('+repr(str(Path(__file__).resolve()))+',run_name="__main__")'
 env=os.environ.copy();env['ANIC_NATIVE_ORCHESTRATOR_SESSION_ID']=SID
 result=subprocess.run([str(HERE/'runtime/windows-python312/python.exe'),'-X','utf8','-c',boot,'--validate-dir',str(directory)],env=env,capture_output=True,timeout=90)
 (directory/'validation.stderr.txt').write_bytes(result.stderr)
 if result.returncode:raise RuntimeError('Replay output validation failed; raw outputs retained')
 summary=json.loads((directory/'summary.json').read_text(encoding='utf-8'))
 print(json.dumps({'directory':str(directory),'counts':summary['counts'],'official_score':None,'model_calls':0},ensure_ascii=True),flush=True)

if __name__=='__main__':
 if len(sys.argv)==3 and sys.argv[1]=='--validate-dir':validate(Path(sys.argv[2]))
 else:raise RuntimeError('Run only through the verified original Harness coordinator')
