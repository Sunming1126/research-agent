"""核对 E 修订镜像只增加 E 流程并保留已验证的基底层。"""
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
    expected={p.relative_to(ROOT/'src').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (ROOT/'src').rglob('*.py') if 'd_algorithms' not in p.parts}
    assert all(actual.get(k)==v for k,v in expected.items())
    extras={k:v for k,v in actual.items() if k not in expected}
    assert len(extras)==4 and all(original.get(k)==v for k,v in extras.items())
    changed=sorted(k for k in original if original[k]!=actual.get(k))
    added=sorted(set(actual)-set(original))
    assert set(changed)=={'research_agent/workflows/reports.py','research_agent/integrations/idea_prompt.py'}
    assert added==['research_agent/workflows/idea_workflow.py']
    assert not any('d_algorithms' in k for k in actual)
    assert all(base['Config'].get(k)==image['Config'].get(k) for k in ('Env','Cmd','Entrypoint','User','WorkingDir'))
    proof=dict(base_image=args.base,base_image_id=base['Id'],image=args.image,image_id=image['Id'],
        base_layers_preserved=True,changed_existing=changed,new_files=added,unchanged_existing_count=len(original)-len(changed),
        source_matches_workspace=True,source_hashes=actual,sdk_adapter_files_unchanged=extras,
        runtime_config_unchanged=True,no_prebuilt_d_algorithms=True)
    path=ROOT/'output/e4-20261031/image-proof.json';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in proof.items() if k not in ('source_hashes','sdk_adapter_files_unchanged')}))

if __name__=='__main__':main()
