import math
import re
from collections import defaultdict

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text):
    if text is None:
        text = ""
    if not isinstance(text, str):
        text = str(text)
    return _TOKEN_RE.findall(text.lower())


class _FieldStats(object):
    def __init__(self, docs, field):
        self.N = len(docs)
        self.doc_len = {}
        self.tf = {}
        total_len = 0
        postings = defaultdict(dict)
        for d in docs:
            did = d["id"]
            toks = _tokenize(d.get(field, ""))
            self.doc_len[did] = len(toks)
            total_len += len(toks)
            counts = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            self.tf[did] = counts
            for t, c in counts.items():
                postings[t][did] = c
        self.postings = postings
        self.df = {t: len(p) for t, p in postings.items()}
        self.avg_len = (float(total_len) / float(self.N)) if self.N > 0 else 0.0
        if not (self.avg_len > 0.0):
            self.avg_len = 1.0

    def idf(self, term):
        df = self.df.get(term, 0)
        return math.log(1.0 + (self.N - df + 0.5) / (df + 0.5))

    def score_doc(self, terms, did, k1, b):
        k1 = float(k1)
        b = float(b)
        length = float(self.doc_len.get(did, 0))
        norm = 1.0 - b + b * (length / self.avg_len)
        tf_doc = self.tf.get(did, {})
        total = 0.0
        for term in terms:
            f = float(tf_doc.get(term, 0))
            if f <= 0.0:
                continue
            denom = f + k1 * norm
            if denom <= 0.0:
                num = f * (k1 + 1.0)
                contrib = num if num == 0.0 else num / 1e-12
            else:
                contrib = f * (k1 + 1.0) / denom
            total += self.idf(term) * contrib
        return total


class _Index(object):
    def __init__(self, docs):
        self.docs = list(docs)
        self.doc_ids = [d["id"] for d in self.docs]
        self.text = _FieldStats(self.docs, "text")
        self.title = _FieldStats(self.docs, "title")

    def rank(self, query_text, config):
        terms = _tokenize(query_text)
        k1 = float(config.get("k1", 1.2))
        b = float(config.get("b", 0.75))
        tw = float(config.get("title_weight", 0.0))
        scored = []
        for did in self.doc_ids:
            ts = self.text.score_doc(terms, did, k1, b)
            if tw != 0.0:
                hs = self.title.score_doc(terms, did, k1, b)
                s = ts + tw * hs
            else:
                s = ts
            scored.append((did, s))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return [{"id": did, "score": s} for did, s in scored]


def _metrics(ranking, relevant, recall_k, mrr_k):
    rel = set(relevant)
    if not rel:
        recall = 0.0
        mrr = 0.0
        return recall, mrr
    top_r = ranking[:recall_k]
    hits = 0
    seen = set()
    for row in top_r:
        rid = row["id"]
        if rid in rel and rid not in seen:
            seen.add(rid)
            hits += 1
    recall = float(hits) / float(len(rel))
    mrr = 0.0
    top_m = ranking[:mrr_k]
    for i, row in enumerate(top_m):
        if row["id"] in rel:
            mrr = 1.0 / float(i + 1)
            break
    return recall, mrr


def _round12(x):
    return round(float(x) + 0.0, 12)


def _safe_mean(values):
    if not values:
        return 0.0
    return float(sum(values)) / float(len(values))


def _config_key(config):
    return (
        float(config.get("title_weight", 0.0)),
        float(config.get("k1", 1.2)),
        float(config.get("b", 0.75)),
    )


def _config_dict(config):
    return {
        "k1": float(config.get("k1", 1.2)),
        "b": float(config.get("b", 0.75)),
        "title_weight": float(config.get("title_weight", 0.0)),
    }


def _normalize_grid(protocol):
    grid = None
    if isinstance(protocol, dict):
        grid = protocol.get("grid")
    if not isinstance(grid, list) or len(grid) == 0:
        grid = []
        for tw in (0.0, 0.5, 2.0):
            for k1 in (0.4, 1.2, 2.0):
                for b in (0.0, 0.5, 1.0):
                    grid.append({"b": b, "k1": k1, "title_weight": tw})
    seen = set()
    out = []
    for cfg in grid:
        if not isinstance(cfg, dict):
            continue
        cd = _config_dict(cfg)
        key = (cd["title_weight"], cd["k1"], cd["b"])
        if key in seen:
            continue
        seen.add(key)
        out.append(cd)
    out.sort(key=_config_key)
    return out


def _evaluate_config(index, dev_queries, config):
    rankings = {}
    per_group = {}
    recalls = []
    mrrs = []
    for q in dev_queries:
        qid = q["id"]
        ranking = index.rank(q.get("text", ""), config)
        rankings[qid] = ranking
        relevant = q.get("relevant", [])
        if relevant is None:
            relevant = []
        rec, mrr = _metrics(ranking, relevant, 5, 10)
        recalls.append(rec)
        mrrs.append(mrr)
        group = q.get("group", "")
        if group is None:
            group = ""
        g = per_group.setdefault(str(group), {"recalls": [], "mrrs": []})
        g["recalls"].append(rec)
        g["mrrs"].append(mrr)
    groups = {}
    for gname in per_group:
        g = per_group[gname]
        groups[gname] = {
            "recall_at_5_percent": _safe_mean(g["recalls"]) * 100.0,
            "mrr_at_10": _safe_mean(g["mrrs"]),
        }
    g_mrr_vals = [groups[g]["mrr_at_10"] for g in groups]
    g_rec_vals = [groups[g]["recall_at_5_percent"] for g in groups]
    worst_mrr = min(g_mrr_vals) if g_mrr_vals else 0.0
    macro_mrr = _safe_mean(g_mrr_vals)
    macro_rec = _safe_mean(g_rec_vals)
    group_metrics = {
        "groups": groups,
        "worst_mrr_at_10": worst_mrr,
        "macro_mrr_at_10": macro_mrr,
        "macro_recall_at_5_percent": macro_rec,
    }
    return {
        "config": _config_dict(config),
        "rankings": rankings,
        "recall_at_5_percent": _safe_mean(recalls) * 100.0,
        "mrr_at_10": _safe_mean(mrrs),
        "group_metrics": group_metrics,
    }


def _select(search_rows):
    best = None
    best_key = None
    for row in search_rows:
        gm = row["group_metrics"]
        key = (
            _round12(gm["worst_mrr_at_10"]),
            _round12(gm["macro_mrr_at_10"]),
            _round12(gm["macro_recall_at_5_percent"]),
        )
        cfg = row["config"]
        tie = _config_key(cfg)
        full = (-key[0], -key[1], -key[2]) + tie
        if best_key is None or full < best_key:
            best_key = full
            best = row
    if best is None:
        return {"k1": 1.2, "b": 0.75, "title_weight": 0.0}
    return dict(best["config"])


def _test_rankings(index, test_queries, config):
    out = {}
    for q in test_queries:
        qid = q["id"]
        out[qid] = index.rank(q.get("text", ""), config)
    return out


def run_experiment(request):
    if not isinstance(request, dict):
        request = {}
    phase = request.get("phase", "run")
    data = request.get("data", {}) or {}
    protocol = request.get("protocol", {}) or {}

    documents = data.get("documents", []) or []
    dev_queries = data.get("dev_queries", []) or []
    test_queries = data.get("test_queries", []) or []

    index = _Index(documents)

    if phase == "rank":
        selected = data.get("selected")
        if not isinstance(selected, dict):
            default_cfg = protocol.get("default_config", {}) if isinstance(protocol, dict) else {}
            selected = default_cfg
        cfg = _config_dict(selected)
        return {"candidate": _test_rankings(index, test_queries, cfg)}

    default_cfg = protocol.get("default_config", {}) if isinstance(protocol, dict) else {}
    if not isinstance(default_cfg, dict):
        default_cfg = {"k1": 1.2, "b": 0.75, "title_weight": 0.0}
    baseline_cfg = _config_dict(default_cfg)

    baseline = _test_rankings(index, test_queries, baseline_cfg)

    if len(dev_queries) == 0:
        return {
            "baseline": baseline,
            "candidate": None,
            "details": {},
        }

    grid = _normalize_grid(protocol)

    search_rows = []
    for cfg in grid:
        search_rows.append(_evaluate_config(index, dev_queries, cfg))

    selected = _select(search_rows)

    candidate = _test_rankings(index, test_queries, selected)

    return {
        "baseline": baseline,
        "candidate": candidate,
        "details": {
            "selected": _config_dict(selected),
            "search": search_rows,
        },
    }
