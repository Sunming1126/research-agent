from __future__ import annotations

import math
import re
from collections import Counter, defaultdict


TOKEN_RE = re.compile(r"[a-z0-9]+")
GRID = [{"k1": k1, "b": b, "title_weight": weight}
        for k1 in (0.4, 1.2, 2.0)
        for b in (0.0, 0.5, 1.0)
        for weight in (0.0, 0.5, 2.0)]


def _tokens(value: str) -> list[str]:
    return TOKEN_RE.findall(str(value).lower())


def _field_stats(documents, field):
    rows = [_tokens(doc.get(field, "")) for doc in documents]
    df = Counter()
    for row in rows:
        df.update(set(row))
    average = sum(len(row) for row in rows) / max(1, len(rows))
    return rows, df, average or 1.0


def _field_score(query, tokens, df, average, n_docs, config):
    counts = Counter(tokens)
    score = 0.0
    for term in query:
        frequency = counts.get(term, 0)
        if not frequency:
            continue
        term_df = df.get(term, 0)
        inverse = math.log(1.0 + (n_docs - term_df + 0.5) / (term_df + 0.5))
        denominator = frequency + config["k1"] * (1.0 - config["b"] + config["b"] * len(tokens) / average)
        score += inverse * frequency * (config["k1"] + 1.0) / denominator
    return score


def _rank(documents, query_text, config, stats):
    query = _tokens(query_text)
    body_rows, body_df, body_average, title_rows, title_df, title_average = stats
    rows = []
    for pos, doc in enumerate(documents):
        score = _field_score(query, body_rows[pos], body_df, body_average, len(documents), config)
        score += config["title_weight"] * _field_score(query, title_rows[pos], title_df, title_average, len(documents), config)
        rows.append({"id": doc["id"], "score": score})
    return sorted(rows, key=lambda row: (-row["score"], row["id"]))


def _rankings(documents, queries, config, stats):
    return {query["id"]: _rank(documents, query.get("text", ""), config, stats) for query in queries}


def _metrics(rankings, queries):
    values = []
    groups = defaultdict(list)
    for query in queries:
        ranking = rankings[query["id"]]
        relevant = set(query.get("relevant", []))
        top5 = [row["id"] for row in ranking[:5]]
        recall = 100.0 * len(set(top5) & relevant) / max(1, len(relevant))
        reciprocal = 0.0
        for rank, row in enumerate(ranking[:10], start=1):
            if row["id"] in relevant:
                reciprocal = 1.0 / rank
                break
        values.append((recall, reciprocal))
        groups[query.get("group", "default")].append((recall, reciprocal))
    group_values = {
        group: {
            "recall_at_5_percent": sum(item[0] for item in items) / len(items),
            "mrr_at_10": sum(item[1] for item in items) / len(items),
        }
        for group, items in groups.items()
    }
    return {
        "recall_at_5_percent": sum(item[0] for item in values) / max(1, len(values)),
        "mrr_at_10": sum(item[1] for item in values) / max(1, len(values)),
        "group_metrics": {
            "groups": group_values,
            "worst_mrr_at_10": min((item["mrr_at_10"] for item in group_values.values()), default=0.0),
            "macro_mrr_at_10": sum(item["mrr_at_10"] for item in group_values.values()) / max(1, len(group_values)),
            "macro_recall_at_5_percent": sum(item["recall_at_5_percent"] for item in group_values.values()) / max(1, len(group_values)),
        },
    }


def _config_key(config):
    return (config["title_weight"], config["k1"], config["b"])


def run_d02(request: dict) -> dict:
    """按 D-02 protocol 完成 run 调参或 rank 固定配置。"""
    data = request.get("data") or {}
    documents = list(data.get("documents") or [])
    if not documents:
        raise ValueError("D-02 requires documents")
    stats = (*_field_stats(documents, "text"), *_field_stats(documents, "title"))
    phase = request.get("phase", "run")
    test_queries = list(data.get("test_queries") or [])
    if phase == "rank":
        selected = dict(data.get("selected") or {})
        if set(selected) != {"k1", "b", "title_weight"}:
            raise ValueError("D-02 rank requires selected configuration")
        return {"candidate": _rankings(documents, test_queries, selected, stats)}
    if phase != "run":
        raise ValueError("unsupported D-02 phase")
    default = {"k1": 1.2, "b": 0.75, "title_weight": 0.0}
    baseline = _rankings(documents, test_queries, default, stats)
    search = []
    dev_queries = list(data.get("dev_queries") or [])
    for config in GRID:
        rankings = _rankings(documents, dev_queries, config, stats)
        metrics = _metrics(rankings, dev_queries)
        search.append({"config": dict(config), "rankings": rankings,
                       "recall_at_5_percent": metrics["recall_at_5_percent"],
                       "mrr_at_10": metrics["mrr_at_10"],
                       "group_metrics": metrics["group_metrics"]})
    selected_row = max(search, key=lambda row: (
        round(row["group_metrics"]["worst_mrr_at_10"], 12),
        round(row["group_metrics"]["macro_mrr_at_10"], 12),
        round(row["group_metrics"]["macro_recall_at_5_percent"], 12),
        tuple(-value for value in _config_key(row["config"])),
    ))
    selected = dict(selected_row["config"])
    return {"baseline": baseline, "candidate": _rankings(documents, test_queries, selected, stats),
            "details": {"selected": selected, "search": search}}

