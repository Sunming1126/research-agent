"""Retry a failed, owned GitHub asset transfer using native curl streaming."""
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlencode
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parent.parent
REPO=ROOT/'协同开发/research-agent-d-contract-20261106'
DELIVERY=ROOT/'交付物/2026-11-06-d30-2e6f51a643ac'
m=json.loads((DELIVERY/'manifest.json').read_text());tar=Path(m['archive'])
auth=subprocess.run(['git','-c','safe.directory='+REPO.as_posix(),'credential','fill'],input='protocol=https\nhost=github.com\n\n',
                    capture_output=True,text=True,cwd=REPO,timeout=35,env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never'))
assert auth.returncode==0
credential=dict(s.split('=',1) for s in auth.stdout.splitlines() if '=' in s)
token=credential['password'];assert all(c not in token for c in ('\r','\n','"','\\'))
headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'research-agent-d30-archive'}
def api(path,method='GET'):
    req=Request('https://api.github.com/repos/Sunming1126/research-agent'+path,headers=headers,method=method)
    with urlopen(req,timeout=45) as r:return json.load(r) if r.status!=204 else None
release=api('/releases/tags/d30-20261106-2e6f51a643ac')
assert release['id']==436051812
record={'first_attempt':'Python streaming upload: connection reset, recorded >=16777216 bytes sent; not marked complete',
        'release_id':release['id'],'cleaned_owned_empty_assets':[]}
for a in api('/releases/'+str(release['id'])+'/assets'):
    if a['name']!=tar.name:continue
    if a['state']=='uploaded':
        assert a['size']==m['bytes'] and a.get('digest')=='sha256:'+m['sha256']
        raise SystemExit('Asset already verified; no duplicate transfer')
    # GitHub retains the declared Content-Length even for a failed starter.
    # This exact asset ID was created by the completed, failed upload above.
    if not (a['id']==715701520 and a['state']=='starter' and a.get('digest') is None
            and a['size'] in (0,m['bytes']) and a['uploader']['id']==release['author']['id']):
        raise RuntimeError(json.dumps({'owned_asset_inspection':{k:a.get(k) for k in ('id','name','state','size','digest')},
                                      'same_uploader_as_release_author':a['uploader']['id']==release['author']['id']}))
    api('/releases/assets/'+str(a['id']),'DELETE')
    record['cleaned_owned_empty_assets'].append(a['id'])
progress=ROOT/'.session-audit/d30-curl-transfer-20261106.json'
progress.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
upload=release['upload_url'].split('{',1)[0]+'?'+urlencode({'name':tar.name})
config='url = "'+upload+'"\n'+'\n'.join('header = "'+k+': '+v+'"' for k,v in {**headers,'Content-Type':'application/x-tar'}.items())+'\n'
response=ROOT/'.session-audit/d30-curl-asset-response-20261106.json'
stderr=ROOT/'.session-audit/d30-curl-transfer-20261106.stderr'
with stderr.open('wb') as error:
    p=subprocess.run(['C:/Windows/System32/curl.exe','--config','-','--request','POST','--upload-file',str(tar),
                      '--output',str(response),'--write-out','%{json}','--max-time','600','--connect-timeout','30',
                      '--speed-limit','1024','--speed-time','60'],input=config.encode(),stdout=subprocess.PIPE,stderr=error,timeout=640)
record['curl_returncode']=p.returncode
try:record['curl_metrics']=json.loads(p.stdout)
except ValueError:record['curl_metrics']={}
if p.returncode==0:
    a=json.loads(response.read_text(encoding='utf-8'))
    assert a['state']=='uploaded' and a['name']==tar.name and a['size']==m['bytes']
    assert a.get('digest')=='sha256:'+m['sha256']
    record['verified_asset']={k:a.get(k) for k in ('id','name','size','digest','browser_download_url','state')}
progress.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'returncode':p.returncode,'verified':bool(record.get('verified_asset'))},ensure_ascii=False),flush=True)
if p.returncode:raise SystemExit('curl transfer failed; exact stderr retained')
credential.clear();token=None
