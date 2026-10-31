"""Build B6 strictly on the previously verified E4 image; do not recopy frozen category modules."""
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE='research-assistant:0.4.13-test1-2-e4-20261031'
BASE_ID='sha256:e708156c5f1d912063e7b6a7f8c0529f385e8eddad50d362e98a98a5f84b587a'
IMAGE='research-assistant:0.4.13-test1-2-b6-20261031'
RECIPE=f'''FROM {BASE}
COPY --chown=agent:agent src/research_agent/workflows/tasks.py /app/src/research_agent/workflows/tasks.py
COPY --chown=agent:agent src/research_agent/integrations/prompts.py src/research_agent/integrations/b_prompt.py /app/src/research_agent/integrations/
COPY --chown=agent:agent src/research_agent/workflows/b_*.py /app/src/research_agent/workflows/
'''
if __name__=='__main__':
    base=json.loads(subprocess.check_output(['docker','image','inspect',BASE],text=True,encoding='utf-8'))[0]
    assert base['Id']==BASE_ID,'Load the verified E4 tar before building; do not silently replace the base'
    subprocess.run(['docker','build','-t',IMAGE,'-f','-','.'],input=RECIPE,text=True,encoding='utf-8',cwd=ROOT,check=True)
