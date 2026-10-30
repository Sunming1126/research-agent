"""Deny outbound connections in native Harness no-target-model test children."""
import ipaddress
import json
import os
from pathlib import Path
import socket

path=Path(os.environ['ANIC_C_TEST_GUARD_LOG'])
state={'target_model_calls':0,'external_network_allowed':False,'external_attempts_blocked':0}


def write():
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(state)+'\n',encoding='utf-8')


def check(address):
    if not isinstance(address,tuple):return
    host=address[0]
    if isinstance(host,bytes):host=host.decode(errors='replace')
    if host=='localhost':return
    try:
        if ipaddress.ip_address(host).is_loopback:return
    except ValueError:pass
    state['external_attempts_blocked']+=1;write()
    raise OSError('External connection prohibited in no-target-model C regression')


connect=socket.socket.connect;connect_ex=socket.socket.connect_ex
def guarded_connect(self,address):check(address);return connect(self,address)
def guarded_connect_ex(self,address):check(address);return connect_ex(self,address)
socket.socket.connect=guarded_connect;socket.socket.connect_ex=guarded_connect_ex
write()
