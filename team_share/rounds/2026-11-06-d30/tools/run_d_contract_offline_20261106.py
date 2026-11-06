"""No D execution without a recorded, explicit D-specific user exception."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
authorization=ROOT/'.session-audit/d-contract-offline-authorization-20261106.json'
if not authorization.is_file():
    raise SystemExit('D-specific offline authorization not recorded; no test launched')
record=json.loads(authorization.read_text(encoding='utf-8'))
assert record['explicit_user_authorization'] is True
assert record['scope']=='D zero-model fault injection and Linux regression'
sys.path.insert(0,str(ROOT/'镜像测试'))
from d_contract_20261106 import run
run({'candidate_commit':record['candidate_commit'],'execution_origin':'Codex offline',
     'target_model_calls':0,'native_tool_execution_verified':False,
     'authorization_receipt':str(authorization),'user_reply':record['user_reply'],
     **({'reuse_native_pairs':record['reuse_native_pairs']} if record.get('reuse_native_pairs') else {})})
