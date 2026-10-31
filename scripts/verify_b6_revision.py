"""核对 B6 镜像的四个专用文件和两个受类别保护的入口，其余字节保持 E4。"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, encoding='utf-8')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--image',required=True)
    parser.add_argument('--base',required=True)
    args=parser.parse_args()
    base=json.loads(docker('image','inspect',args.base))[0]
    image=json.loads(docker('image','inspect',args.image))[0]
    assert image['RootFS']['Layers'][:len(base['RootFS']['Layers'])]==base['RootFS']['Layers']
    code="import json,hashlib;from pathlib import Path;r=Path('/app/src');print(json.dumps({str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest() for p in r.rglob('*.py')}))"
    def sources(tag):
        return json.loads(docker('run','--rm','--network','none','--entrypoint','python',tag,'-c',code))
    original,actual=sources(args.base),sources(args.image)
    changed=sorted(k for k in original if original[k]!=actual.get(k))
    added=sorted(set(actual)-set(original))
    assert set(changed)=={'research_agent/workflows/tasks.py','research_agent/integrations/prompts.py'}
    assert set(added)=={'research_agent/workflows/b_conditions.py','research_agent/workflows/b_evidence.py','research_agent/workflows/b_workflow.py','research_agent/integrations/b_prompt.py'}
    for name in changed+added:
        assert actual[name]==hashlib.sha256((ROOT/'src'/name).read_bytes()).hexdigest(),name
    extras={k:v for k,v in actual.items() if not (ROOT/'src'/k).exists()}
    assert len(extras)==4 and all(original.get(k)==v for k,v in extras.items())
    assert not any('d_algorithms' in k for k in actual)
    assert all(base['Config'].get(k)==image['Config'].get(k) for k in ('Env','Cmd','Entrypoint','User','WorkingDir'))
    proof=dict(base_image=args.base,base_image_id=base['Id'],image=args.image,image_id=image['Id'],
        base_layers_preserved=True,changed_existing=changed,new_files=added,unchanged_existing_count=len(original)-len(changed),
        source_matches_workspace=True,source_hashes=actual,sdk_adapter_files_unchanged=extras,
        runtime_config_unchanged=True,no_prebuilt_d_algorithms=True)
    path=ROOT/'output/b6-20261031/image-proof.json';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in proof.items() if k not in ('source_hashes','sdk_adapter_files_unchanged')}))

if __name__=='__main__':main()
