"""未修改官方客户端 + 本地Unix RPC夹具；不代表平台来源凭证已验收。"""
import json
import os
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import UnixStreamServer
from threading import Thread

import pytest

from fixtures import case
from research_agent.core.config import Settings
from research_agent.core.models import CaseContext
from research_agent.core.session import CaseSession
from research_agent.workflows.layered_d_runner import LayeredDRunner


CLIENT = Path('/data/experiments/run_experiment.py')
pytestmark = pytest.mark.skipif(not CLIENT.is_file(), reason='mount unmodified official experiment materials')


@pytest.mark.parametrize('case_id', ['D-02','D-05'])
def test_unmodified_client_calls_source_then_run_despite_public_sample_failure(tmp_path, monkeypatch, case_id):
    source = 'def run_experiment(request):\n    return {"baseline": {}, "candidate": None, "details": {}}\n'
    class Model:
        def completion(self, *args, **kwargs):
            return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':source}}]}
    protocol = json.loads((CLIENT.parent/case_id/'protocol.json').read_text())
    specs = [(m['name'],m['unit']) for m in protocol['metrics']]
    submission = LayeredDRunner._failed(case_id, specs, 'failed')
    calls = []
    class RPC(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path,payload))
            assert self.headers['Authorization'] == 'Bearer local-test-token'
            assert payload['case_id'] == case_id
            if self.path == '/source':
                assert payload['implementation_source'] == source
                result = {'source_credential':'local-fixture-only'}
            else:
                assert self.path == '/run'
                assert payload['source_credential'] == 'local-fixture-only'
                result = {'status':'complete','case_id':case_id,'submission':submission}
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    socket = tmp_path/'rpc.sock'
    server = UnixStreamServer(str(socket),RPC)
    thread = Thread(target=server.serve_forever,daemon=True)
    thread.start()
    for key, value in dict(RESEARCH_EXPERIMENT_CASE=case_id,RESEARCH_EXPERIMENT_URL='unix://'+str(socket),
        RESEARCH_EXPERIMENT_TOKEN='local-test-token',RESEARCH_LAYERED_D='layered-d-v3',
        RESEARCH_D_SOURCE_PROVENANCE='d-model-source-v1',RESEARCH_WORKSPACE_TEAM='runtime-id').items():
        monkeypatch.setenv(key,value)
    runner = LayeredDRunner(Settings(CLIENT.parent,tmp_path/'out','runtime-id'),tmp_path/'work',Model())
    prepare = runner.prepare_checked_implementation
    def failed_test(*args):
        raise ValueError('public example constraint mismatch')
    monkeypatch.setattr(runner,'prepare_checked_implementation',lambda c: prepare(c,tester=failed_test))
    try:
        with CaseSession(case_id,170).activate():
            result = runner.run(CaseContext.from_dict(case(case_id)),specs)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    assert [path for path,_ in calls] == ['/source','/run']
    assert result == submission
    assert runner.proof['submission'] == result
    assert json.loads((runner.work/'platform-submission.json').read_text()) == result
    assert json.loads((runner.work/'public-selftest.json').read_text())['public_selftest_passed'] is False
