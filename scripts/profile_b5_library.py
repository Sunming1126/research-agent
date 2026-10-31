"""B5 public-library cold parse/recall performance only; no model or relevance score."""
import json,time,resource
from pathlib import Path
from research_agent.core.config import Settings
from research_agent.core.session import CaseSession
from research_agent.rag.materials import DocumentIndex
from research_agent.rag.retrieval import HybridRetriever
from research_agent.workflows.b_evidence import recall
start=time.monotonic();out=Path('/profile');cache=out/'cold-library-cache'
if cache.exists():raise RuntimeError('Use a clean profile directory; do not mislabel warm cache')
s=Settings(workspace=Path('/data'),output=out,material_roots=[Path('/data/papers')])
with CaseSession('B-profile',170).activate():
    idx=DocumentIndex(s,cache);idx.parser.extract_tables=False
    mats=idx.resolver.resolve(['/data/papers/*.pdf'],pdf_only=True,diagnostics=idx.errors)
    pages=2 if len(mats)>=250 else 4 if len(mats)>=50 else 8
    cs=idx.build(['/data/papers/*.pdf'],page_limit=pages,resolved=mats,tolerate_pdf_errors=True)
    parsed=time.monotonic();index=HybridRetriever(cs)
    rows=recall(index,[('qa','open domain question answering dense passage retrieval'),('reflection','learning reflection tokens self critique')],24)
    result={'documents':len(mats),'chunks':len(cs),'page_limit':pages,'parse_seconds':round(parsed-start,3),'total_seconds':round(time.monotonic()-start,3),'max_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1),'candidates':len(rows),'material_errors':idx.errors,'network':False,'model_calls':0,'note':'Performance only; not recall accuracy validation'}
(out/'large-library-profile.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
