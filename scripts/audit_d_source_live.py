"""Official model + published samples; does not claim formal RPC/provenance admission."""
import json
import time
from pathlib import Path
from research_agent.core.config import Settings
from research_agent.core.models import CaseContext
from research_agent.core.session import CaseSession
from research_agent.integrations.llm import GatewayLLM
from research_agent.workflows.layered_d_runner import LayeredDRunner
from research_agent.workflows.source_generation import extract_source
from research_agent.core.utils import sha256_file, write_json

root = Path('/data/experiments')
output = Path('/audit')
rows = []
for case_id in ('D-01','D-02','D-05'):
    protocol = json.loads((root/case_id/'protocol.json').read_text())
    text = '按照当前公开方法和协议实现实验。核心指标：' + '；'.join(m['name']+'（'+m['unit']+'）' for m in protocol['metrics'])
    case = CaseContext.from_dict(dict(case_id=case_id,function='Reproduction',title='Public sample audit',input=text,
        materials=['data/experiments/'+case_id],constraints=[],expected_output='Reproduction',output_schema_ref='Reproduction'))
    llm = GatewayLLM(timeout=170)
    settings = Settings(Path('/data'),output,'local-public-audit',material_roots=[root/case_id])
    work = output/case_id
    runner = LayeredDRunner(settings,work,llm)
    started = time.monotonic()
    row = dict(case_id=case_id,official_model_api=True,formal_platform_session=False)
    try:
        with CaseSession(case_id,170).activate():
            source = runner.prepare_checked_implementation(case)
        response = json.loads((work/'generation-response.json').read_text())
        assert source.read_text() == extract_source(response)
        row.update(status='passed_public_sample',source_sha256=sha256_file(source),source_matches_current_response=True)
    except Exception as exc:
        row.update(status='failed',error_type=type(exc).__name__,message=str(exc)[:600])
    row.update(seconds=round(time.monotonic()-started,3),usage=llm.usage)
    rows.append(row)
    write_json(output/'summary.json',rows)
    print(json.dumps(row,ensure_ascii=False),flush=True)
if any(r['status'] != 'passed_public_sample' for r in rows):
    raise SystemExit(1)
