"""Remove only this task's empty, unfinished optional GitHub release."""
import json
import os
from pathlib import Path
import subprocess
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parent.parent;REPO=ROOT/'协同开发/research-agent-d-contract-20261106'
a=subprocess.run(['git','-c','safe.directory='+REPO.as_posix(),'credential','fill'],input='protocol=https\nhost=github.com\n\n',capture_output=True,text=True,cwd=REPO,timeout=35,env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never'))
assert a.returncode==0
secret=dict(line.split('=',1) for line in a.stdout.splitlines() if '=' in line)['password']
def api(path,method='GET',user=False):
    req=Request('https://api.github.com'+('/user' if user else '/repos/Sunming1126/research-agent'+path),headers={'Authorization':'Bearer '+secret,'Accept':'application/vnd.github+json','User-Agent':'research-agent-d30-archive'},method=method)
    with urlopen(req,timeout=45) as r:return None if r.status==204 else json.load(r)
release=api('/releases/436051812')
assert release['tag_name']=='d30-20261106-2e6f51a643ac' and release['prerelease']
assert release['author']['id']==api('',user=True)['id']
assets=api('/releases/436051812/assets')
assert all(x['name']=='research-assistant-d30-2e6f51a643ac.tar' and x['state']=='starter' and x.get('digest') is None and x['uploader']['id']==release['author']['id'] for x in assets)
tag=api('/git/ref/tags/d30-20261106-2e6f51a643ac')
assert tag['object']['sha']=='5736833c3a9fd3379ba4bfd6f9f3e2115469254b'
api('/releases/436051812','DELETE')
api('/git/refs/tags/d30-20261106-2e6f51a643ac','DELETE')
record={'removed_only_own_incomplete_prerelease':True,'release_id':436051812,'removed_incomplete_asset_ids':[x['id'] for x in assets],'archive_branch_preserved':'archive/d30-release-20261106','local_image_preserved':True}
(ROOT/'.session-audit/d30-release-cleanup-20261106.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
print(json.dumps(record))
