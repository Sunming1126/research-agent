"""Read-only existing connection; used by the native Harness test coordinator.

No profile changes, plugins or fetch patches. Selected credentials remain in
memory and are sent only to that connection's configured API as authentication.
Docker receives only an ephemeral local bridge token, never the provider key.
"""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

from harness_bridge import write_json
HERE=Path(__file__).resolve().parent
QUOTA_EXHAUSTED=threading.Event()

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None

class MagicAPI:
    def __init__(self):
        env=os.environ.copy();env['ELECTRON_RUN_AS_NODE']='1';env['ANIC_PRIVATE_CONFIG_PIPE']='parent-process-only'
        result=subprocess.run(['D:/deepseek/DeepSeek Harness.exe',str(HERE/'read_magic_connection.cjs'),'--private-parent-pipe'],
                              env=env,capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise RuntimeError('Selected magic connection unavailable; no raw configuration echoed')
        self._config=json.loads(result.stdout)
        assert self._config['provider']=='magic'
        self.model=self._config['model']
        self.url=self._config['base_url'].rstrip('/')+'/chat/completions'
        self.opener=urllib.request.build_opener(NoRedirect())
        self.quota_exhausted=QUOTA_EXHAUSTED

    def complete(self,body,directory,timeout=55):
        if self.quota_exhausted.is_set():raise RuntimeError('MAGIC_ACCOUNT_QUOTA')
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        request_body=dict(body);request_body['model']=self.model
        # Controls come from the tested Agent request; do not invent a larger cap.
        if type(request_body.get('max_tokens')) is not int or request_body['max_tokens']<1:
            raise ValueError('Target omitted a valid output cap')
        write_json(directory/'request.json',request_body)
        headers={str(k):str(v) for k,v in self._config.get('headers',{}).items()}
        headers.update({'Content-Type':'application/json','Authorization':'Bearer '+self._config['api_key']})
        request=urllib.request.Request(self.url,data=json.dumps(request_body,ensure_ascii=False).encode('utf-8'),headers=headers)
        started=time.monotonic()
        meta=dict(provider='magic',model=self.model,backend='Existing magic API; native Harness workspace orchestration',
                  real_model_call=True,requested_max_tokens=request_body['max_tokens'],
                  thinking=request_body.get('thinking'),credentials_logged=False,profile_modified=False,
                  native_orchestrator_session_id=os.environ.get('ANIC_NATIVE_ORCHESTRATOR_SESSION_ID'))
        try:
            with self.opener.open(request,timeout=timeout) as response:
                value=json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            # An upstream diagnostic may echo credentials; do not save/echo it.
            try:error=json.loads(exc.read().decode('utf-8')).get('error',{})
            except (ValueError,UnicodeDecodeError):error={}
            code=error.get('code') if isinstance(error,dict) else None
            code=code if code in {'AccountQuotaExceeded','insufficient_quota','rate_limit_exceeded','context_length_exceeded','invalid_api_key'} else 'UPSTREAM_ERROR'
            if code in {'AccountQuotaExceeded','insufficient_quota'}:self.quota_exhausted.set()
            meta.update(http_status=exc.code,error_code=code,seconds=round(time.monotonic()-started,3))
            write_json(directory/'call.json',meta)
            raise RuntimeError('magic HTTP '+str(exc.code)+' '+code) from None
        except (urllib.error.URLError,TimeoutError,OSError) as exc:
            reason=getattr(exc,'reason',exc)
            number=getattr(reason,'errno',None)
            meta.update(error_code='TRANSPORT_ERROR',seconds=round(time.monotonic()-started,3),
                        transport_exception_type=type(reason).__name__,
                        transport_errno=number if type(number) is int else None)
            write_json(directory/'call.json',meta)
            raise RuntimeError('magic connection transport failed; no credentials displayed') from None
        usage=value.get('usage') or {}
        meta.update(seconds=round(time.monotonic()-started,3),usage=usage,
                    finish_reason=(value.get('choices') or [{}])[0].get('finish_reason'),http_status=200)
        write_json(directory/'response.json',value);write_json(directory/'call.json',meta)
        completion=usage.get('completion_tokens')
        if type(completion) is int and completion>request_body['max_tokens']:
            raise RuntimeError('magic returned completion usage over the forwarded output cap')
        return value,meta

class HarnessBridge:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.token=secrets.token_urlsafe(32);self.records=[];self.lock=threading.Lock();self.api=MagicAPI()
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.headers.get('Authorization')!='Bearer '+owner.token:return self.send(401,{'error':{'message':'Local test bridge authentication failed'}})
                if not self.path.endswith('/chat/completions'):return self.send(404,{'error':{'message':'Unsupported test route'}})
                if owner.api.quota_exhausted.is_set():return self.send(429,{'error':{'code':'AccountQuotaExceeded','message':'Configured model quota exhausted'}})
                identifier=uuid.uuid4().hex
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=2_000_000:return self.send(413,{'error':{'message':'Request too large'}})
                    body=json.loads(self.rfile.read(size))
                    value,meta=owner.api.complete(body,owner.root/identifier)
                    with owner.lock:owner.records.append(dict(id=identifier,real_model_call=True,seconds=meta['seconds'],call_dir=str(owner.root/identifier)))
                    return self.send(200,value)
                except Exception as exc:
                    if owner.api.quota_exhausted.is_set():return self.send(429,{'error':{'code':'AccountQuotaExceeded','message':'Configured model quota exhausted'}})
                    return self.send(502,{'error':{'message':str(exc)[:150]}})
            def send(self,code,value):
                data=json.dumps(value,ensure_ascii=False).encode('utf-8')
                try:
                    self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError):pass
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('0.0.0.0',0),Handler);self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
    def __enter__(self):self.thread.start();return self
    def __exit__(self,*args):
        self.server.shutdown();self.server.server_close();write_json(self.root/'calls-index.json',self.records)

def call_harness(prompt,output,timeout=150,max_tokens=None,reasoning_effort=None):
    api=MagicAPI()
    body={'model':api.model,'messages':[{'role':'user','content':prompt}],'max_tokens':max_tokens or 2400,'thinking':{'type':'disabled'}}
    value,meta=api.complete(body,output,timeout=min(timeout,55))
    text=value['choices'][0]['message']['content']
    (Path(output)/'reply.txt').write_text(text,encoding='utf-8')
    return text,meta
