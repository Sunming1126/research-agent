"""E 专用证据选取：机制、基准、指标、瓶颈、题目焦点分配固定额度。"""
import re
from ..core.models import Evidence
from ..core.utils import source_quote_matches
from ..evidence import EvidenceError
from ..evidence.reporting import focused_excerpt
from ..rag.retrieval import QueryPlan
from ..rag.report_selection import own_method_score

FACETS = (
    ('method', 2, 'method mechanism architecture input output training 方法 机制', r'method|model|approach|architecture|方法|模型'),
    ('data', 1, 'dataset benchmark corpus evaluation tasks 数据 基准', r'dataset|experiment|evaluation|数据|实验'),
    ('metric', 1, 'evaluation metric ablation baseline measurement 实验 消融 指标', r'evaluation|result|experiment|实验|结果'),
    ('limitation', 1, 'limitation failure computational cost latency assumptions 局限 瓶颈 失败', r'limitation|discussion|analysis|局限|分析'),
)


def idea_ledger(case, retriever, selected):
    question = ' '.join([case.title, case.input, *case.constraints, case.expected_output])
    names = sorted({c.filename for c in retriever.chunks if c.filename.lower().endswith('.pdf')})
    allowance = min(4200, 22000//max(1,len(names)))
    ledger, sources = [], []
    for name in names:
        available = [c for c in retriever.chunks if c.filename == name and c.kind in {'text','table'}
                     and c.page is not None and len(c.text.strip()) >= 40]
        seen, chosen = set(), []
        for facet, count, query, section in FACETS:
            hits = retriever.search(QueryPlan(query), filename=name, top_k=24)
            scores = {h.chunk.chunk_id:h.score for h in hits}
            candidates = [c for c in available if c.chunk_id not in seen and
                          (c.chunk_id in scores or re.search(section,c.heading,re.I))]
            def key(c):
                related = bool(re.search(r'related work|prior work|相关工作',c.heading+' '+c.text[:100],re.I))
                return (not related, bool(re.search(section,c.heading,re.I)),
                        max(0,own_method_score(c)) if facet=='method' else 0, scores.get(c.chunk_id,0))
            for c in sorted(candidates,key=key,reverse=True)[:count]:
                chosen.append((c,facet,query));seen.add(c.chunk_id)
        hits = retriever.search(QueryPlan(question), filename=name, top_k=12)
        for c in [*(h.chunk for h in hits), *selected, *available]:
            if c.filename == name and c in available and c.chunk_id not in seen:
                chosen.append((c,'unspecified',question));break
        remaining = allowance
        for i,(c,facet,query) in enumerate(chosen):
            maximum = min(850, max(1,remaining//(len(chosen)-i)))
            quote = focused_excerpt(c, question+' '+query, maximum)
            if not source_quote_matches(quote,c.text):
                raise EvidenceError('Idea excerpt must be continuous authorized text')
            ledger.append(Evidence(f'ev-{len(ledger)+1}',quote,name,c.page,c.chunk_id,quote,
                kind='source_context',facet=facet,role='source_context',verification='verbatim_only'))
            sources.append(c);remaining -= len(quote)
    if set(names) != {e.filename for e in ledger}:
        raise EvidenceError('authorized paper has no readable Idea source context')
    return ledger,sources
