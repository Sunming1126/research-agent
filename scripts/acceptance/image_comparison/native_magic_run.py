"""Entry point invoked only by a native Harness tool in the required workspace."""
from collections import Counter
from compression import zstd
from pathlib import Path
import argparse
import hashlib
import json
import os
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent

def allocate_run(stage):
    """Never reuse or erase a partially created attempt after an early failure."""
    base='hybrid-20261023-v042-v4-magic-native-api'
    record=ROOT/'.session-audit/native-v4-latest-run.json'
    if stage=='d-repeat':
        saved=json.loads(record.read_text(encoding='utf-8'))
        run=saved['run']
        if not (HERE/'runs'/run/'summary.json').is_file():
            raise RuntimeError('The main round must finish before an independent repeat')
        if (HERE/'runs'/(run+'-d-repeat')).exists():
            raise RuntimeError('Independent repeat already exists; inspect its evidence before another generation')
        return run
    index=1
    while True:
        run=base if index==1 else base+'-r'+str(index)
        if not (HERE/'runs'/run).exists():break
        index+=1
    record.write_text(json.dumps(dict(run=run,stage=stage,prior_attempts_preserved=index-1),indent=2),encoding='utf-8')
    return run

def native_launch_proof(stage):
    request=json.loads((ROOT/'.session-audit/native-api-workflow-request.json').read_text(encoding='utf-8'))
    assert request['stage']==stage
    state=json.loads((Path.home()/'.dsh/storages/workspace.json').read_text(encoding='utf-8'))
    record=state['tables']['workspaces'][request['workspace_id']]
    assert Path(record['path']).resolve()==ROOT.resolve() and request['session_id'] in record['sessionIds']
    files=list((Path.home()/'.dsh/sessions').glob('*/'+request['session_id']+'/session.v4.jsonl.zstd'))
    tool=None
    for _ in range(30):
        if files:
            rows=[json.loads(line) for line in zstd.decompress(files[0].read_bytes()).decode('utf-8').splitlines()]
            for row in rows:
                if row.get('type')!='assistant/message':continue
                for part in row.get('data',{}).get('message',{}).get('content',[]):
                    if part.get('type')!='tool-call':continue
                    value=json.dumps(part,ensure_ascii=False).replace('\\\\','/').replace('\\','/')
                    if 'native_magic_run.py' in value and 'Agent挑战赛' in value and '--stage '+stage in value:
                        tool=part;break
        if tool:break
        time.sleep(.2)
    if not tool:raise RuntimeError('No native Harness tool invocation found; model testing not started')
    os.environ['ANIC_NATIVE_ORCHESTRATOR_SESSION_ID']=request['session_id']
    return dict(native_tool_execution_verified=True,workspace_id=request['workspace_id'],
                session_id=request['session_id'],tool_name=tool.get('name'),
                native_tool_call_sha256=hashlib.sha256(json.dumps(tool,sort_keys=True).encode()).hexdigest(),
                profile_modified=False,plugin_installed=False,headless_model_process_used=False)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['smoke','all','d-repeat'],required=True)
    args=parser.parse_args()
    proof=native_launch_proof(args.stage)
    os.environ['PATH']=str(Path.home()/'AppData/Local/Programs/DockerDesktop/resources/bin')+os.pathsep+os.environ.get('PATH','')
    os.environ['TEST_HARNESS_REASONING']='off'
    import magic_api_bridge as bridge
    if args.stage=='smoke':
        directory=HERE/'runs/native-magic-api-smoke-20261023'
        directory.mkdir(parents=True,exist_ok=False)
        bridge.write_json(directory/'native-orchestration-proof.json',proof)
        text,meta=bridge.call_harness('Return only {"magic_ready":true}.',directory/'model',max_tokens=64)
        assert json.loads(text)=={'magic_ready':True}
        summary=dict(status='PASS',native_orchestration=proof,model=meta['model'],
                     forwarded_max_tokens=64,thinking='disabled',actual_usage=meta['usage'])
        bridge.write_json(directory/'summary.json',summary)
        print(json.dumps(summary,ensure_ascii=False),flush=True)
        return
    import accept_hybrid as runner
    import test_agent as T
    runner.IMAGE='research-assistant:0.4.2-hybrid-v4'
    runner.RUN=allocate_run(args.stage)
    runner.C.HarnessBridge=bridge.HarnessBridge
    runner.D.HarnessBridge=bridge.HarnessBridge
    runner.T.HarnessBridge=bridge.HarnessBridge
    runner.T.call_harness=bridge.call_harness
    original_docker=T.docker
    def no_private_config(*arguments,**kwargs):
        values=list(arguments)
        if values and values[0]=='run':
            value='RESEARCH_CONFIG=/test-config.json'
            if value in values:
                index=values.index(value);assert values[index-1]=='-e';del values[index-1:index+1]
            for index in range(len(values)-1,-1,-1):
                if isinstance(values[index],str) and 'target=/test-config.json' in values[index]:
                    assert values[index-1]=='--mount';del values[index-1:index+1]
        return original_docker(*values,**kwargs)
    T.docker=no_private_config
    original_metadata=runner.C.metadata
    def metadata(rec,tag):
        identity=original_metadata(rec,tag)
        bridge.write_json(rec.root/'native-orchestration-proof.json',proof)
        bridge.write_json(rec.root/'adapter-scope.json',dict(
            backend='Existing magic API called by the native Harness workspace test job',
            credentials='Read-only existing selected connection; memory-only; never supplied to Docker',
            profile_modified=False,plugin_installed=False,headless_model_process_used=False,
            max_tokens_forwarded=True,thinking_forwarded=True,exact_api_usage_forwarded=True,
            custom_RESEARCH_CONFIG_supplied=False,public_cases_only=True,official_score=None))
        bridge.write_json(rec.root/'actual-toolkit-sha256.json',{
            n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in [
                'native_magic_run.py','magic_api_bridge.py','read_magic_connection.cjs','accept_hybrid.py',
                'compare_images.py','comparison_d_repeat.py','test_agent.py','public_oracles.py']})
        return identity
    runner.C.metadata=metadata
    import sys
    sys.argv=[sys.argv[0],'--stage',args.stage]
    runner.main()

if __name__=='__main__':main()
