"""B检索证据覆盖与输入预算；只选择已有授权原文，不生成论文事实。"""
import json
import re
from collections import Counter

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
    if maximum < 1:
        raise ValueError('evidence window must be positive')
    terms = set(tokenize(' '.join(q for q in queries if isinstance(q, str))))
    events = []
    for term in sorted(terms):
        if len(term) < 2 or len(term) > maximum:
            continue
        pattern = re.escape(term)
        if re.fullmatch(r'[a-z0-9_+.#/-]+', term):
            pattern = r'(?<![A-Za-z0-9_])' + pattern + r'(?![A-Za-z0-9_])'
        events.extend((m.start(), m.end(), term) for m in re.finditer(pattern, text, re.I))
    events.sort()
    if not events:
        return text[:maximum]
    # 所有原文命中一起滑窗，避免首次命中在背景段时掩盖后文的组合条件。
    counts, left, right_end = Counter(), 0, 0
    best = None
    for right, (_, end, term) in enumerate(events):
        counts[term] += 1
        right_end = max(right_end, end)
        while left <= right and right_end - events[left][0] > maximum:
            old = events[left][2]
            counts[old] -= 1
            if not counts[old]:
                del counts[old]
            left += 1
        if left > right:
            continue
        span_start, span_end = events[left][0], right_end
        key = (len(counts), -(span_end - span_start), -span_start)
        if best is None or key > best[0]:
            best = (key, span_start, span_end)
    if best is None:
        return text[:maximum]
    _, span_start, span_end = best
    start = max(0, min(len(text) - maximum, span_start - (maximum - (span_end - span_start)) // 2))
    # 在预算和全部命中都能保留时，向前靠近句界；始终保留连续原文。
    lower = max(0, span_end - maximum, start - 160)
    boundaries = [m.end() for m in re.finditer(r'(?:[。！？.!?]\s+|\n+)', text[lower:start])]
    if boundaries:
        start = lower + boundaries[-1]
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
