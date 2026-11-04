"""Linux replay of already saved model sources; no LLM or platform credentials."""
import json,os,platform,subprocess,sys,time
from pathlib import Path
inputs=Path('/input');output=Path('/output');plan=json.loads((inputs/'inputs.json').read_text())
rows=[]
for source in plan['sources']:
 code=(inputs/source['file']).read_text()
 for case in plan['cases']:
  folder=output/source['name']/case['name'];folder.mkdir(parents=True,exist_ok=True)
  request=json.loads((inputs/case['file']).read_text())
  started=time.monotonic()
  try:
   p=subprocess.run([sys.executable,'-I','-S','-B','-X','utf8',str(inputs/'sample_worker.py')],
    input=json.dumps({'source':code,'request':request,'seconds':20}),text=True,capture_output=True,
    timeout=25,env={'PATH':os.environ.get('PATH',''),'PYTHONIOENCODING':'utf-8'})
   row={'returncode':p.returncode,'stdout_bytes':len(p.stdout.encode()),'stderr_bytes':len(p.stderr.encode())}
   (folder/'stdout.json').write_text(p.stdout);(folder/'stderr.txt').write_text(p.stderr)
  except subprocess.TimeoutExpired as e:
   row={'returncode':124,'error':'wall timeout 25s'}
   (folder/'stdout.json').write_text((e.stdout or b'').decode() if isinstance(e.stdout,bytes) else e.stdout or '')
   (folder/'stderr.txt').write_text((e.stderr or b'').decode() if isinstance(e.stderr,bytes) else e.stderr or '')
  row.update(source=source['name'],case=case['name'],category=case['category'],seconds=round(time.monotonic()-started,4))
  rows.append(row);(output/'worker-results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
(output/'environment.json').write_text(json.dumps({'python':sys.version,'platform':platform.platform(),'model_calls':0},indent=2))
