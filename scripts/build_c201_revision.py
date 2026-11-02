"""Build C-only recovery on the exact B6/2.0.0 runtime image."""
import json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE='research-assistant:2.0.0'
EXPECTED='sha256:23c2ce558c773a5c93d69db0f14be292a89f09808e2b1e7036d37cb81113b3d1'
IMAGE='research-assistant:2.0.1-c1'
if __name__=='__main__':
    actual=json.loads(subprocess.check_output(['docker','image','inspect',BASE]))[0]['Id']
    assert actual==EXPECTED,actual
    recipe=f"FROM {BASE}\nCOPY --chown=agent:agent src/research_agent/workflows/reports.py src/research_agent/workflows/survey_repair.py /app/src/research_agent/workflows/\nCOPY --chown=agent:agent src/research_agent/integrations/prompts.py src/research_agent/integrations/survey_prompt.py /app/src/research_agent/integrations/\n"
    subprocess.run(['docker','build','-t',IMAGE,'-f','-','.'],input=recipe,text=True,cwd=ROOT,check=True)
