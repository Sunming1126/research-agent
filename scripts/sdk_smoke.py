"""验证 SDK 在 D 类缺少实验会话时如实返回材料缺失。"""
import json
from pathlib import Path
from fastapi.testclient import TestClient
from agent import AgentRuntime, ResearchAgent

runtime = AgentRuntime(ResearchAgent())
payload = {"task_id":"final-sdk-check", "input":{
    "case_id":"D-01", "function":"Reproduction", "title":"Protocol smoke test",
    "input":"name=Macro-F1, unit=分数; name=Accuracy, unit=%; name=运行时间, unit=秒。",
    "materials":[], "constraints":[], "expected_output":"three metrics and figures",
    "output_schema_ref":"Reproduction"}}
with TestClient(runtime.app) as client:
    health = client.get('/health').json()
    response = client.post('/solve',json=payload).json()
assert health['status'] == 'healthy'
assert response['status'] == 'success', response
case = json.loads(response['output']['answer'])
assert case['status'] == 'materials_missing'
assert len(case['metrics']) == 3
assert response['metadata']['tool_call_count'] > 0
Path('/results/final-sdk-check.json').write_text(json.dumps({'health':health,'response':response},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'health':health['status'],'sdk':response['status'],'case':case['status'],'tools':response['metadata']['tool_call_count']}))
