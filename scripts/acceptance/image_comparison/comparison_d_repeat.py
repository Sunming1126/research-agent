"""Independent second D-generation round; same frozen cases and unchanged D test body."""
import argparse,json,os
from pathlib import Path
import test_agent as T
from compare_images import BASE,IMAGES,RUN,metadata,frozen_inputs
from harness_bridge import HarnessBridge,write_json
import time,re,hashlib

def run_d(rec,inputs):
    with HarnessBridge(rec.root/'harness-model-calls') as bridge:
        for cid in ['D-01', 'D-02', 'D-05']:
            source_box = {}
            def generate(cid=cid):
                directory = rec.root / ('live-' + cid); task = inputs['tasks'][cid]
                before = len(bridge.records)
                with T.target(directory, bridge=bridge, case_id=cid) as (base, output, name):
                    started = time.monotonic(); code, body = T.http(base + '/solve', task)
                    elapsed = time.monotonic() - started
                    write_json(directory / 'response.json', {'http': code, 'seconds': elapsed, 'response': body})
                    result = T.unpack_answer(body); T.independent_contract(task, result, output)
                    sources = list((output / 'data/workspace_team/work' / cid).glob('*/implementation.py'))
                    T.require(len(sources) == 1, 'No unique generated source')
                    source = sources[0]
                    generation = json.loads((source.parent / 'generation-response.json').read_text(encoding='utf-8'))
                    content = generation['choices'][0]['message']['content']
                    normalized = source.read_text(encoding='utf-8').replace('\r\n', '\n').rstrip('\n')
                    candidates = [content, *re.findall(r'^```(?:python|py)?[ \t]*\r?\n(.*?)^```[ \t]*$', content, re.M | re.S)]
                    T.require(any(c.replace('\r\n', '\n').replace('\r', '\n').rstrip('\n') == normalized for c in candidates), 'Source changed beyond newline normalization')
                    calls = bridge.records[before:]
                    T.require(calls, 'No actual current-case model call')
                    T.require(elapsed <= 180, 'Case exceeded 180 seconds')
                    T.require(result['status'] != 'completed', 'D falsely completed without formal service')
                    history_file = source.parent / 'public-selftest.json'
                    if history_file.exists():
                        history = json.loads(history_file.read_text(encoding='utf-8'))
                        T.require(history.get('attempts') and history['attempts'][-1].get('status') not in ['failed', 'error'], 'Final internal self-test failed; source is rejected')
                    source_box['path'] = source
                    return {'case_id': cid, 'source': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                            'seconds': elapsed, 'real_model_calls': len(calls), 'official_execution': 'BLOCKED'}
            rec.test('live.' + cid + '.model_source_generation', 'D-source-live', generate)
            if 'path' in source_box:
                T.public_samples(rec, cid, source_box['path'])
            else:
                rec.add('public_algorithm.' + cid, 'D-public-algorithm', 'BLOCKED', error='No accepted source from this run')

def main():
    p=argparse.ArgumentParser();p.add_argument('--version',required=True,choices=IMAGES);a=p.parse_args()
    os.environ['TEST_HARNESS_REASONING']='off'
    root=T.HERE/'runs'/(RUN+'-'+a.version+'-d-repeat');root.mkdir(parents=True,exist_ok=False)
    rec=T.Recorder(root);identity=metadata(rec,IMAGES[a.version])
    run_d(rec,frozen_inputs())
    done=T.command(['python',str(T.HERE/'check_reproducibility.py'),'--run-id',root.name],timeout=120)
    (root/'reproducibility-stdout.txt').write_bytes(done.stdout+done.stderr)
    if done.returncode:raise RuntimeError('Independent replay runner failed')
    for row in json.loads((root/'reproducibility.json').read_text(encoding='utf-8')):
        rec.add(row['id'],row['group'],row['status'],evidence=row,error=row.get('error'),level='quality')
    write_json(root/'summary.json',{'target':IMAGES[a.version],'id':identity,'official_score':None,'independent_generation_repeat':True})

if __name__=='__main__':main()
