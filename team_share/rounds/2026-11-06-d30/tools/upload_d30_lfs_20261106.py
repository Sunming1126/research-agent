"""Save the verified image on the requested archive branch through Git LFS."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlsplit,urlunsplit

ROOT=Path(__file__).resolve().parent.parent
REPO=ROOT/'协同开发/research-agent-d-contract-20261106'
DELIVERY=ROOT/'交付物/2026-11-06-d30-2e6f51a643ac'
m=json.loads((DELIVERY/'manifest.json').read_text())
source=Path(m['archive']);relative=Path('team_share/image/d30-20261106')/source.name;target=REPO/relative
target.parent.mkdir(parents=True,exist_ok=True)
if not target.exists():shutil.copyfile(source,target)
with target.open('rb') as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==m['sha256']
git=['git','-c','safe.directory='+REPO.as_posix()]
subprocess.run(git+['add','-f',relative.as_posix()],cwd=REPO,check=True,timeout=90)
pointer=subprocess.check_output(git+['show',':'+relative.as_posix()],cwd=REPO,text=True)
assert pointer.startswith('version https://git-lfs.github.com/spec/v1\n')
assert 'oid sha256:'+m['sha256'] in pointer and 'size '+str(m['bytes']) in pointer
progress=ROOT/'.session-audit/d30-lfs-progress-20261106.txt';progress.touch(exist_ok=True)
env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never',GIT_LFS_FORCE_PROGRESS='1',GIT_LFS_PROGRESS=str(progress))
result=subprocess.run(git+['-c','lfs.transfer.maxretries=1','-c','lfs.dialtimeout=30','-c','lfs.activitytimeout=120',
                          'lfs','push','--object-id','origin',m['sha256']],cwd=REPO,env=env,capture_output=True,timeout=1200)
def sanitize(data):
    text=data.decode('utf-8',errors='replace')
    def redact(match):
        value=match.group(0);parts=urlsplit(value)
        return urlunsplit((parts.scheme,parts.netloc,parts.path,'[redacted-query]' if parts.query else '', ''))
    return re.sub(r'https?://[^\s\"<>]+',redact,text)
log=ROOT/'.session-audit/d30-lfs-transfer-20261106.log'
log.write_text(sanitize(result.stdout)+'\n'+sanitize(result.stderr),encoding='utf-8')
receipt={'branch':'archive/d30-release-20261106','path':relative.as_posix(),'oid_sha256':m['sha256'],
         'bytes':m['bytes'],'lfs_push_returncode':result.returncode,'pointer':pointer,
         'upload_acknowledged':result.returncode==0}
(ROOT/'.session-audit/d30-lfs-receipt-20261106.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(receipt,ensure_ascii=False),flush=True)
if result.returncode:raise SystemExit('LFS transfer failed; sanitized log retained')
