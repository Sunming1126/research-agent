"""Prepare a Linux OCI/Docker archive without executing it or calling a model.

Uses the existing complete Linux dependency layers and an exact Git source
snapshot. Runtime acceptance is deliberately recorded as pending.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def filehash(path):
    h=hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def dump(path, obj):
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def serialized(obj):
    return json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode()


def add(archive, name, data, uid=0, gid=0, directory=False):
    item=tarfile.TarInfo(name)
    item.mtime=0;item.uid=uid;item.gid=gid;item.mode=0o755 if directory else 0o644
    if directory:item.type=tarfile.DIRTYPE
    else:item.size=len(data)
    archive.addfile(item,None if directory else io.BytesIO(data))


def safe(name):
    path=PurePosixPath(name)
    assert not path.is_absolute() and '..' not in path.parts, 'Unsafe archive path'
    return str(path).removeprefix('./')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--tested-source-manifest',type=Path)
    parser.add_argument('--validation-summary',type=Path)
    args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    revision=subprocess.check_output(['git','rev-parse',args.revision],cwd=args.repo,text=True).strip()
    snapshot=subprocess.check_output(['git','archive','--format=tar',revision,'--','agent.py','README.md',
                                     'requirements.txt','Dockerfile','.dockerignore','pyproject.toml','src'],cwd=args.repo)
    files={};line_ending_files=[]
    tested_path=args.tested_source_manifest or (args.repo/'docs/testing/2026-10-29-b-stability-closure/candidate-final-source-files.json')
    tested=json.loads(tested_path.read_text(encoding='utf-8'))
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
        for member in archive:
            name=safe(member.name)
            if not member.isfile() or 'd_algorithms' in PurePosixPath(name).parts or '__pycache__' in PurePosixPath(name).parts or name.endswith('.pyc'):continue
            assert not any(part.startswith('.env') for part in PurePosixPath(name).parts)
            data=archive.extractfile(member).read()
            assert not re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:sk-|ghp_|github_pat_)[A-Za-z0-9_\-]{24,}',data), 'Credential-like value; withheld'
            checked_out=(args.repo/name).read_bytes()
            if checked_out!=data:
                assert checked_out.replace(b'\r\n',b'\n')==data.replace(b'\r\n',b'\n'),'Checkout has a non-line-ending source change'
                line_ending_files.append(name)
            if name.endswith('.py'):assert tested.get(name)==digest(checked_out),'Source differs from final regression bytes'
            files[name]=checked_out
    tree=ast.parse(files['agent.py'])
    assert any(isinstance(n,ast.ImportFrom) and n.module=='agenthack' and any(a.name=='AgentRuntime' for a in n.names) for n in ast.walk(tree))
    assert b'AgentRuntime(ResearchAgent())' in files['agent.py'] and b'runtime.run()' in files['agent.py']
    assert not any(name.endswith('d_algorithms.py') for name in files)
    source_hashes={name:digest(data) for name,data in files.items()}
    image_files={name:data for name,data in files.items() if name.startswith('src/') or name in {'agent.py','README.md','requirements.txt'}}
    version=re.search(rb'AGENT_VERSION=([^\s]+)',files['Dockerfile'])[1].decode();tag='research-assistant:'+version
    archive_path=out/('research-assistant-'+version+'.tar')
    pins={line.strip().lower() for line in files['requirements.txt'].decode().splitlines() if line.strip() and not line.lstrip().startswith('#')}
    created=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    with tarfile.open(args.base) as base:
        saved=json.load(base.extractfile('manifest.json'))
        assert len(saved)==1
        old=saved[0];config=json.load(base.extractfile(old['Config']))
        assert config['os']=='linux' and config['architecture']=='amd64'
        layers=old['Layers'];diffids=config['rootfs']['diff_ids'];assert len(layers)==len(diffids)
        kept=[];audits=[];scanned={};packages={};sdk={};base_requirements=None;agent_ids=None
        for name,expected_diff in zip(layers,diffids):
            if name not in scanned:
                raw=base.extractfile(name).read();assert digest(raw)==name.rsplit('/',1)[-1]
                plain=gzip.decompress(raw) if raw.startswith(b'\x1f\x8b') else raw
                assert 'sha256:'+digest(plain)==expected_diff
                with tarfile.open(fileobj=io.BytesIO(plain)) as contents:
                    members=contents.getmembers();paths=[safe(m.name) for m in members]
                    app_only=all(n in {'.','app','output'} or n.startswith(('app/','output/')) for n in paths)
                    assert app_only or not any('d_algorithms' in PurePosixPath(n).parts for n in paths), 'Unexpected bundled algorithm fixture in dependency layer'
                    for member,path in zip(members,paths):
                        if not member.isfile():continue
                        if path=='app/requirements.txt':base_requirements=contents.extractfile(member).read()
                        if path=='etc/passwd':
                            for line in contents.extractfile(member).read().decode().splitlines():
                                if line.startswith('agent:'):agent_ids=tuple(map(int,line.split(':')[2:4]))
                        if path.endswith('.dist-info/METADATA'):
                            data=contents.extractfile(member).read().decode(errors='replace')
                            nm=re.search(r'^Name: (.+)$',data,re.M);ver=re.search(r'^Version: (.+)$',data,re.M)
                            if nm and ver:packages[re.sub('[-_.]+','-',nm[1].strip().lower())]=ver[1].strip()
                        if '/site-packages/agenthack/' in path and path.endswith('.py'):sdk[path]=digest(contents.extractfile(member).read())
                scanned[name]={'blob':name,'diff_id':expected_diff,'size':len(raw),'app_only':app_only,'members':len(members)}
            row=scanned[name];assert row['diff_id']==expected_diff
            audits.append(row)
            if not row['app_only']:kept.append(row)
        assert agent_ids is not None and sdk, 'Official SDK or non-root account missing'
        base_pins={line.strip().lower() for line in base_requirements.decode().splitlines() if line.strip() and not line.lstrip().startswith('#')}
        assert pins==base_pins,'Dependency requirements differ from the complete Linux base'
        mismatches=[]
        for pin in pins:
            name,version_pin=pin.split('==')
            actual=packages.get(re.sub('[-_.]+','-',name))
            if actual!=version_pin:mismatches.append({'package':name,'expected':version_pin,'observed':actual})
        assert not mismatches,'Pinned dependency metadata mismatch: '+json.dumps(mismatches)
        uid,gid=agent_ids
        layer_stream=io.BytesIO()
        with tarfile.open(fileobj=layer_stream,mode='w',format=tarfile.PAX_FORMAT) as layer:
            add(layer,'app',b'',uid,gid,True)
            add(layer,'app/.wh..wh..opq',b'',uid,gid)
            directories=sorted({str(p) for name in image_files for p in PurePosixPath('app/'+name).parents if str(p) not in {'.','app'}},key=lambda p:(p.count('/'),p))
            for directory in directories:add(layer,directory,b'',uid,gid,True)
            for name,data in sorted(image_files.items()):add(layer,'app/'+name,data,uid,gid)
            add(layer,'output',b'',uid,gid,True)
        layer_plain=layer_stream.getvalue();layer_raw=gzip.compress(layer_plain,mtime=0)
        layer_digest=digest(layer_raw);layer_name='blobs/sha256/'+layer_digest
        env=dict(item.split('=',1) for item in config['config'].get('Env',[]))
        assert not any(v for k,v in env.items() if k!='GPG_KEY' and re.search('KEY|TOKEN|SECRET|PASSWORD',k,re.I)), 'Non-empty credential environment; withheld'
        env.update(PYTHONPATH='/app/src',RESEARCH_WORKSPACE_ROOT='/data',RESEARCH_RESULT_ROOT='/output',AGENT_VERSION=version,TEAM_ID='')
        env.pop('RESEARCH_WORKSPACE_TEAM',None)
        cfg=config['config'];cfg.update(Env=[k+'='+v for k,v in env.items()],User='agent',WorkingDir='/app',Entrypoint=None,Cmd=['python','agent.py'],ExposedPorts={'8080/tcp':{}})
        labels=cfg.setdefault('Labels',{});labels.pop('org.agenthack.team_id',None)
        labels.update({'org.agenthack.base':'false','org.agenthack.version':version,
            'org.agenthack.prompt_version':'20261027-v21-b-retrieval','org.opencontainers.image.revision':revision,
            'org.opencontainers.image.created':created,'org.opencontainers.image.source':'https://github.com/Sunming1126/research-agent'})
        config['created']=created;config['rootfs']={'type':'layers','diff_ids':[r['diff_id'] for r in kept]+['sha256:'+digest(layer_plain)]}
        config['history']=[{'created_by':'Inherited verified Linux dependency layer '+r['diff_id']} for r in kept]+[{'created':created,'created_by':'Install exact Git application snapshot '+revision}]
        config_bytes=serialized(config);config_digest=digest(config_bytes);config_name='blobs/sha256/'+config_digest
        descriptors=[{'mediaType':'application/vnd.oci.image.layer.v1.tar+gzip' if base.extractfile(r['blob']).read(2)==b'\x1f\x8b' else 'application/vnd.oci.image.layer.v1.tar','digest':'sha256:'+r['blob'].rsplit('/',1)[-1],'size':r['size']} for r in kept]
        descriptors.append({'mediaType':'application/vnd.oci.image.layer.v1.tar+gzip','digest':'sha256:'+layer_digest,'size':len(layer_raw)})
        manifest={'schemaVersion':2,'mediaType':'application/vnd.oci.image.manifest.v1+json','config':{'mediaType':'application/vnd.oci.image.config.v1+json','digest':'sha256:'+config_digest,'size':len(config_bytes)},'layers':descriptors}
        manifest_bytes=serialized(manifest);manifest_digest=digest(manifest_bytes)
        index={'schemaVersion':2,'mediaType':'application/vnd.oci.image.index.v1+json','manifests':[{'mediaType':manifest['mediaType'],'digest':'sha256:'+manifest_digest,'size':len(manifest_bytes),'platform':{'architecture':'amd64','os':'linux'},'annotations':{'io.containerd.image.name':'docker.io/library/'+tag,'org.opencontainers.image.ref.name':version}}]}
        legacy=[{'Config':config_name,'RepoTags':[tag],'Layers':[r['blob'] for r in kept]+[layer_name]}]
        with tarfile.open(archive_path,mode='w') as result:
            for name in dict.fromkeys(r['blob'] for r in kept):add(result,name,base.extractfile(name).read())
            add(result,layer_name,layer_raw);add(result,config_name,config_bytes)
            add(result,'blobs/sha256/'+manifest_digest,manifest_bytes)
            add(result,'index.json',serialized(index));add(result,'oci-layout',serialized({'imageLayoutVersion':'1.0.0'}));add(result,'manifest.json',serialized(legacy))
    assert archive_path.stat().st_size<2_000_000_000
    # Independent archive readback: every referenced blob, diff ID, source byte and config.
    with tarfile.open(archive_path) as archive:
        entry=json.load(archive.extractfile('manifest.json'))[0]
        actual_cfg=json.load(archive.extractfile(entry['Config']))
        assert actual_cfg['config']['User']=='agent' and actual_cfg['config']['Cmd']==['python','agent.py']
        assert entry['RepoTags']==[tag] and len(entry['Layers'])==len(actual_cfg['rootfs']['diff_ids'])
        for name,expected in zip(entry['Layers'],actual_cfg['rootfs']['diff_ids']):
            raw=archive.extractfile(name).read();assert digest(raw)==name.rsplit('/',1)[-1]
            data=gzip.decompress(raw) if raw.startswith(b'\x1f\x8b') else raw
            assert 'sha256:'+digest(data)==expected
        idx=json.load(archive.extractfile('index.json'));desc=idx['manifests'][0]
        data=archive.extractfile('blobs/sha256/'+desc['digest'].split(':')[1]).read()
        assert digest(data)==desc['digest'].split(':')[1] and len(data)==desc['size']
        oci=json.loads(data)
        for desc in [oci['config'],*oci['layers']]:
            blob=archive.extractfile('blobs/sha256/'+desc['digest'].split(':')[1]).read()
            assert len(blob)==desc['size'] and digest(blob)==desc['digest'].split(':')[1]
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(archive.extractfile(entry['Layers'][-1]).read()))) as layer:
            names=set(layer.getnames());assert 'app/.wh..wh..opq' in names
            for name,data in image_files.items():assert layer.extractfile('app/'+name).read()==data
            assert not any('d_algorithms' in PurePosixPath(name).parts for name in names)
    source_zip=out/('research-agent-'+revision[:7]+'-build-source.zip')
    with zipfile.ZipFile(source_zip,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in sorted(files.items()):archive.writestr(name,data)
    dump(out/'source-sha256.json',source_hashes)
    dump(out/'static-image-audit.json',{'source_revision':revision,'platform':'linux/amd64','official_runtime_entry_verified':True,
         'image_application_files':len(image_files),'source_bytes_readback_matched':True,'dependency_pins_verified':len(pins),
         'official_sdk_files_retained':sdk,'agent_uid':uid,'agent_gid':gid,'base_layers_examined':audits,
         'app_only_or_empty_layers_removed':sum(r['app_only'] for r in audits),'archive_blob_and_diffid_verified':True,
         'credentials_found':False,'tested_checkout_line_endings_preserved':line_ending_files,
         'runtime_execution_verified':False,'native_harness_execution':False,'model_calls':0})
    delivery={'version':version,'source_revision':revision,'image':tag,'image_id':'sha256:'+config_digest,
         'image_archive':archive_path.name,'image_archive_bytes':archive_path.stat().st_size,'image_archive_sha256':filehash(archive_path),
         'base_archive_sha256':filehash(args.base),'assembly':'OCI filesystem layers from complete Linux archive plus exact Git source; Docker daemon unavailable',
         'platform':'linux/amd64','port':8080,'official_runtime_entry_verified_statically':True,'static_archive_audit':'passed',
         'docker_load_verified':False,'container_health_verified':False,'real_model_validation':False,
         'ready_for_recommended_contest_submission':False,'uploaded_to_contest':False,'official_score':None,
         'source_archive':source_zip.name,'source_archive_sha256':filehash(source_zip)}
    dump(out/'delivery-manifest.json',delivery)
    (out/'SHA256SUMS.txt').write_text(delivery['image_archive_sha256']+'  '+archive_path.name+'\n'+filehash(source_zip)+'  '+source_zip.name+'\n',encoding='ascii')
    validation_note='上一轮离线正确性：同60篇十题旧8/10、新10/10；重复三题旧5/9、新9/9，397项源码回归通过，6环境跳过与1已知strict xfail。上述不是本镜像的真实模型或官方榜分。'
    if args.validation_summary:
        checks=json.loads(args.validation_summary.read_text(encoding='utf-8'))
        assert checks['native_harness_execution'] and checks['candidate_offline_checks_passed']
        validation_note=('本轮原Harness反例：旧版'+str(checks['baseline']['passed'])+'/'+str(checks['baseline']['tests'])+
            '，候选'+str(checks['candidate']['passed'])+'/'+str(checks['candidate']['tests'])+'。完整源码回归'+str(checks['regression']['passed'])+
            '通过，0失败，'+str(checks['regression']['skipped'])+'项跳过（含已知预期失败），目标模型调用0。'
            '修复了独立复现的C响应恢复缺陷；尚无私有日志证明它就是eval-20261030-2de7fb4c的C-01根因，D-01失败未解决。')
    (out/'提交说明.md').write_text('# 科研助手'+version+'提交文件\n\n上传文件：`'+archive_path.name+'`。它是完整Linux/amd64镜像归档；不要上传源码ZIP。\n\n'
        '此包从完整Linux依赖层及Git提交`'+revision+'`的应用源码组装，静态回读及摘要校验通过。官方AgentRuntime入口、非root用户、8080端口及33项依赖版本已核对。\n\n'
        '本机Docker因Inference manager临时通信文件故障未能启动，因此本包尚未完成docker load、容器健康与真实模型复测，不标为已完成验收。先在平台上传后检查镜像解析和健康状态，再决定启动本轮评测。\n\n'
        '平台操作：赛题一科研助手 → 上传主Agent镜像 → 选择同目录tar → 等待处理 → 核对版本/健康状态 → 开始评测。平台API凭据及RESEARCH_WORKSPACE_TEAM由平台注入，不写真实队名，不填写个人模型Key。\n\n'
        +validation_note+'\n\n'
        '本地导入命令（待Docker恢复后执行）：\n\n```powershell\ndocker load -i "'+archive_path.name+'"\ndocker image inspect '+tag+'\n```\n',encoding='utf-8')
    print(json.dumps(delivery,ensure_ascii=False))


if __name__=='__main__':main()
