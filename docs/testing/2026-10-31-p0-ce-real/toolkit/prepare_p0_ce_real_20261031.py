"""Freeze a real-paper C/E protocol before native Harness execution; no model calls."""
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import time
import uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
REPO=ROOT/'协同开发/research-agent-stability-20261027'
def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def freeze(revision,path):
    data=subprocess.check_output(['git','archive','--format=tar',revision,'--','src','agent.py','pyproject.toml','requirements.txt'],cwd=REPO)
    path.mkdir(parents=True,exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for item in archive:
            if not item.isfile():continue
            target=path/item.name
            assert target.resolve().is_relative_to(path.resolve())
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.extractfile(item).read())
    return {p.relative_to(path).as_posix():sha(p) for p in [path/'agent.py',*sorted((path/'src').rglob('*.py'))]}
study='p0-ce-real-20261031-'+time.strftime('%H%M%S')+'-'+uuid.uuid4().hex[:6]
out=ROOT/'镜像测试/runs'/study
out.mkdir(parents=True,exist_ok=False)
baseline='316c2d2c1b5c96c59b09fe906356f18d8d0364d8'
candidate=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
assert candidate=='96bf4be1db2a804a1bfceb797da16af174a39c2c'
sources={name:{'revision':revision,'path':str(out/'source'/name),
               'files':freeze(revision,out/'source'/name)} for name,revision in [('baseline',baseline),('candidate',candidate)]}
previous=json.loads((ROOT/'镜像测试/quality-20261029/benchmark.json').read_text(encoding='utf-8'))
old={r['task']['case_id']:r['task'] for r in previous['cases']}
c=json.loads(json.dumps(old['C-21']));c['case_id']='C-01'
e=json.loads(json.dumps(old['E-21']));e['case_id']='E-01'
c2={**c,'case_id':'C-03','title':'原文引用与方法归属核验',
    'input':'围绕知识密集任务，比较给定五篇论文的检索对象、训练信号与推理控制，说明实验设置和评价口径的适用边界。关键事实标注PDF物理页码及完整文件名参考表。只陈述材料支持的事实，区分相关工作与本篇贡献，不把缺少摘录解释为论文没有开展相关工作。'}
e2={**e,'case_id':'E-02','title':'有限检索预算下的可证伪方案',
    'input':'以给定五篇论文为依据，为每个问题最多两轮检索、每轮最多十个候选段落的情境提出待验证方案。明确模块分工、训练与推理接口、冲突处理、数据划分和泄漏防护、对照与消融、量化指标及失败判据。区分已有论文结果与本方案的拟议参数，不声称已完成实验或全球首创。'}
cases=[c,c2,e,e2]
assert all(r['materials']==c['materials'] for r in cases) and len(c['materials'])==5
corpus=json.loads((ROOT/'镜像测试/depth-optimization-20261029/corpus.json').read_text(encoding='utf-8'))
known={r['filename']:r['sha256'] for r in corpus['papers']}
papers=ROOT/'科研资料/2026-10-29-扩充论文库/pdfs'
paper_rows=[]
for name in c['materials']:
    path=papers/name;digest=sha(path);assert digest==known[name]
    paper_rows.append({'filename':name,'path':str(path),'sha256':digest,'bytes':path.stat().st_size})
protocol={'study':study,'source':sources,'cases':cases,'papers':paper_rows,
          'input_sha256':{r['case_id']:hashlib.sha256(json.dumps(r,sort_keys=True,ensure_ascii=False).encode()).hexdigest() for r in cases},
          'provider':'magic','model':'deepseek-v4.1-flash','case_budget':{'timeout_seconds':180,'calls':30,'tokens':40000},
          'precheck':['C-01','E-01'],'paired_rounds':3,'cases_per_round_per_variant':4,
          'official_score':None,'platform_assigned_verified':False,'linux_container_acceptance':False,
          'independent_fact_assessment':'pending after generation; self-audit does not establish factual accuracy',
          'stop_on_quota':True,'stop_pair_phase_on_candidate_hard_failure':True,
          'limitations':['self-authored real-paper cases, not historical private platform inputs',
                         'cumulative P0 candidate comparison after four separate offline single-factor tests',
                         'Windows official SDK local HTTP, not Linux/OTLP/platform acceptance']}
save(out/'protocol.json',protocol)
shutil.copy2(ROOT/'.session-audit/quality-harness-foreground.png',out/'native-ui-before-call.png')
save(out/'ui-binding.json',{'session_id':'session-9c903d0e-5884-44fe-bc7f-3719da00864b',
                           'workspace_id':'c61ec5e8-9932-4ef5-9709-fed302ad7355',
                           'workspace_title':'Agent挑战赛','conversation_title':'科研助手镜像验收总控',
                           'image_sha256':sha(out/'native-ui-before-call.png'),'target_model_calls':0})
save(ROOT/'.session-audit/p0-ce-real-study-20261031.json',{'study':study,'directory':str(out)})
print(json.dumps({'study':study,'cases':len(cases),'papers':len(paper_rows),'target_model_calls':0},ensure_ascii=True))
