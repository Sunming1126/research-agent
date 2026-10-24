"""Prepare immutable upload files only; never calls a model or submits a contest."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import zipfile

import test_agent as T
from harness_bridge import write_json

ROOT=Path(__file__).resolve().parent.parent
REPO=ROOT/'协同开发/research-agent'
DELIVERY=ROOT/'镜像测试/交付/0.4.2-hybrid-v6'
IMAGE='research-assistant:0.4.2-hybrid-v6'
COMMIT='094d622'

def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()

def main():
    DELIVERY.mkdir(parents=True,exist_ok=True)
    info=json.loads(T.docker('image','inspect',IMAGE))[0]
    config=info['Config'];revision=config['Labels']['org.opencontainers.image.revision']
    assert revision.startswith(COMMIT) and config['Labels']['org.agenthack.version']=='0.4.2-hybrid-v6'
    assert info['Os']=='linux' and info['Architecture']=='amd64'
    assert config['User'] not in ['', 'root', '0'] and '8080/tcp' in config['ExposedPorts']
    env=dict(v.split('=',1) for v in config.get('Env',[]))
    base=json.loads(T.docker('image','inspect','research-assistant:0.4.1-source-v1'))[0]
    base_env=dict(v.split('=',1) for v in base['Config'].get('Env',[]))
    assert env.get('GPG_KEY')==base_env.get('GPG_KEY') and re.fullmatch('[0-9A-Fa-f]{40,64}',env.get('GPG_KEY',''))
    sensitive={k:bool(v) for k,v in env.items() if k!='GPG_KEY' and re.search('KEY|TOKEN|SECRET|PASSWORD',k,re.I)}
    assert not any(sensitive.values())
    snapshot=DELIVERY/'build-source'
    files=[REPO/n for n in ['agent.py','requirements.txt','Dockerfile','.dockerignore','README.md']]
    files += [p for p in (REPO/'src').rglob('*.py') if 'd_algorithms' not in p.parts and '__pycache__' not in p.parts]
    for path in files:
        destination=snapshot/path.relative_to(REPO)
        if destination.exists() and destination.read_bytes()!=path.read_bytes():
            raise RuntimeError('Refusing to overwrite an immutable build snapshot')
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,destination)
    source=DELIVERY/'research-agent-0.4.2-hybrid-v6-build-source.zip'
    if not source.exists():
        with zipfile.ZipFile(source,'w',zipfile.ZIP_DEFLATED) as z:
            for path in sorted(snapshot.rglob('*')):
                if path.is_file():z.write(path,path.relative_to(snapshot).as_posix())
    tar=DELIVERY/'research-assistant-0.4.2-hybrid-v6.tar'
    if not tar.exists():T.docker('save','-o',tar,IMAGE,timeout=120)
    assert tar.stat().st_size<2*1024**3
    assert json.loads(T.docker('image','inspect',IMAGE))[0]['Id']==info['Id']
    manifest=dict(version='0.4.2-hybrid-v6',image=IMAGE,image_id=info['Id'],build_commit=revision,
        status='ARCHIVE_PREPARED_PENDING_NATIVE_HARNESS_ACCEPTANCE',
        ready_for_recommended_contest_submission=False,uploaded_to_contest=False,official_score=None,
        platform='linux/amd64',user=config['User'],port=8080,nonempty_credential_env=sensitive,
        source_snapshot_files=len(files),files=[dict(file=p.name,bytes=p.stat().st_size,sha256=digest(p)) for p in [tar,source]])
    write_json(DELIVERY/'delivery-manifest.json',manifest)
    (DELIVERY/'SHA256SUMS.txt').write_text(''.join(f"{p['sha256']}  {p['file']}\n" for p in manifest['files']),encoding='ascii')
    print(json.dumps(manifest,ensure_ascii=True),flush=True)

if __name__=='__main__':main()
