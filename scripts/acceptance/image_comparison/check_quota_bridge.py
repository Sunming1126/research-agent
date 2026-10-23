from pathlib import Path
import json,urllib.request,urllib.error
from unittest.mock import patch
import harness_bridge_quota as Q
root=Path(__file__).resolve().parent/'runs/hybrid-quota-bridge-fixture'
root.mkdir(parents=True,exist_ok=True)
with patch.object(Q,'call_harness',side_effect=Q.HarnessQuotaError('Harness model call failed: ACCOUNT_QUOTA')) as native,Q.HarnessBridge(root) as bridge:
 for _ in range(2):
  request=urllib.request.Request(f'http://127.0.0.1:{bridge.port}/session/fixture/v1/chat/completions',data=b'{"messages":[]}',headers={'Content-Type':'application/json','Authorization':'Bearer '+bridge.token})
  try:urllib.request.urlopen(request,timeout=5);raise AssertionError('quota fixture should reject')
  except urllib.error.HTTPError as e:assert e.code==429 and json.loads(e.read())['error']['code']=='AccountQuotaExceeded'
 assert native.call_count==1
 row={'status':'PASS','fixture_not_real_model':True,'http':429,'provider_code':'AccountQuotaExceeded','native_mock_calls':native.call_count,'second_request_short_circuited':True}
 Q.write_json(root/'result.json',row)
 print(json.dumps(row))
