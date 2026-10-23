"""Run an archived/generated source against public Python 3.9 inputs, offline.

Reference checks run in the coordinator, never in the Agent image or model prompt.
This tool does not produce official source credentials or scores.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import uuid

from public_oracles import verify_public


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=('D-01','D-02','D-05'), required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--materials', type=Path, default=root/'科研助手赛题材料/x新/research-experiments-20261020-source-v1')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    source, materials = args.source.resolve(), args.materials.resolve()
    if not source.is_file() or not 0 < source.stat().st_size <= 65536:
        parser.error('source must be an existing Python file of 1..65536 bytes')
    output = args.output or root/'output/acceptance'/f'{args.case}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}'
    output.mkdir(parents=True, exist_ok=False)
    samples = materials/'公开样例请求'
    requests = [samples/(args.case+'.json'), *sorted((samples/args.case).glob('*.json'))]
    checks, selected = [], None
    for index, path in enumerate(requests):
        name = path.relative_to(samples).as_posix()
        folder = output/f'{index:02d}'
        folder.mkdir()
        request = json.loads(path.read_text(encoding='utf-8-sig'))
        if request['payload']['phase']=='rank' and selected is not None:
            request['payload']['data']['selected']=selected
        (folder/'request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf-8')
        command = ['docker','run','--rm','--init','-i','--pull=never','--platform','linux/amd64',
            '--network','none','--read-only','--user','65534:65534','--cap-drop','ALL',
            '--security-opt','no-new-privileges','--cpus','1','--memory','1024m','--pids-limit','64',
            '--ulimit','cpu=20:20','-e','PYTHONHASHSEED=42','-e','PYTHONDONTWRITEBYTECODE=1',
            '--mount',f'type=bind,source={materials/args.case},target=/experiment,readonly',
            '--mount',f'type=bind,source={source},target=/experiment/implementation.py,readonly',
            '--workdir','/experiment','--entrypoint','python','python:3.9-slim','-B','-c',
            "import signal,runpy;signal.alarm(20);runpy.run_path('worker.py',run_name='__main__')"]
        try:
            process = subprocess.run(command,input=json.dumps(request,ensure_ascii=False).encode('utf-8'),capture_output=True,timeout=30)
            (folder/'stdout.txt').write_bytes(process.stdout)
            (folder/'stderr.txt').write_bytes(process.stderr)
            if process.returncode:
                raise RuntimeError(f'worker exit {process.returncode}: '+process.stderr.decode('utf-8',errors='replace')[-1000:])
            result = json.loads(process.stdout)
            if 'result' in result and 'baseline' not in result:
                result=result['result']
            evidence=verify_public(request['payload'],result)
            if args.case=='D-02' and request['payload']['phase']=='run':
                selected=result.get('details',{}).get('selected')
            checks.append({'request':name,'status':'PASS','evidence':evidence})
        except (subprocess.TimeoutExpired,RuntimeError,ValueError,AssertionError,KeyError) as exc:
            checks.append({'request':name,'status':'FAIL','error':type(exc).__name__+': '+str(exc)[:1400]})
        print(json.dumps(checks[-1],ensure_ascii=True),flush=True)
    summary={'case_id':args.case,'checks':checks,'passed':sum(x['status']=='PASS' for x in checks),
             'failed':sum(x['status']=='FAIL' for x in checks),'official_provenance':False,'official_score':None}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return 1 if summary['failed'] else 0


if __name__=='__main__':
    sys.exit(main())
