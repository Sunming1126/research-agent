"""核验公开开发排名对应的统计量，不产生测试集指标或正式答卷。"""
from __future__ import annotations

import math
import re
from collections import defaultdict, Counter


def validate_bm25_rankings(rankings, queries, documents, config):
    """从公开字段核对所报分数；接受保持排名的正比例缩放，不生成预测。"""
    tokenize = lambda text: re.findall(r"[a-z0-9]+", text.lower())
    fields = []
    for field, weight in (("text", 1), ("title", config["title_weight"])):
        counts = {doc["id"]: Counter(tokenize(doc.get(field, ""))) for doc in documents}
        df = Counter(term for row in counts.values() for term in row)
        lengths = {key: sum(row.values()) for key, row in counts.items()}
        average = sum(lengths.values()) / max(1, len(documents)) or 1
        fields.append((weight, counts, df, lengths, average))
    for query in queries:
        scale = None
        for row in rankings[query["id"]]:
            expected = 0.0
            for weight, counts, df, lengths, average in fields:
                for term in tokenize(query["text"]):
                    frequency = counts[row["id"]][term]
                    if frequency and weight:
                        expected += weight * math.log(1 + (len(documents) - df[term] + .5) / (df[term] + .5)) * (
                            frequency * (config["k1"] + 1) / (frequency + config["k1"] * (
                                1 - config["b"] + config["b"] * lengths[row["id"]] / average)))
            if expected > 0 and scale is None:
                scale = row["score"] / expected
                if scale <= 0:
                    raise ValueError("D-02 matching query terms require positive BM25 scores")
            if not math.isclose(row["score"], expected * (scale or 1), rel_tol=1e-8, abs_tol=1e-10):
                raise ValueError(f"D-02 BM25 score disagrees with query terms for query={query['id']}, doc={row['id']}; "
                                 "sum over QUERY terms, use term frequency from DOCUMENT, count repeated query terms")


def validate_search_metrics(row, queries, document_ids):
    rankings = row.get("rankings")
    if isinstance(rankings, list):
        if rankings and isinstance(rankings[0], dict) and "query_id" in rankings[0]:
            rankings = {item["query_id"]: item["rankings"] for item in rankings}
        elif len(rankings) == len(queries):
            rankings = {q["id"]: items for q, items in zip(queries, rankings)}
    if not isinstance(rankings, dict) or set(rankings) != {q["id"] for q in queries}:
        raise ValueError("D-02 search rankings must cover every development query")
    groups, values = defaultdict(list), []
    for query in queries:
        ranking = rankings[query["id"]]
        normalized = [item if isinstance(item, dict) else dict(zip(("id", "score"), item)) for item in ranking]
        ids = [item["id"] for item in normalized]
        if len(ids) != len(document_ids) or set(ids) != document_ids:
            raise ValueError("D-02 development ranking must contain every document once")
        validate_ranking_order(normalized)
        relevant = set(query.get("relevant", []))
        recall = 100 * len(set(ids[:5]) & relevant) / max(1, len(relevant))
        mrr = next((1 / (i + 1) for i, key in enumerate(ids[:10]) if key in relevant), 0)
        values.append((recall, mrr))
        groups[query.get("group", "default")].append((recall, mrr))

    def mean(items, col):
        return sum(item[col] for item in items) / max(1, len(items))

    def check(actual, expected, label):
        if type(actual) not in {int, float} or not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-10):
            raise ValueError(f"D-02 {label} disagrees with public development rankings: expected {expected}, got {actual}")

    check(row.get("recall_at_5_percent"), mean(values, 0), "ordinary recall")
    check(row.get("mrr_at_10"), mean(values, 1), "ordinary MRR")
    reported = row.get("group_metrics", {})
    reported_groups = reported.get("groups", {})
    if set(reported_groups) != set(groups):
        raise ValueError("D-02 group metrics must include every development group")
    group_values = []
    for name, items in groups.items():
        recall, mrr = mean(items, 0), mean(items, 1)
        check(reported_groups[name].get("recall_at_5_percent"), recall, "group recall")
        check(reported_groups[name].get("mrr_at_10"), mrr, "group MRR")
        group_values.append((recall, mrr))
    check(reported.get("worst_mrr_at_10"), min((x[1] for x in group_values), default=0), "worst group MRR")
    check(reported.get("macro_mrr_at_10"), mean(group_values, 1), "macro MRR")
    check(reported.get("macro_recall_at_5_percent"), mean(group_values, 0), "macro recall")


def validate_ranking_order(ranking):
    for row in ranking:
        if type(row.get("score")) not in {int, float} or not math.isfinite(row["score"]):
            raise ValueError("D-02 ranking scores must be finite numbers")
    for left, right in zip(ranking, ranking[1:]):
        a, b = left["score"], right["score"]
        # 官方允许1e-12相对尺度的数值并列，不能因浮点加和顺序拒绝等价实现。
        tolerance = 1e-12 * max(1, abs(a), abs(b))
        if b - a > tolerance or (a == b and left["id"] > right["id"]):
            raise ValueError("D-02 rankings must use descending scores and lexical ties")
