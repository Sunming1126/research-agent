from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..core.models import RetrievalHit
from ..core.utils import tokenize
from ..integrations.instrumentation import tool
from ..core.session import checkpoint

MAX_SUBQUERIES = 8


def fuse_document_rankings(rankings, *, k=60, excerpts=4):
    """文档级 RRF；原词法分只作门限，跨通道排名和证据分别合并，不修改输入命中。"""
    totals, rows, evidence = defaultdict(float), {}, defaultdict(dict)
    for ranking in rankings:
        seen = set()
        for rank, (name, score, hits) in enumerate(ranking, start=1):
            if name in seen:
                continue
            seen.add(name)
            totals[name] += 1.0 / (k + rank)
            if name not in rows or score > rows[name]:
                rows[name] = score
            for hit in hits:
                old = evidence[name].get(hit.chunk.chunk_id)
                if old is None or hit.score > old.score:
                    evidence[name][hit.chunk.chunk_id] = hit
    ordered = sorted(rows, key=lambda name: (-totals[name], -rows[name], name))
    merged = [(name, rows[name], sorted(evidence[name].values(), key=lambda h: (-h.score, h.chunk.start))[:excerpts])
              for name in ordered]
    return merged, dict(totals)


def bounded_document_candidates(rows, mandatory, limit):
    """控制模型批次数，同时给每个独立子题的最佳候选保留位置。"""
    if limit <= 0:
        return []
    available = {row[0] for row in rows}
    reserved = set(list(dict.fromkeys(name for name in mandatory if name in available))[:limit])
    selected = set(row[0] for row in rows[:limit]) | reserved
    for row in reversed(rows):
        if len(selected) <= limit:
            break
        if row[0] not in reserved:
            selected.discard(row[0])
    return [row for row in rows if row[0] in selected]

SYNONYMS = {
    "检索": "retrieval search", "综述": "survey review", "缓存": "cache caching",
    "延迟": "latency delay", "吞吐": "throughput", "调度": "scheduling",
    "遥感": "remote sensing", "绿地": "green space vegetation", "温度": "temperature",
    "准确率": "accuracy", "召回率": "recall", "公平性": "fairness",
    "表观遗传": "epigenetic feedback", "基因调控": "gene regulatory network",
    "平均场": "mean field theory", "马尔可夫": "Markovian",
    "启动子": "promoter", "停留时间": "dwell time",
    "费氏弧菌": "Vibrio fischeri", "群体感应": "quorum sensing",
    "弱独立": "weak independence", "信号层级": "signal hierarchy",
}


@dataclass
class QueryPlan:
    topic: str
    methods: str = ""
    focus: str = ""
    exclusions: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)
    subqueries: list[str] = field(default_factory=list)

    @classmethod
    def from_model(cls, value, fallback):
        if not isinstance(value, dict):
            return cls(fallback)
        def strings(key, limit):
            rows = value.get(key, [])
            if not isinstance(rows, list):
                return []
            return list(dict.fromkeys(x.strip() for x in rows if isinstance(x, str) and x.strip()))[:limit]
        return cls(str(value.get("topic") or fallback), str(value.get("methods") or ""),
                   str(value.get("focus") or ""), strings("exclusions", 8), strings("variants", 6),
                   strings("subqueries", MAX_SUBQUERIES))


class HybridRetriever:
    """在本地片段上结合词法匹配、字段覆盖和排除条件排序。"""
    def __init__(self, chunks):
        self.chunks = [c for c in chunks if c.kind != "references"]
        self.counts = [Counter(tokenize(c.text)) for c in self.chunks]
        self.df = Counter()
        self.postings = defaultdict(set)
        self.documents = defaultdict(list)
        for index, counts in enumerate(self.counts):
            self.documents[self.chunks[index].filename].append(index)
            self.df.update(counts.keys())
            for term in counts:
                self.postings[term].add(index)
        self.avg_len = sum(sum(c.values()) for c in self.counts) / max(1, len(self.counts))

    @tool(name="hybrid_retrieve")
    def search(self, query, *, top_k=60, filename=None):
        plan = query if isinstance(query, QueryPlan) else QueryPlan(query)
        expanded = plan.topic + " " + " ".join(plan.variants)
        for word, translated in SYNONYMS.items():
            if word in expanded:
                expanded += " " + translated
        topic = set(tokenize(expanded))
        methods = set(tokenize(plan.methods))
        focus = set(tokenize(plan.focus))
        terms = topic | methods | focus
        explicit_files = {name for name in self.documents if name.casefold() in plan.topic.casefold()}
        rows = []
        # 倒排表先缩小候选，避免每个子问题重复扫描所有片段。
        candidates = set().union(*(self.postings.get(term, set()) for term in terms))
        for name in explicit_files:
            candidates.update(self.documents[name])
        for index in sorted(candidates):
            if index % 256 == 0:
                checkpoint("retrieval")
            chunk, counts = self.chunks[index], self.counts[index]
            if filename and chunk.filename != filename:
                continue
            matched = terms & counts.keys()
            if not matched and chunk.filename not in explicit_files:
                continue
            length = sum(counts.values())
            bm25 = 0.
            for term in matched:
                tf = counts[term]
                idf = math.log(1 + (len(self.chunks)-self.df[term]+.5)/(self.df[term]+.5))
                bm25 += idf * tf * 2.2 / (tf + 1.2*(.25+.75*length/max(1,self.avg_len)))
            cover = lambda ts: len(ts & counts.keys()) / max(1, len(ts))
            title_terms = set(tokenize(chunk.heading + " " + (chunk.text[:600] if chunk.page == 1 else "")))
            title = len(topic & title_terms) / max(1, len(topic))
            lexical = bm25 / (bm25 + 3)
            quality = .9 if chunk.page else .5
            penalty = .25 * sum(phrase.lower() in chunk.text.lower() for phrase in plan.exclusions)
            # 按预设权重融合覆盖率与词法得分，排除词另行扣分。
            score = .35*cover(topic)+.25*(cover(methods) if methods else lexical)+.20*title+.10*(cover(focus) if focus else lexical)+.10*quality-penalty
            if chunk.filename in explicit_files:
                score += .9
            rows.append(RetrievalHit(chunk, score, tuple(sorted(matched))))
        return sorted(rows, key=lambda h: (-h.score, h.chunk.filename, h.chunk.start))[:top_k]

    def group_by_document(self, hits, limit=10):
        groups = defaultdict(list)
        for hit in hits:
            groups[hit.chunk.filename].append(hit)
        return sorted([(name, max(h.score for h in group), group) for name, group in groups.items()],
                      key=lambda x: (-x[1], x[0]))[:limit]

    def top_documents(self, plan, *, limit=18, excerpts=3):
        """先覆盖所有命中的论文，再逐篇截取证据，避免全局片段上限挤占论文。"""
        hits = self.search(plan, top_k=len(self.chunks))
        ranked = self.group_by_document(hits, limit=limit)
        return [(name, score, group[:excerpts]) for name, score, group in ranked]

    def per_document(self, plan, maximum=4):
        selected = []
        for name in sorted({c.filename for c in self.chunks}):
            document = [c for c in self.chunks if c.filename == name]
            hits = self.search(plan, top_k=len(document), filename=name)
            chosen, seen_text, seen_pages = [], set(), set()

            def add(chunk):
                if chunk.text in seen_text or len(chosen) >= maximum:
                    return
                chosen.append(chunk)
                seen_text.add(chunk.text)
                seen_pages.add(chunk.page)

            # 排除作者名单与孤立的“Abstract”标题，保留最多两个首页实质段落。
            first = [c for c in document if c.page == 1 and c.kind == "text"]
            substantive = [c for c in first if len(c.text.strip()) >= 300
                           and "@" not in c.text[:160] and c.text[:240].count(",") < 8]
            for chunk in sorted(substantive, key=lambda c: c.start)[:min(2, maximum)]:
                add(chunk)
            if not chosen and first:
                add(max(first, key=lambda c: len(c.text)))
            # 优先覆盖不同页，避免同一方法段落的重复切片占满证据位。
            for hit in hits:
                if len(hit.chunk.text.strip()) >= 80 and hit.chunk.page not in seen_pages:
                    add(hit.chunk)
            for hit in hits:
                if len(hit.chunk.text.strip()) >= 80:
                    add(hit.chunk)
            if not chosen:
                for chunk in document[:maximum]:
                    add(chunk)
            selected.extend(chosen)
        return selected

