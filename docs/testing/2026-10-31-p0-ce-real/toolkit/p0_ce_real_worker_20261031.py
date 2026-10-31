"""Real requests only under fresh original Harness native orchestration proof."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
SID='session-9c903d0e-5884-44fe-bc7f-3719da00864b'
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,required=True);args=parser.parse_args()
    directory=args.directory.resolve();assert directory.is_relative_to(ROOT/'镜像测试/runs')
    config=json.loads((directory/'job.json').read_text(encoding='utf-8'))
    study=Path(config['study_directory']);protocol=json.loads((study/'protocol.json').read_text(encoding='utf-8'))
    proof=json.loads(Path(config['native_proof']).read_text(encoding='utf-8'))
    assert os.environ.get('ANIC_NATIVE_ORCHESTRATOR_SESSION_ID')==SID and proof['session_id']==SID
    assert proof['native_tool_execution_verified'] and proof['magic_selection_verified']
    from magic_api_bridge import MagicAPI
    from research_agent.core.engine import ResearchAgentEngine
    from research_agent.core.config import Settings
    from research_agent.integrations.llm import GatewayLLM,ModelError,ModelQuotaError,_RetryableError
    from research_agent.core.session import remaining_timeout
    from research_agent.contracts.validation import validate_wire_fields
    from research_agent.core.models import CaseContext
    from agent import ResearchAgent
    from agenthack import AgentRuntime,llm_call
    from agenthack.telemetry import Telemetry
    from fastapi.testclient import TestClient
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    import research_agent
    source=Path(protocol['source'][config['variant']]['path'])
    assert Path(research_agent.__file__).resolve().is_relative_to(source)
    for rel,digest in protocol['source'][config['variant']]['files'].items():
        assert hashlib.sha256((source/rel).read_bytes()).hexdigest()==digest
    for row in protocol['papers']:
        assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()==row['sha256']
    models=[]
    class LiveLLM(GatewayLLM):
        def __init__(self):
            budget=protocol['case_budget'];super().__init__(budget['calls'],budget['tokens'],budget['timeout_seconds'])
            self.api=MagicAPI();assert self.api.model==protocol['model']
            self.model=self.api.model;self.base_url='native-memory-adapter';self.api_key='unused-local-marker'
            self.route_fingerprint=hashlib.sha256(self.api.url.encode()).hexdigest()
            models.append(self)
        def _request_once(self,system,prompt,requested,remaining):
            payload={'model':self.model,'messages':[{'role':'system','content':system},{'role':'user','content':prompt}],
                     'max_tokens':requested,'stream':False}
            if getattr(self,'json_response',False):payload['response_format']={'type':'json_object'}
            if getattr(self,'thinking_override',None)=='disabled':payload['temperature']=0
            thinking=os.environ.get('RESEARCH_LLM_THINKING') or getattr(self,'thinking_override',None)
            if thinking in {'enabled','disabled'}:payload['thinking']={'type':thinking}
            call=directory/'model-calls'/f'{self.calls:02}'
            try:
                with llm_call(self.model,'magic',streaming=False) as operation:
                    try:
                        value,_=self.api.complete(payload,call,timeout=remaining_timeout(min(self.REQUEST_TIMEOUT_CAP,remaining)))
                    finally:
                        if (call/'call.json').is_file():
                            actual=json.loads((call/'call.json').read_text(encoding='utf-8'))
                            usage=actual.get('usage') or {}
                            operation.set(input_tokens=usage.get('prompt_tokens'),output_tokens=usage.get('completion_tokens'),
                                          finish_reason=actual.get('finish_reason'))
                return value
            except RuntimeError:
                if self.api.quota_exhausted.is_set():raise ModelQuotaError('Selected magic account quota exhausted') from None
                meta=json.loads((call/'call.json').read_text()) if (call/'call.json').is_file() else {}
                if meta.get('error_code')=='TRANSPORT_ERROR' or meta.get('http_status') in {429,500,502,503,504}:
                    raise _RetryableError('Selected magic transport failed; sanitized evidence retained') from None
                raise ModelError('Selected magic request failed; sanitized evidence retained') from None
    task=config['task'];output=directory/'output'
    settings=Settings(ROOT,output,'p0_ce_real',material_roots=[Path(protocol['papers'][0]['path']).parent],case_timeout=180)
    exporter=InMemorySpanExporter();provider=TracerProvider();provider.add_span_processor(SimpleSpanProcessor(exporter))
    engine=ResearchAgentEngine(settings,LiveLLM)
    runtime=AgentRuntime(ResearchAgent(engine),telemetry=Telemetry(provider))
    routes=[(r.path,id(r.endpoint)) for r in runtime.app.routes]
    from research_agent.integrations.http_runtime import install_research_protocol
    install_research_protocol(runtime);assert routes==[(r.path,id(r.endpoint)) for r in runtime.app.routes]
    started=time.monotonic();row={'case_id':task['case_id'],'variant':config['variant'],'phase':config['phase'],'round':config['round']}
    sdk_metadata={}
    try:
        with TestClient(runtime.app) as client:
            assert client.get('/health').status_code==200
            response=client.post('/solve',json=task,headers={'X-Research-Task-ID':config['transport_id'],
                'Idempotency-Key':config['transport_id'],'X-Session-ID':SID})
            result=response.json();save(directory/'http-response.json',result)
            trace=response.headers.get('x-agenthack-trace-id')
            sdk_metadata=json.loads(response.headers.get('x-agenthack-metadata','{}'))
            spans=exporter.get_finished_spans()
            trace_match=trace in {format(s.context.trace_id,'032x') for s in spans}
            artifact=output/f'data/p0_ce_real/{task["case_id"]}.json'
            valid=False;disk=False;markdown=False
            if response.status_code==200:
                validate_wire_fields(CaseContext.from_dict(task),result);valid=True
                disk=artifact.is_file() and json.loads(artifact.read_text(encoding='utf-8'))==result
                path=result.get('path')
                markdown=isinstance(path,str) and (output/path).is_file() and (output/path).read_text(encoding='utf-8')==result.get('markdown')
            row.update(status='completed' if response.status_code==200 and valid and disk and markdown else 'failed',
                       http_status=response.status_code,wire_fields_valid=valid,disk_matches=disk,markdown_matches=markdown,
                       sdk_trace_id=trace,sdk_trace_linked=trace_match,sdk_span_count=len(spans),
                       sdk_tool_spans=sum(s.name.startswith('tool.') or s.name.startswith('agent.tool') for s in spans),
                       error_type=result.get('error_type') if isinstance(result,dict) else None)
    except Exception as exc:
        row.update(status='failed',error_type=type(exc).__name__,disk_matches=False)
    finally:provider.shutdown()
    calls=[json.loads(p.read_text(encoding='utf-8')) for p in (directory/'model-calls').glob('*/call.json')]
    known=[c['usage']['total_tokens'] for c in calls if isinstance(c.get('usage'),dict) and type(c['usage'].get('total_tokens')) is int]
    versions={}
    for name in ['agenthack','fastapi','starlette','PyMuPDF','openai']:
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:versions[name]='metadata_unavailable'
    environment={'python':sys.version,'platform':platform.platform(),'versions':versions,
                 'sdk_runtime_sha256':hashlib.sha256(Path(sys.modules[AgentRuntime.__module__].__file__).read_bytes()).hexdigest(),
                 'provider':protocol['provider'],'model':protocol['model'],
                 'route_sha256':models[0].route_fingerprint if models else None,
                 'case_budget':protocol['case_budget'],'telemetry':'local_InMemorySpanExporter',
                 'container_limits_enforced':False,'platform_assigned_verified':False,
                 'thinking_env_override':os.environ.get('RESEARCH_LLM_THINKING') if os.environ.get('RESEARCH_LLM_THINKING') in {'enabled','disabled'} else None}
    save(directory/'environment.json',{'sha256':hashlib.sha256(json.dumps(environment,sort_keys=True).encode()).hexdigest(),'fields':environment})
    row.update(seconds=round(time.monotonic()-started,3),target_real_model_calls=len(calls),known_total_tokens=sum(known),
               total_tokens=sum(known) if len(known)==len(calls) else None,unknown_usage_calls=len(calls)-len(known),
               quota_exhausted=any(c.get('error_code') in {'AccountQuotaExceeded','insufficient_quota'} for c in calls),
               sdk_recorded_llm_calls=sdk_metadata.get('llm_calls'),
               sdk_llm_call_count_matches=sdk_metadata.get('llm_calls')==len(calls),
               model_stages=[r['stage'] for m in models for r in m.usage],native_harness_orchestrated=True,
               native_session_id=SID,official_score=None,linux_acceptance=False,
               input_sha256=hashlib.sha256(json.dumps(task,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
    save(directory/'result.json',row);print(json.dumps(row,ensure_ascii=True),flush=True)
if __name__=='__main__':main()
