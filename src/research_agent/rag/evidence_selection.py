"""B检索证据覆盖与输入预算；只选择已有授权原文，不生成论文事实。"""
import json

from ..core.utils import tokenize


def select_evidence(hits, queries, limit=3):
    """贪心补足不同条件，避免同一段落的重叠切片占满证据位。"""
    rows = list({h.chunk.chunk_id: h for h in reversed(hits)}.values())
    facets = [set(tokenize(q)) for q in queries if isinstance(q, str) and q.strip()]
    facets = [s for i, s in enumerate(facets) if s and s not in facets[:i]]
    tokens = {h.chunk.chunk_id: set(tokenize(h.chunk.text)) for h in rows}
    selected = []
    covered = [set() for _ in facets]
    while rows and len(selected) < limit:
        def priority(hit):
            terms = tokens[hit.chunk.chunk_id]
            gain = sum(len((terms & facet) - old) / len(facet) for facet, old in zip(facets, covered))
            similarity = max((len(terms & old) / max(1, len(terms | old)) for old in seen_tokens), default=0.)
            # 分值仅作同覆盖/多样性下的决胜，不将跨查询词法分直接相加。
            return (round(gain, 8), -round(similarity, 8), getattr(hit, 'score', 0),
                    -(hit.chunk.page or 0), -hit.chunk.start)
        seen_tokens = [tokens[h.chunk.chunk_id] for h in selected]
        best = max(rows, key=priority)
        rows.remove(best)
        selected.append(best)
        terms = tokens[best.chunk.chunk_id]
        for index, facet in enumerate(facets):
            covered[index].update(terms & facet)
    return selected


def query_window(text, queries, maximum=1100):
    """保留命中附近连续原文；返回值始终是原文子串，不拼接摘录。"""
    if len(text) <= maximum:
        return text
    terms = set(tokenize(' '.join(q for q in queries if isinstance(q, str))))
    folded = text.casefold()
    positions = sorted({p for term in terms if len(term) >= 2
                        for p in [folded.find(term.casefold())] if p >= 0})
    if not positions:
        return text[:maximum]
    starts = {0, max(0, len(text) - maximum)}
    for position in positions:
        starts.add(max(0, min(len(text) - maximum, position - maximum // 4)))
    def coverage(start):
        window = folded[start:start + maximum]
        return sum(term.casefold() in window for term in terms)
    start = max(sorted(starts), key=coverage)
    return text[start:start + maximum]


def evidence_batches(candidates, task, llm, *, max_papers=8, max_input_tokens=11000):
    """按实际序列化输入估计分批，短证据允许多篇，长证据缩批。"""
    current = []
    for candidate in candidates:
        proposed = current + [candidate]
        payload = {'task': task, 'candidates': proposed}
        estimator = getattr(llm, 'input_tokens', None)
        estimate = estimator('retrieve', payload) if callable(estimator) else (len(json.dumps(payload, ensure_ascii=False)) + 1) // 2 + 1000
        if current and (len(proposed) > max_papers or estimate > max_input_tokens):
            yield current
            current = [candidate]
        else:
            current = proposed
    if current:
        yield current
