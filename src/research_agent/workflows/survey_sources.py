"""C-only balanced source retrieval. Excerpts are source text, not verified claims."""
import re
from collections import defaultdict

from ..core.models import Evidence
from ..core.utils import source_quote_matches, tokenize
from ..evidence import EvidenceError
from ..rag.retrieval import QueryPlan
from ..rag.report_selection import own_method_score

FACETS = {
    'method': 'method architecture model formulation training variants mechanism 方法 机制 变体',
    'result': 'experiments results evaluation dataset benchmark comparison accuracy performance 实验 数据 指标 结果',
    'limitation': 'limitations discussion failure assumptions computational cost robustness 局限 失败 假设 成本',
}
SECTION = {
    'method': r'method|model|approach|architecture|方法|模型',
    'result': r'experiment|result|evaluation|dataset|实验|结果|评估',
    'limitation': r'limitation|discussion|analysis|局限|讨论|分析',
}


def excerpt(chunk, focus, maximum=850):
    """Keep sentence boundaries where possible; never concatenate disjoint quotes."""
    text = chunk.text.strip()
    if len(text) <= maximum:
        return text
    starts = [0] + [m.end() for m in re.finditer(r'[.!?。！？]\s+|\n\s*\n', text)]
    terms = set(tokenize(focus))
    windows = []
    for start in starts:
        if start >= len(text) - 80:
            continue
        end = min(len(text), start + maximum)
        boundaries = [m.end() for m in re.finditer(r'[.!?。！？](?:\s|$)', text[start:end])]
        if boundaries and boundaries[-1] >= maximum // 2:
            end = start + boundaries[-1]
        window = text[start:end].strip()
        score = len(terms & set(tokenize(window)))
        windows.append((score, min(len(window), 500), -start, window))
    return max(windows)[-1] if windows else text[:maximum]


def survey_ledger(case, retriever, plan, selected):
    """Reserve two method/result passages per paper before topic/background text."""
    question = ' '.join([case.title, case.input, *case.constraints, case.expected_output])
    groups = defaultdict(list)
    for chunk in retriever.chunks:
        if (chunk.filename.lower().endswith('.pdf') and chunk.kind in {'text', 'table'}
                and chunk.page is not None and len(chunk.text.strip()) >= 160):
            groups[chunk.filename].append(chunk)
    ledger, sources = [], []
    names = sorted({c.filename for c in retriever.chunks if c.filename.lower().endswith('.pdf')})
    # At five papers this stays close to the previous 20k-character source budget.
    per_paper = min(4200, 24000 // max(1, len(names)))
    for name in names:
        available = groups[name]
        if not available:
            # Some readable PDFs are extracted one line at a time. Do not turn
            # a quality preference for longer paragraphs into a missing-paper error.
            available = [c for c in retriever.chunks if c.filename == name and c.kind == 'text'
                         and c.page is not None and len(c.text.strip()) >= 40]
        chosen, seen = [], set()
        def add(chunk, facet):
            if chunk.chunk_id not in seen:
                chosen.append((chunk, facet))
                seen.add(chunk.chunk_id)
        for facet, count in (('method', 2), ('result', 2), ('limitation', 1)):
            hits = retriever.search(QueryPlan(FACETS[facet]), filename=name, top_k=24)
            scores = {h.chunk.chunk_id: h.score for h in hits}
            candidates = [c for c in available if c.chunk_id not in seen and
                          (c.chunk_id in scores or (facet == 'method' and own_method_score(c) >= 2))]
            def priority(c):
                related = bool(re.search(r'related work|prior work|相关工作', c.heading + ' ' + c.text[:80], re.I))
                section = bool(re.search(SECTION[facet], c.heading, re.I))
                own = max(0, own_method_score(c)) if facet == 'method' else 0
                # Generic abstract mentions should not outrank substantive sections.
                complete_start = bool(re.match(r'[A-Z\u4e00-\u9fff]', c.text.lstrip()))
                return (not related, min(own, 5), complete_start, section, c.page > 1,
                        scores.get(c.chunk_id, 0), -c.start)
            for chunk in sorted(candidates, key=priority, reverse=True)[:count]:
                add(chunk, facet)
        # At least one task-focused source, including requirements outside input.
        hits = retriever.search(QueryPlan(question), filename=name, top_k=16)
        for chunk in [*(h.chunk for h in hits), *selected, *available]:
            if chunk.filename == name and chunk in available and chunk.chunk_id not in seen:
                add(chunk, 'unspecified')
                break
        used = 0
        for index, (chunk, facet) in enumerate(chosen):
            # Fair quotas keep late facets from being starved by long method passages.
            limit = min(900, max(80, (per_paper-used)//max(1,len(chosen)-index)))
            quote = excerpt(chunk, question + ' ' + FACETS.get(facet, ''), limit)
            if not source_quote_matches(quote, chunk.text):
                raise EvidenceError('Survey excerpt is not continuous authorized text')
            ledger.append(Evidence(f'ev-{len(ledger)+1}', quote, name, chunk.page, chunk.chunk_id,
                                   quote, kind='source_context', facet=facet,
                                   role='source_context', verification='verbatim_only'))
            sources.append(chunk)
            used += len(quote)
    if set(names) != {e.filename for e in ledger}:
        raise EvidenceError('authorized paper has no readable Survey source context')
    return ledger, sources
