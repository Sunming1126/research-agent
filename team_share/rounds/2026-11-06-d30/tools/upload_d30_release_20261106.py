"""Upload the authorized image materials to GitHub; no competition submission."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parent.parent
REPO=ROOT/'协同开发/research-agent-d-contract-20261106'
DELIVERY=ROOT/'交付物/2026-11-06-d30-2e6f51a643ac'
OWNER='Sunming1126';NAME='research-agent';BRANCH='archive/d30-release-20261106'
TAG='d30-20261106-2e6f51a643ac'
manifest=json.loads((DELIVERY/'manifest.json').read_text())
tar=Path(manifest['archive'])
with tar.open('rb') as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==manifest['sha256']
auth=subprocess.run(['git','-c','safe.directory='+REPO.as_posix(),'credential','fill'],
    input='protocol=https\nhost=github.com\n\n',text=True,capture_output=True,cwd=REPO,
    env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never'),timeout=35)
if auth.returncode:raise SystemExit('Existing GitHub authentication unavailable; no credential output')
credentials=dict(line.split('=',1) for line in auth.stdout.splitlines() if '=' in line)
token=credentials.get('password')
assert token,'No GitHub credential from the existing Git helper'
headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
         'X-GitHub-Api-Version':'2022-11-28','User-Agent':'research-agent-d30-archive'}
state={'tag':TAG,'branch':BRANCH,'assets':[],'complete':False}
progress=ROOT/'.session-audit/d30-upload-progress-20261106.json'
def save():progress.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def api(path,body=None,allow404=False):
    req=Request('https://api.github.com/repos/'+OWNER+'/'+NAME+path,
                data=None if body is None else json.dumps(body).encode(),
                headers={**headers,'Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=60) as response:return json.load(response)
    except HTTPError as exc:
        if allow404 and exc.code==404:return None
        raise RuntimeError('GitHub API HTTP '+str(exc.code)+'; no credentials logged') from None
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
assert api('/git/ref/heads/'+quote(BRANCH,safe='/'))['object']['sha']==head
release=api('/releases/tags/'+TAG,allow404=True)
if release is None:
    release=api('/releases',{'tag_name':TAG,'target_commitish':head,
        'name':'D30提交候选 · 2026-11-06 · 2e6f51a', 'draft':False,'prerelease':True,
        'body':'本轮源码、过程及原始证据保存在 archive/d30-release-20261106。源码固定2e6f51a643acf66920612f83555a1c3a59d9d8b8，修复PR #16。\n\n上传比赛平台请使用附件research-assistant-d30-2e6f51a643ac.tar，SHA256见SHA256SUMS.txt。Linux源码636项零失败/零跳过；新镜像入口、源码、SDK、health及10项D契约、tar回读通过。\n\n这是准备提交的候选，本次未启动正式评测；没有新官方分数，不代表D02/D05算法已提分。保留版本元数据2.0.10-bd7，请按tar文件名和SHA256识别此候选。'})
assert release['tag_name']==TAG and release['prerelease'] is True
state.update(release_id=release['id'],release_url=release['html_url'],archive_commit=head);save()
existing={row['name']:row for row in api('/releases/'+str(release['id'])+'/assets')}
paths=[tar,DELIVERY/'SHA256SUMS.txt',DELIVERY/'manifest.json',DELIVERY/'environment.json',
       DELIVERY/'提交说明.md',DELIVERY/'评测绑定模板.json']
class Reader:
    def __init__(self,path):self.stream=path.open('rb');self.count=0;self.last=0;self.path=path
    def read(self,n=-1):
        data=self.stream.read(n);self.count+=len(data)
        if self.count-self.last>=16*1024*1024 or not data:
            self.last=self.count;state['uploading']={'name':self.path.name,'bytes_sent':self.count,'bytes_total':self.path.stat().st_size};save()
        return data
    def close(self):self.stream.close()
for path in paths:
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if path.name in existing:
        asset=existing[path.name]
        assert asset['size']==path.stat().st_size and asset.get('digest')=='sha256:'+digest,'Existing asset is not proven identical; no overwrite'
    else:
        upload=release['upload_url'].split('{',1)[0]+'?'+urlencode({'name':path.name})
        assert urlparse(upload).netloc=='uploads.github.com'
        reader=Reader(path)
        state['uploading']={'name':path.name,'bytes_sent':0,'bytes_total':path.stat().st_size};save()
        try:
            request=Request(upload,data=reader,headers={**headers,'Content-Type':'application/x-tar' if path.suffix=='.tar' else 'application/octet-stream',
                                                       'Content-Length':str(path.stat().st_size)},method='POST')
            with urlopen(request,timeout=180) as response:asset=json.load(response)
        except HTTPError as exc:
            raise RuntimeError('GitHub asset upload HTTP '+str(exc.code)+'; existing artifacts retained') from None
        finally:reader.close()
    assert asset['size']==path.stat().st_size and asset['state']=='uploaded'
    remote=api('/releases/assets/'+str(asset['id']))
    remote_digest=remote.get('digest')
    if remote_digest:assert remote_digest=='sha256:'+digest
    state['assets'].append({'name':path.name,'id':asset['id'],'bytes':asset['size'],
                            'sha256':digest,'remote_digest':remote_digest,
                            'remote_digest_verified':remote_digest=='sha256:'+digest,
                            'download_url':asset['browser_download_url']});save()
state.pop('uploading',None);state['complete']=True;save()
(DELIVERY/'github-release.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
tar_asset=next(row for row in state['assets'] if row['name'].endswith('.tar'))
(DELIVERY/'GitHub下载地址.md').write_text('# 本轮镜像下载\n\n归档分支：https://github.com/'+OWNER+'/'+NAME+'/tree/'+BRANCH+'\n\nRelease：'+state['release_url']+'\n\n镜像：'+tar_asset['download_url']+'\n\nSHA256：'+tar_asset['sha256']+'\n',encoding='utf-8')
print(json.dumps({'complete':True,'release_url':state['release_url'],'tar_url':tar_asset['download_url'],
                  'assets':len(state['assets']),'remote_tar_sha256_verified':tar_asset['remote_digest_verified']},ensure_ascii=False),flush=True)
credentials.clear();token=None
