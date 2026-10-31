"""Run current model source through unmodified public workers on Python 3.9."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def inputs(case_id, request):
    yield 'public_sample', request
    other = copy.deepcopy(request)
    if case_id == 'D-01':
        other['data']['texts'] += ['', 'UNSEEN_TOKEN_9841', 'repeat repeat !!!']
        yield 'empty_unknown_repeated_text', other
        other = copy.deepcopy(request)
        other['data']['train'] = list(reversed(other['data']['train']))
        yield 'training_order', other
    elif case_id == 'D-02':
        other['data']['test_queries'] += [{'id':'unknown_query','text':'UNKNOWN_TOKEN_9841','group':'topic'}]
        yield 'unknown_query', other
        other = copy.deepcopy(request)
        first = other['data']['dev_queries'][0]
        other['data']['dev_queries'] += [dict(first,id='extra_dev_'+str(i)) for i in range(3)]
        yield 'unequal_group_sizes', other
        other = copy.deepcopy(request)
        for query in other['data']['test_queries']:
            query['text'] += ' ' + query['text']
        yield 'repeated_query_words', other
        for field in ('title','text'):
            other = copy.deepcopy(request)
            for document in other['data']['documents']:
                document[field] = ''
            yield 'all_empty_'+field, other
        other = copy.deepcopy(request)
        other['data']['documents'] = [dict(d,id=d['id']+'_'+str(i),text=d['text']*4)
                                      for i in range(4) for d in request['data']['documents']]
        for query in other['data']['dev_queries']:
            query['relevant'] = [rid+'_0' for rid in query['relevant']]
        yield 'larger_public_corpus', other
    else:
        other['data']['jobs'] = []
        yield 'empty_jobs', other
        for servers in (1,2,3):
            other = copy.deepcopy(request)
            other['data']['servers'] = servers
            for job in other['data']['jobs']:
                job['arrival_ms']=0
            yield 'simultaneous_servers_'+str(servers), other
        for name,data in [
            ('rejected_then_short', {'servers':1,'setup_ms':0,'horizon_ms':5,'flow_weights':{'A':1},'jobs':[
                {'id':'a','flow':'A','arrival_ms':0,'service_ms':6,'deadline_ms':20},
                {'id':'b','flow':'A','arrival_ms':0,'service_ms':1,'deadline_ms':20}]}),
            ('exact_window', {'servers':1,'setup_ms':0,'horizon_ms':5,'flow_weights':{'A':1},'jobs':[
                {'id':'a','flow':'A','arrival_ms':0,'service_ms':5,'deadline_ms':20}]}),
        ]:
            yield name, dict(request,data=data)


def main():
    root=Path('/materials'); live=Path('/live'); output=Path('/results'); rows=[]
    for case_id in ('D-01','D-02','D-05'):
        work=Path('/tmp')/case_id;shutil.copytree(root/case_id,work)
        shutil.copyfile(live/case_id/'implementation.py',work/'implementation.py')
        request=json.loads((work/'sample.json').read_text())
        request['protocol']=json.loads((work/'protocol.json').read_text())
        for name,payload in inputs(case_id,request):
            message={'stage':'run','payload':payload}
            proc=subprocess.run([sys.executable,str(work/'worker.py')],input=json.dumps(message),
                capture_output=True,text=True,timeout=20,cwd=work)
            evidence={'case_id':case_id,'scenario':name,'request':payload,'returncode':proc.returncode,
                      'worker_sha256':hashlib.sha256((work/'worker.py').read_bytes()).hexdigest(),
                      'source_sha256':hashlib.sha256((work/'implementation.py').read_bytes()).hexdigest(),
                      'python':sys.version,'stderr':proc.stderr}
            evidence['result']=json.loads(proc.stdout) if proc.returncode==0 else None
            if proc.returncode==0 and case_id=='D-02':
                selected=evidence['result']['details'].get('selected',payload['protocol']['default_config'])
                ranking=dict(payload,phase='rank',data=dict(payload['data'],selected=selected))
                follow=subprocess.run([sys.executable,str(work/'worker.py')],input=json.dumps({'stage':'rank','payload':ranking}),capture_output=True,text=True,timeout=20,cwd=work)
                evidence['rank_returncode']=follow.returncode
                evidence['rank_result']=json.loads(follow.stdout) if follow.returncode==0 else None
            if proc.returncode==0 and case_id=='D-02' and name=='public_sample':
                evidence['benchmarks'] = []
                for kind,config in [('baseline',payload['protocol']['default_config']),('candidate',selected)]:
                    for repeat in range(1,4):
                        import time
                        bench = dict(payload,phase='rank',data=dict(payload['data'],selected=config,dev_queries=[]))
                        started = time.monotonic()
                        measured = subprocess.run([sys.executable,str(work/'worker.py')],
                            input=json.dumps({'stage':f'benchmark_{kind}_{repeat}',
                                              'payload':{'request':bench,'calls':payload['protocol']['timing_calls']}}),
                            capture_output=True,text=True,timeout=payload['protocol']['resources']['stage_timeout_seconds'],cwd=work)
                        assert measured.returncode==0,measured.stderr
                        value = json.loads(measured.stdout)
                        assert value['completed_queries']==len(bench['data']['test_queries'])*payload['protocol']['timing_calls']
                        evidence['benchmarks'].append(dict(kind=kind,repeat=repeat,seconds=time.monotonic()-started,**value))
                for kind in ('baseline','candidate'):
                    assert len({r['rankings_sha256'] for r in evidence['benchmarks'] if r['kind']==kind})==1
            target=output/(case_id+'-'+name+'.json');target.write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
            rows.append({'case_id':case_id,'scenario':name,'returncode':proc.returncode})
    (output/'worker-summary.json').write_text(json.dumps(rows,indent=2))
    assert all(row['returncode']==0 for row in rows),rows
    print('Official Python 3.9 worker cases:',len(rows))


if __name__=='__main__':main()
