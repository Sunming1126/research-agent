"""真实SDK/引擎下测试E失败恢复；模型故障注入，不访问外部接口。"""
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
from research_agent.integrations.llm import ModelError, ModelQuotaError
from test_eval_20261026_regressions import DirectLLM
from test_dispatch_v3 import _write_v3_dispatch
from fixtures import case,papers


@pytest.mark.parametrize('mode,status',[('recover',200),('quota',500),('always_invalid',500)])
def test_e_http_recovery_and_real_failure_status(tmp_path,mode,status):
    output=tmp_path/'output';_write_v3_dispatch(output)
    names=papers(tmp_path/'data/papers')[:2];payload=case('E-02',names)
    assigned=output/'data/team/assigned/E-02.json';assigned.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    dispatch_path=output/'data/team/dispatch.json';dispatch=json.loads(dispatch_path.read_text())
    row=next(row for row in dispatch['cases'] if row['case_id']=='E-02')
    row.update(title=payload['title'],question_sha256=hashlib.sha256(assigned.read_bytes()).hexdigest())
    dispatch_path.write_text(json.dumps(dispatch),encoding='utf-8')
    class Model(DirectLLM):
        def __init__(self):super().__init__();self.drafts=0
        def json(self,stage,data,**kwargs):
            if stage=='report':
                self.drafts+=1
                if mode=='quota':raise ModelQuotaError('quota rejected')
                if mode=='always_invalid':raise ModelError('model returned invalid JSON')
                if self.drafts==1:raise ModelError('model response incomplete')
            return super().json(stage,data,**kwargs)
    llm=Model();exporter=InMemorySpanExporter();provider=TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    env={'RESEARCH_WORKSPACE_ROOT':str(tmp_path/'data'),'RESEARCH_RESULT_ROOT':str(output),
         'RESEARCH_WORKSPACE_TEAM':'team','TEAM_ID':'test','CHALLENGE_SESSION_ID':'e-session'}
    try:
        with patch.dict('os.environ',env,clear=True):
            engine=ResearchAgentEngine(Settings.from_env(),lambda:llm)
            runtime=AgentRuntime(ResearchAgent(engine),telemetry=Telemetry(provider));install_research_protocol(runtime)
            with TestClient(runtime.app) as client:response=client.post('/solve',json=payload)
        assert response.status_code==status,response.text
        saved=output/'data/team/E-02.json'
        assert response.headers['x-agenthack-trace-id']
        if status==200:
            body=response.json();assert set(body)=={'case_id','path','markdown'}
            assert json.loads(saved.read_text(encoding='utf-8'))==body
            assert (output/body['path']).read_text(encoding='utf-8')==body['markdown']
            assert llm.drafts==2
        else:
            assert not saved.exists()
            assert llm.drafts==(1 if mode=='quota' else 2)
    finally:provider.shutdown()
