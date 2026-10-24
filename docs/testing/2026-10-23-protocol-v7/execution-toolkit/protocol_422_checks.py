"""Real Docker HTTP protocol checks; fixtures are explicitly not real LLM calls."""
import json
from pathlib import Path
import test_agent as T
from harness_bridge import write_json


def public_task(cid):
    from compare_images import frozen_inputs
    tasks = frozen_inputs()['tasks']
    task = json.loads(json.dumps(tasks['A-01'] if cid.startswith('A-') else tasks[cid]))
    task['input']['case_id'] = cid
    task['task_id'] = 'protocol-public-' + cid
    return task


def baseline(proof):
    root = T.HERE / 'runs/protocol-422-v6-baseline'
    index = 1
    while root.exists():
        index += 1
        root = T.HERE / ('runs/protocol-422-v6-baseline-r' + str(index))
    T.IMAGE = 'research-assistant:0.4.2-hybrid-v6'
    T.docker('image', 'inspect', T.IMAGE)  # Native permission check before allocation.
    root.mkdir()
    write_json(root / 'native-orchestration-proof.json', proof)
    rec = T.Recorder(root)
    with T.FaultGateway('valid') as gateway, T.target(root / 'container', bridge=gateway) as (base, output, name):
        for cid in ['A-03', 'B-01', 'B-02']:
            def check(cid=cid):
                task = public_task(cid)
                code, body = T.http(base + '/solve', task['input'])
                write_json(root / (cid + '.json'), dict(request=task['input'], http=code, response=body))
                T.require(code == 422, 'Baseline no longer reproduces the reported 422')
                T.require(gateway.calls == 0, 'Rejected input reached the gateway')
                return dict(http=code, gateway_calls=0, reproduced_known_failure=True)
            rec.test('baseline.direct.' + cid, 'known-platform-failure', check)
        def legacy():
            code, body = T.http(base + '/solve', public_task('A-03'))
            write_json(root / 'A-03-sdk-envelope.json', dict(http=code, response=body))
            T.require(code == 200 and body.get('status') == 'success', 'Control SDK envelope failed')
            return dict(http=code, gateway_calls=gateway.calls, fixture_not_real_llm=True)
        rec.test('baseline.sdk_control', 'control', legacy)
    write_json(root / 'summary.json', dict(image=T.IMAGE, counts={s:sum(r['status']==s for r in rec.rows) for s in ['PASS','FAIL']},
        meaning='PASS means the old defect was reproduced, not that v6 is acceptable', fixture_not_real_llm=True,
        input_scope='Public frozen cases; A-01 renamed A-03 for protocol identity only, not the private platform question'))
    T.require(all(r['status']=='PASS' for r in rec.rows), 'Protocol baseline incomplete; see saved failures')


def fixed(rec):
    root = rec.root / 'direct-protocol-fixtures'
    with T.FaultGateway('valid') as gateway, T.target(root, bridge=gateway) as (base, output, name):
        for cid in ['A-03', 'A-100']:
            def check(cid=cid):
                task = public_task(cid)
                before = gateway.calls
                code, body = T.http(base + '/solve', task['input'])
                write_json(root / (cid + '.json'), dict(request=task['input'], http=code, response=body))
                T.require(code == 200, 'Direct case HTTP ' + str(code))
                T.require(set(body) == {'case_id','answer','clarifying_question'}, 'Response still wrapped in SDK fields')
                T.independent_contract(task, body, output)
                T.require(gateway.calls > before, 'Direct input did not execute the gateway call')
                return dict(http=code, gateway_calls=gateway.calls-before, fixture_not_real_llm=True)
            rec.test('protocol.direct.' + cid, 'production-http-contract', check)
        def legacy():
            code, body = T.http(base + '/solve', public_task('A-03'))
            write_json(root / 'sdk-envelope.json', dict(http=code, response=body))
            T.require(code == 200 and body.get('status') == 'success', 'SDK compatibility failed')
            T.require(body['metadata']['llm_calls'] >= 1, 'Official SDK model interception lost')
            T.independent_contract(public_task('A-03'), T.unpack_answer(body), output)
            return dict(http=code, sdk_llm_calls=body['metadata']['llm_calls'], fixture_not_real_llm=True)
        rec.test('protocol.sdk_legacy_and_telemetry', 'production-http-contract', legacy)
        for label, payload in [('missing_fields', {'case_id':'A-03'}), ('wrong_type', dict(public_task('A-03')['input'],constraints={})),
                               ('wrong_schema', dict(T.request_for('B-01')['input'],output_schema_ref='Chat'))]:
            def reject(label=label,payload=payload):
                before = gateway.calls; code, body = T.http(base + '/solve', payload)
                write_json(root / (label + '.json'), dict(request=payload,http=code,response=body))
                T.require(code == 422 and gateway.calls == before, 'Invalid direct case executed')
                return dict(http=code, gateway_calls=0)
            rec.test('protocol.reject.' + label, 'production-http-contract', reject)


def solve_case(base, task, *, request_path):
    """Send the exact direct case and preserve the wire payload beside its response."""
    write_json(Path(request_path), task['input'])
    return T.http(base + '/solve', task['input'])
