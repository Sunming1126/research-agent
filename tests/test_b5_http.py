"""B5 在原生SDK下核对恢复、失败标志和实际JSON落盘。"""
import hashlib
import json
from unittest.mock import patch
import pytest
pytest.importorskip('agenthack')
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from agenthack.telemetry import Telemetry
from agenthack import AgentRuntime
from agent import ResearchAgent
from research_agent.integrations.http_runtime import install_research_protocol
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.integrations.llm import ModelError,ModelQuotaError
from test_dispatch_v3 import _write_v3_dispatch
from fixtures import case,papers,FakeLLM

@pytest.mark.parametrize('mode',['normal','transient','quota','bad_evidence'])
def test_b_contract_and_failure_trace(tmp_path,mode):
    output=tmp_path/'output';_write_v3_dispatch(output)
    names=papers(tmp_path/'data/papers');payload=case('B-01',names)
    assigned=output/'data/team/assigned/B-01.json';assigned.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    path=output/'data/team/dispatch.json';dispatch=json.loads(path.read_text());row=next(r for r in dispatch['cases'] if r['case_id']=='B-01')
    row.update(title=payload['title'],question_sha256=hashlib.sha256(assigned.read_bytes()).hexdigest());path.write_text(json.dumps(dispatch),encoding='utf-8')
    class Model(FakeLLM):
        count=0
        def json(self,stage,data,**kwargs):
            if stage=='retrieve':
                self.count+=1
                if mode=='quota':raise ModelQuotaError('quota denied')
                if mode=='transient' and self.count==1:raise ModelError('model transport timeout')
            value=super().json(stage,data,**kwargs)
            if stage=='retrieve' and mode=='bad_evidence':
                for item in value['papers']:item['evidence_ids']=['invented-source-id']
            return value
    model=Model();exporter=InMemorySpanExporter();provider=TracerProvider();provider.add_span_processor(SimpleSpanProcessor(exporter))
    env={'RESEARCH_WORKSPACE_ROOT':str(tmp_path/'data'),'RESEARCH_RESULT_ROOT':str(output),'RESEARCH_WORKSPACE_TEAM':'team','TEAM_ID':'test','CHALLENGE_SESSION_ID':'b-session'}
    try:
        with patch.dict('os.environ',env,clear=True):
            engine=ResearchAgentEngine(Settings.from_env(),lambda:model)
            runtime=AgentRuntime(ResearchAgent(engine),telemetry=Telemetry(provider));install_research_protocol(runtime)
            with TestClient(runtime.app) as client:response=client.post('/solve',json=payload)
        assert response.status_code==200,response.text
        body=response.json();assert set(body)=={'case_id','papers'}
        assert json.loads((output/'data/team/B-01.json').read_text(encoding='utf-8'))==body
        spans=[s for s in exporter.get_finished_spans() if s.name=='agent.session']
        if mode in ('quota','bad_evidence'):
            assert body['papers']==[] and response.headers['x-research-outcome']=='failed'
            assert spans[-1].attributes['success'] is False
        else:
            assert len(body['papers'])==5
            assert spans[-1].attributes['success'] is True
        assert model.count=={'normal':3,'transient':4,'quota':1,'bad_evidence':4}[mode]
    finally:provider.shutdown()
