"""公开论文、自拟题的对照验证；不是赛事题面或官方金标。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

p=argparse.ArgumentParser()
p.add_argument('--source',required=True)
p.add_argument('--env-file',required=True)
p.add_argument('--pdfs',required=True)
p.add_argument('--out',required=True)
args=p.parse_args()
sys.path.insert(0,str(Path(args.source)/'src'))
from research_agent.core.models import CaseContext,Chunk
from research_agent.core.session import CaseSession
from research_agent.integrations.llm import GatewayLLM
from research_agent.rag.retrieval import HybridRetriever
from research_agent.workflows.tasks import TaskHandlers
import fitz

for line in Path(args.env_file).read_text(encoding='utf-8-sig').splitlines():
    if '=' not in line or line.startswith('#'):continue
    key,value=line.split('=',1)
    if key.startswith(('CHALLENGE_LLM_','PLATFORM_LLM_')) or key in ('PLATFORM_API_KEY','CHALLENGE_SESSION_ID','AGENT_MODEL_NAME'):
        os.environ[key]=value.strip().strip('"').strip("'")
names=['2004.04906-DPR.pdf','2005.11401-RAG.pdf','2007.01282-FiD.pdf',
       '2112.04426-RETRO.pdf','2310.11511-Self-RAG.pdf','2104.08663-BEIR.pdf','2203.02155-InstructGPT.pdf']
questions=[
 ('B-01','找问答研究：将问题分别与每个证据段落拼接后独立编码，再将这些表示共同交给单一生成解码器。必须实际使用这一联合解码机制；只对各证据分别生成并混合概率不满足。重点关注多证据整合，但不要求提出新的检索器。不限定篇数。',['2007.01282-FiD.pdf']),
 ('B-02','分别寻找两个目标，任一目标满足即可：（一）以通用检索基准系统比较不同领域的零样本迁移能力；（二）学习特殊反思符号来决定检索时机并判断生成是否有证据支持。各目标内部描述须同时满足。排除仅作相关工作引用而没有实际研究该机制的论文，不限篇数。',['2104.08663-BEIR.pdf','2310.11511-Self-RAG.pdf']),
 ('B-03','查找使用卫星热红外观测实测城市树冠覆盖率与地表温度关系的原创研究。只讨论自然语言检索或文本生成不符合。不限篇数。',[]),
]
out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
frozen={'kind':'developer-authored public-paper checks, not official answers','questions':questions,
        'pdf_sha256':{n:hashlib.sha256((Path(args.pdfs)/n).read_bytes()).hexdigest() for n in names}}
(out/'frozen.json').write_text(json.dumps(frozen,ensure_ascii=False,indent=2),encoding='utf-8')
chunks=[]
for name in names:
    with fitz.open(Path(args.pdfs)/name) as pdf:
        for page in pdf:
            text=page.get_text()
            for start in range(0,len(text),1400):
                part=text[start:start+1800]
                if part.strip():chunks.append(Chunk(f'{name}:{page.number+1}:{start}',name,page.number+1,'',part,'text','public',start=start))
results=[]
for cid,q,expected in questions:
    case=CaseContext(cid,'Literature_Retrieval','Public independently specified retrieval',q,[],[],'Return filenames and specific reasons','Literature_Retrieval')
    llm=GatewayLLM(token_budget=float('inf'),timeout=530)
    workflow=TaskHandlers(llm,'validation');workflow.document_expander=lambda ns:[c for c in chunks if c.filename in ns]
    begin=time.monotonic()
    try:
        with CaseSession(cid,530).activate():
            result=workflow.handle(case,HybridRetriever([c for c in chunks if c.page<=8]))
        got={r['filename'] for r in result['papers']};gold=set(expected)
        row={'case':cid,'expected':expected,'returned':sorted(got),'missing':sorted(gold-got),'extra':sorted(got-gold),
             'exact':got==gold,'answer':result,'trace':workflow.retrieval_trace}
    except Exception as exc:
        row={'case':cid,'error_type':type(exc).__name__,'error':str(exc)[:200],'trace':workflow.retrieval_trace}
    row.update(seconds=round(time.monotonic()-begin,2),calls=llm.calls,tokens=llm.total_tokens)
    results.append(row)
    (out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in row.items() if k not in ('trace','answer')},ensure_ascii=True),flush=True)
