"""Summarize completed evidence; does not execute tests or target models."""
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent.parent
REPO=ROOT/'协同开发/research-agent-d-contract-20261106'
NATIVE=ROOT/'镜像测试/runs/d-contract-20261106-bdd77c7e'
OFFLINE=Path(json.loads((ROOT/'.session-audit/d-contract-latest-20261106.json').read_text())['directory'])
OUT=REPO/'docs/testing/2026-11-06-d-contract'
prior=json.loads((NATIVE/'summary.json').read_text());final=json.loads((OFFLINE/'summary.json').read_text())
assert final['complete'] and final['gate_passed'] and final['full']['passed']==636
assert all(final['full'][key]==0 for key in ('failure','error','skipped'))
assert final['environment_fingerprint']==prior['environment_fingerprint']
raw=json.loads((OUT/'diagnostic-matrix.json').read_text())
cases=ET.parse(OFFLINE/'full.xml').findall('.//testcase')
assert all(all(c.find(k) is None for k in ('failure','error','skipped')) for c in cases)
case_ids=[c.attrib['classname']+'::'+c.attrib['name'] for c in cases]
resolved=[]
for item in raw['cases']:
    old=item['id'];prefix=old.split('[',1)[0]
    matching=[cid for cid in case_ids if cid.split('[',1)[0]==prefix]
    assert matching,old
    exact=old if old in matching else None
    if not exact:
        # Two versioned expectations changed only their final expected value.
        assert any(t in prefix for t in ('late_repair','reasoning_is_case'))
        key=old.rsplit('-',1)[0]
        values=[cid for cid in matching if cid.rsplit('-',1)[0]==key]
        assert len(values)==1,(old,matching)
        exact=values[0]
    resolved.append({'original_id':old,'category':item['category'],'current_id':exact,
                     'result':'passed','evidence':'codex-offline-full.xml',
                     'production_defect_count_inferred_from_pass':False})
assert len(resolved)==30
files=['summary.json','environment.json','source-manifest.json','full.xml','full.stdout','full.stderr']
for name in files:
    shutil.copyfile(OFFLINE/name,OUT/('codex-offline-'+name))
for name in ['summary.json','full.xml',*[f'pair-{i}-{kind}.xml' for i,kind in enumerate(['baseline','candidate','candidate','baseline','baseline','candidate'])]]:
    shutil.copyfile(NATIVE/name,OUT/('native-'+name))
proof=json.loads((NATIVE/'native-proof.json').read_text())
save_proof={k:proof[k] for k in ('request_id','session_id','workspace_id','stage','candidate_commit','native_tool_seq','native_tool_call_sha256','config_header_seq','native_tool_execution_verified')}
(OUT/'native-proof-summary.json').write_text(json.dumps(save_proof,indent=2)+'\n',encoding='utf-8')
summary={'candidate_commit':final['candidate_commit'],'baseline_commit':final['baseline_commit'],
         'resolution':resolved,'linux_full':final['full'],
         'pair_order':['baseline','candidate','candidate','baseline','baseline','candidate'],
         'native_pair_results':{k:v for k,v in prior.items() if k.startswith('pair-')},
         'environment_fingerprint':final['environment_fingerprint'],
         'production_changes':['src/research_agent/workflows/layered_d_runner.py'],
         'source_policy':'D cancellation only; no algorithm/prompt/threshold/ABCE/shared SDK changes',
         'old_30_failures_retained':True,'native_first_full_missing_fixture_retained':True,
         'target_model_calls':0,'official_score':None,'official_score_delta':None,
         'D02_second_scenario_and_D05_service_accounting_proven_fixed':False,
         'scope':'D30 current-contract regression closure; not a scoring closure'}
(OUT/'closure.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.iterdir() if p.is_file() and p.name!='evidence-sha256.json'}
(OUT/'evidence-sha256.json').write_text(json.dumps(hashes,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'resolved_original_cases':len(resolved),'full_passed':636,'failures':0,'skipped':0,'new_target_calls':0,'source':str(OFFLINE)},ensure_ascii=False))
