"""Independent second-container replay of accepted generated public sources."""
import argparse
import hashlib
import copy
import json
from pathlib import Path
from test_agent import HERE,MATERIAL,command,require,write_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-id',default='acceptance-20261022-verified')
    args=parser.parse_args()
    root=HERE/'runs'/args.run_id
    require(root.resolve().is_relative_to((HERE/'runs').resolve()),'Unsafe run path')
    checks=json.loads((root/'checks.json').read_text(encoding='utf-8'))
    results=[]
    for cid in ['D-01','D-02','D-05']:
        row={'id':'replay.'+cid,'group':'D-reproducibility','status':'BLOCKED'}
        try:
            match=next((x for x in checks if x['id']=='live.'+cid+'.model_source_generation' and x['status']=='PASS'),None)
            require(match is not None,'No accepted source available')
            source=Path(match['evidence']['source'])
            bases=sorted((root/'public-workers'/cid).glob(cid+'-*'))
            base=next((p for p in bases if (p/'result.json').is_file()),None)
            require(base is not None,'No first public result available')
            request=(base/'request.json').read_bytes()
            folder=root/'reproducibility'/cid;folder.mkdir(parents=True,exist_ok=True)
            p=command(['docker','run','--rm','--init','-i','--pull=never','--platform','linux/amd64',
                '--network','none','--read-only','--user','65534:65534','--cap-drop','ALL',
                '--security-opt','no-new-privileges','--cpus','1','--memory','1024m','--pids-limit','64',
                '--ulimit','cpu=20:20','-e','PYTHONHASHSEED=42','-e','PYTHONDONTWRITEBYTECODE=1',
                '--mount',f'type=bind,source={MATERIAL/cid},target=/experiment,readonly',
                '--mount',f'type=bind,source={source},target=/experiment/implementation.py,readonly',
                '--workdir','/experiment','--entrypoint','python','python:3.9-slim','-B','-c',
                "import signal,runpy; signal.alarm(20); runpy.run_path('worker.py',run_name='__main__')"],timeout=30,input_bytes=request)
            (folder/'stdout.txt').write_bytes(p.stdout);(folder/'stderr.txt').write_bytes(p.stderr)
            require(p.returncode==0,'Second independent container exited with '+str(p.returncode))
            actual=json.loads(p.stdout.decode('utf-8'))
            expected=json.loads((base/'result.json').read_text(encoding='utf-8'))
            equal=actual==expected
            status='PASS'
            if not equal and cid=='D-01':
                left,right=copy.deepcopy(actual),copy.deepcopy(expected)
                old_time=right.get('details',{}).pop('elapsed_seconds',None)
                new_time=left.get('details',{}).pop('elapsed_seconds',None)
                require(left==right,'Predictions/model differ, beyond an optional self-reported timing field')
                status='REVIEW'
                row.update(raw_equal=False,semantic_equal=True,volatile_field='details.elapsed_seconds',
                           values=[old_time,new_time],note='Core predictions and model are identical; whether extra timing affects official reproducibility requires platform confirmation')
            else:require(equal,'Same source and public input produced different results')
            row.update(status=status,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                       first_result=str(base/'result.json'),replay=str(folder/'stdout.txt'),
                       python='3.9',network='none',official_provenance=False)
        except Exception as exc:
            row['error']=type(exc).__name__+': '+str(exc)
            if not row['error'].endswith(('No accepted source available','No first public result available')):row['status']='FAIL'
        results.append(row)
    write_json(root/'reproducibility.json',results)
    print(json.dumps(results,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
