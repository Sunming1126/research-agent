"""Installed-image verification only. Does not start Harness or call a model."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import compare_images as C
import test_agent as T
from harness_bridge import write_json

IMAGE = 'research-assistant:0.4.2-hybrid-v3'
RUN = 'hybrid-20261023-v042-v3-offline'

def quota_check():
    code = '''import json,tempfile
from pathlib import Path
from unittest.mock import patch
from research_agent.integrations.llm import ModelQuotaError
from research_agent.workflows.experiment_diagnostics import error_record
from research_agent.workflows.layered_d_runner import LayeredDRunner
from research_agent.core.config import Settings
from research_agent.core.models import CaseContext
root=Path(tempfile.mkdtemp())
runner=LayeredDRunner(Settings(root,root/'out',material_roots=[Path('/materials')]),root/'work')
case=CaseContext.from_dict({'case_id':'D-01','function':'Reproduction','title':'public','input':'public method','materials':[],'constraints':[],'expected_output':'result','output_schema_ref':'Reproduction'})
with patch.object(runner,'prepare_generated_implementation',side_effect=ModelQuotaError('quota exhausted')) as call:
 try:runner.prepare_checked_implementation(case)
 except ModelQuotaError:pass
 else:raise AssertionError('quota was swallowed')
 assert call.call_count==1
history=json.loads((runner.work/'public-selftest.json').read_text())['attempts']
assert len(history)==1 and history[0]['code']=='ACCOUNT_QUOTA' and history[0]['retryable'] is False
diagnostic=error_record(ModelQuotaError('quota exhausted'),'generation')
assert diagnostic['category']=='model_quota' and diagnostic['retryable'] is False
print(json.dumps({'fixture_not_real_model':True,'generation_calls':call.call_count,'diagnostic':diagnostic}))
'''
    result = T.docker('run', '--rm', '--pull=never', '--network', 'none', '--read-only',
                      '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                      '--cpus', '1', '--memory', '1024m', '--tmpfs', '/tmp:rw,nosuid,size=64m',
                      '-e', 'PYTHONDONTWRITEBYTECODE=1',
                      '--mount', f'type=bind,source={T.MATERIAL},target=/materials,readonly',
                      '--entrypoint', 'python', T.IMAGE, '-c', code)
    return json.loads(result)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quota-only', action='store_true')
    args = parser.parse_args()
    root = T.HERE / 'runs' / (RUN + ('-quota-corrected' if args.quota_only else ''))
    root.mkdir(parents=True, exist_ok=False)
    rec = T.Recorder(root)
    identity = C.metadata(rec, IMAGE)
    if not args.quota_only:
        rec.test('image.installed_source_inventory', 'runtime', lambda: C.inspect_source(rec))
        rec.test('container.suite_executed', 'runtime', lambda: C.offline(rec))
        T.http_checks(rec)
        rec.test('runtime.outer_timeout_and_no_late_submission', 'time-budget', lambda: C.outer_timeout(rec))
    rec.test('container.v3.quota_does_not_repeat_and_preserves_code', 'recovery', quota_check)
    rec.add('live.magic_workspace_acceptance', 'real-model', 'BLOCKED',
            error='Desktop workspace session binding must be verified before real calls')
    rec.add('official.source_admission_and_private_cases', 'official-platform', 'BLOCKED',
            error='Official Gateway provenance, trusted socket and private cases require platform evaluation')
    value = dict(target=IMAGE, image_id=identity, run=RUN,
                 counts=dict(Counter(r['status'] for r in rec.rows)),
                 real_model_calls=0, official_score=None,
                 mandatory_failures=[r['id'] for r in rec.rows if r['status']=='FAIL' and r['level']=='required'],
                 quality_failures=[r['id'] for r in rec.rows if r['status']=='FAIL' and r['level']=='quality'],
                 status=('FAILED' if any(r['status']=='FAIL' for r in rec.rows)
                         else 'OFFLINE_VERIFIED_PENDING_WORKSPACE_REAL_MODEL'))
    write_json(root / 'summary.json', value)
    print(json.dumps(value, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
