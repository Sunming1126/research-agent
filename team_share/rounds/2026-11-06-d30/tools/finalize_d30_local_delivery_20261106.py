"""Complete requested local image handoff and GitHub process archive."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid
import zipfile

ROOT=Path(__file__).resolve().parent.parent;REPO=ROOT/'协同开发/research-agent-d-contract-20261106'
DELIVERY=ROOT/'交付物/2026-11-06-d30-2e6f51a643ac';ROUND=REPO/'team_share/rounds/2026-11-06-d30'
BRANCH_URL='https://github.com/Sunming1126/research-agent/tree/archive/d30-release-20261106'
def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
m=json.loads((DELIVERY/'manifest.json').read_text());tar=Path(m['archive'])
assert digest(tar)==m['sha256']
m.update(github_archive_branch='archive/d30-release-20261106',github_archive_url=BRANCH_URL,
         github_image_upload_complete=False,github_image_upload_note='Optional binary transfer failed; source and complete process archive uploaded; use local tar')
save(DELIVERY/'manifest.json',m)
note='# GitHub归档与本地提交\n\n完整源码与过程：'+BRANCH_URL+'\n\n比赛上传使用本目录'+tar.name+'，SHA256为'+m['sha256']+'。镜像大附件的Python/curl直传遭连接重置，LFS通道也不稳定，未标记为远端上传成功。临时空Release及未完成占位已清理。新分支保存完整代码、失败/成功证据、工具和清单；本地tar已验证可用。\n'
(DELIVERY/'GitHub归档说明.md').write_text(note,encoding='utf-8')
instructions=(DELIVERY/'提交说明.md').read_text(encoding='utf-8')
instructions=instructions.replace('镜像下载链接见同目录GitHub下载地址.md（上传完成后生成）。','本轮大文件传输未完成，请直接使用本地tar；GitHub源码与过程归档地址见同目录GitHub归档说明.md。')
(DELIVERY/'提交说明.md').write_text(instructions,encoding='utf-8')
for name in ('manifest.json','提交说明.md','GitHub归档说明.md'):
    shutil.copyfile(DELIVERY/name,ROUND/'submission'/name)
for name in ('upload_d30_release_20261106.py','retry_d30_tar_curl_20261106.py',
             'upload_d30_lfs_20261106.py','cleanup_d30_incomplete_release_20261106.py','finalize_d30_local_delivery_20261106.py'):
    shutil.copyfile(ROOT/'.session-audit'/name,ROUND/'tools'/name)
for name in ('d30-upload-first-attempt-20261106.json','d30-curl-transfer-20261106.json','d30-curl-transfer-20261106.stderr',
             'd30-lfs-receipt-20261106.json','d30-lfs-transfer-20261106.log','d30-release-cleanup-20261106.json'):
    source=ROOT/'.session-audit'/name
    if source.is_file():shutil.copyfile(source,ROUND/'raw/image-package'/name)
save(ROUND/'raw/image-package/upload-outcome.json',{
    'scope':'Optional mirror of binary; requested local image and GitHub process archive are ready',
    'python_upload':'URLError / connection reset after >=16MiB; not completed',
    'curl_upload':'exit 55 connection reset after partial transfer; not completed',
    'lfs_upload':'low throughput; own upload stopped to avoid delaying local handoff; not completed',
    'remote_binary_upload_success_claimed':False,'owned_empty_prerelease_cleaned':True,
    'local_tar_preserved_and_verified':True,'credentials_or_browser_storage_archived':False})
timeline=json.loads((ROUND/'timeline.json').read_text())
timeline[-1]['result']='source/process branch uploaded; optional image mirror failed; verified local tar delivered; incomplete placeholder release cleaned'
timeline[-1]['image_sha256']=m['sha256']
save(ROUND/'timeline.json',timeline)
readme=(ROUND/'README.md').read_text(encoding='utf-8').replace('大文件通过GitHub Release附件保存。','本轮大文件传输未完成，镜像在本地交付；不保存无效LFS指针。')
readme+='\n## 当前可用交付\n\n已准备可直接提交的镜像tar及SHA256、提交说明、源码/测试和过程证据包。本分支完整保存本轮过程；镜像大文件远端传输未完成，请使用本地tar。两次直传中断、LFS停止及空Release清理记录均在raw/image-package，未将失败改写为成功。未启动正式评测。\n'
(ROUND/'README.md').write_text(readme,encoding='utf-8')
files={p.relative_to(ROUND).as_posix():{'sha256':digest(p),'bytes':p.stat().st_size}
       for p in sorted(ROUND.rglob('*')) if p.is_file() and p.name!='archive-sha256.json'}
save(ROUND/'archive-sha256.json',files)
bundle=DELIVERY/'D30本轮过程与证据.zip'
with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for path in sorted(ROUND.rglob('*')):
        if path.is_file():z.write(path,path.relative_to(ROUND).as_posix())
source_zip=DELIVERY/'源码与测试-2e6f51a.zip'
subprocess.run(['git','archive','--format=zip','--output',str(source_zip),m['source_commit'],
                'src','tests','agent.py','Dockerfile','.dockerignore','requirements.txt','requirements-test.txt','pyproject.toml','README.md'],cwd=REPO,check=True)
save(DELIVERY/'交付材料校验.json',{p.name:{'sha256':digest(p),'bytes':p.stat().st_size} for p in DELIVERY.iterdir() if p.is_file() and p.name!='交付材料校验.json'})
backup=Path('D:/Agent挑战赛/2026-11-06-d30-release')/('complete-'+uuid.uuid4().hex[:8])
assert backup.resolve().is_relative_to(Path('D:/Agent挑战赛').resolve());backup.mkdir(parents=True,exist_ok=False)
copied=[]
for tree,label in ((DELIVERY,'submission'),(ROUND,'round-archive')):
    for path in sorted(tree.rglob('*')):
        if not path.is_file():continue
        relative=Path(label)/path.relative_to(tree);target=backup/relative
        assert target.resolve().is_relative_to(backup.resolve())
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        expected=digest(path);assert digest(target)==expected
        copied.append({'path':relative.as_posix(),'sha256':expected,'bytes':path.stat().st_size})
save(backup/'backup-manifest.json',copied)
record={'archive_branch':'archive/d30-release-20261106','archive_url':BRANCH_URL,'local_tar':str(tar),
        'tar_sha256':m['sha256'],'backup':str(backup),'backup_files_verified':len(copied),
        'github_image_uploaded':False,'official_evaluation_started':False}
save(ROOT/'.session-audit/d30-final-delivery-receipt-20261106.json',record)
print(json.dumps(record,ensure_ascii=False),flush=True)
