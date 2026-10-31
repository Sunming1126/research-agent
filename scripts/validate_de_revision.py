"""在固定镜像中离线运行回归；测试依赖与算法夹具仅以只读卷挂载。"""
import argparse
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TESTS=['d_isolated_revision','cd2_isolation','public_selftest','source_generation','layered_d_runner',
       'eval_20261026_regressions','survey_input_contract','report_resilience','idea_runtime_regressions',
       'report_routing','report_budget_history','report_patch','report_fix_regressions','report_evidence_selection']

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--image',required=True)
    parser.add_argument('--sdk',action='store_true')
    args=parser.parse_args()
    output=ROOT/'output/de3-20261031'
    (output/'prompts-before.py').write_bytes(subprocess.check_output(['git','show','04216cf:src/research_agent/integrations/prompts.py'],cwd=ROOT))
    materials=ROOT/'科研助手赛题材料/research-experiments-20261020-source-v1'
    command=['docker','run','--rm','--network','none','--cpus','1','--memory','1024m',
             '-e','RESEARCH_RUNTIME='+('sdk' if args.sdk else 'local'),
             '-e','PYTEST_DISABLE_PLUGIN_AUTOLOAD=1','-e','PYTHONPATH=/app/src:/tests:/testdeps']
    for source,target in [(ROOT/'src','/app/src'),(ROOT/'tests','/tests'),(output/'testdeps','/testdeps'),
                          (materials,'/materials'),(materials,'/app/科研助手赛题材料/x新/research-experiments-20261020-source-v1')]:
        command+=['--mount',f'type=bind,source={source},target={target},readonly']
    command+=['--mount',f'type=bind,source={output},target=/results','--entrypoint','python',args.image,'-m','pytest','-q','-p','no:cacheprovider']
    if args.sdk:
        command+=['/tests/test_http_runtime.py','--junitxml=/results/http-final.xml']
    else:
        command+=['/tests/test_'+name+'.py' for name in TESTS]
        command+=['--junitxml=/results/linux-final.xml']
    raise SystemExit(subprocess.call(command))

if __name__=='__main__':main()
