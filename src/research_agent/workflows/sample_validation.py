"""公开输入自测：只判断运行和公开可验证约束，不生成官方指标。"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import re
import time
from copy import deepcopy
from collections import Counter, defaultdict
from pathlib import Path

from ..core.session import checkpoint, current_session, remaining_timeout
from .public_probes import public_probes


class PublicSampleError(ValueError):
    """可直接交回模型的公开反例；不包含凭据或平台隐藏数据。"""
    def __init__(self, case_id, probe, request, error):
        super().__init__(f"{case_id}/{probe}: {error}")
        self.feedback = {"case_id": case_id, "probe": probe,
                         "request": request, "error": str(error)[:1800]}


def _finite(value):
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, list):
        return all(_finite(v) for v in value)
    return not isinstance(value, float) or math.isfinite(value)


def _d02_rankings(raw, queries, documents):
    """规范允许多种排名表示；只核对公开输入，不重建检索算法。"""
    if isinstance(raw, list):
        if raw and isinstance(raw[0], dict) and "query_id" in raw[0]:
            if len({row["query_id"] for row in raw}) != len(raw):
                raise ValueError("D-02 duplicate query ranking")
            raw = {row["query_id"]: row["rankings"] for row in raw}
        elif len(raw) == len(queries):
            raw = {query["id"]: rows for query, rows in zip(queries, raw)}
    if not isinstance(raw, dict) or not {q["id"] for q in queries} <= set(raw):
        raise ValueError("D-02 rankings must cover every supplied query")
    normalized = {}
    for query in queries:
        rows = raw[query["id"]]
        if not isinstance(rows, list):
            raise ValueError("D-02 query ranking must be a list")
        pairs = []
        for row in rows:
            if isinstance(row, dict) and set(row) == {"id", "score"}:
                identifier, score = row["id"], row["score"]
            elif isinstance(row, list) and len(row) == 2:
                identifier, score = row
            else:
                raise ValueError("D-02 ranking items require id and score")
            if type(score) not in {int, float} or not math.isfinite(score):
                raise ValueError("D-02 ranking score must be finite numeric")
            pairs.append((identifier, score))
        if len(pairs) != len(documents) or {row[0] for row in pairs} != documents:
            raise ValueError("D-02 ranking must contain every document exactly once")
        if pairs != sorted(pairs, key=lambda row: (-row[1], row[0])):
            raise ValueError("D-02 rankings must use descending scores and lexical ties")
        normalized[query["id"]] = [row[0] for row in pairs]
    return normalized


def _d02_dev_metrics(row, data):
    """从公开开发排名与公开相关标注核算，防止虚报或混淆两种均值。"""
    queries = data["dev_queries"]
    rankings = _d02_rankings(row.get("rankings"), queries, {d["id"] for d in data["documents"]})
    values, groups = [], defaultdict(list)
    for query in queries:
        ids, relevant = rankings[query["id"]], set(query.get("relevant", []))
        recall = 100 * len(set(ids[:5]) & relevant) / len(relevant) if relevant else 0
        mrr = next((1 / (i + 1) for i, identifier in enumerate(ids[:10]) if identifier in relevant), 0)
        values.append((recall, mrr)); groups[query.get("group", "default")].append((recall, mrr))
    def means(items):
        return tuple(sum(item[i] for item in items) / len(items) for i in (0, 1)) if items else (0, 0)
    def check(actual, expected, name):
        if type(actual) not in {int, float} or abs(actual - expected) > 1e-8 * max(1, abs(expected)):
            raise ValueError(f"D-02 {name} inconsistent with public dev rankings: expected {expected}, got {actual}")
    recall, mrr = means(values)
    check(row.get("recall_at_5_percent"), recall, "ordinary dev Recall")
    check(row.get("mrr_at_10"), mrr, "ordinary dev MRR")
    metrics = row.get("group_metrics", {})
    reported = metrics.get("groups")
    if not isinstance(reported, dict) or set(reported) != set(groups):
        raise ValueError("D-02 group metrics must cover exactly the public dev groups")
    group_means = []
    for group, items in groups.items():
        if not isinstance(reported[group], dict):
            raise ValueError("D-02 each group metric must be an object")
        gr, gm = means(items); group_means.append((gr, gm))
        check(reported[group].get("recall_at_5_percent"), gr, "group Recall")
        check(reported[group].get("mrr_at_10"), gm, "group MRR")
    macro_recall, macro_mrr = means(group_means)
    for key, expected in [("worst_mrr_at_10", min((v[1] for v in group_means), default=0)),
                          ("macro_mrr_at_10", macro_mrr), ("macro_recall_at_5_percent", macro_recall)]:
        check(metrics.get(key), expected, key)


def validate_sample_output(case_id, request, result):
    if not isinstance(result, dict) or not _finite(result):
        raise ValueError("algorithm output must be a finite JSON object")
    if set(result) != {"baseline", "candidate", "details"} or not isinstance(result["details"], dict):
        raise ValueError("algorithm output requires exactly baseline/candidate/details")
    data = request["data"]
    for name in ("baseline", "candidate"):
        value = result[name]
        if name == "candidate" and value is None:
            continue
        if case_id == "D-01":
            labels = {row["label"] for row in data["train"]}
            if not isinstance(value, list) or len(value) != len(data["texts"]) or any(v not in labels for v in value):
                raise ValueError("D-01 predictions must align with texts and known classes")
            if name == "candidate":
                details = result["details"]
                if not {"classes", "vocabulary", "idf", "weights"} <= set(details):
                    raise ValueError("D-01 candidate requires actual model fields directly in details")
                classes, vocabulary, weights = details["classes"], details["vocabulary"], details["weights"]
                if not isinstance(classes, list) or set(classes) != labels or len(classes) != len(labels):
                    raise ValueError("D-01 model classes differ from training labels")
                if not isinstance(vocabulary, list) or not isinstance(weights, list) or len(weights) != len(classes):
                    raise ValueError("D-01 model matrix dimensions are invalid")
                if any(not isinstance(row, list) or len(row) != len(vocabulary) + 1 for row in weights):
                    raise ValueError("D-01 model weights must include bias as the last column")
                if any(type(weight) not in {int, float} for row in weights for weight in row):
                    raise ValueError("D-01 weights must be finite numeric values")
                if classes != sorted(classes) or vocabulary != sorted(set(vocabulary)):
                    raise ValueError("D-01 class and vocabulary ordering must be lexical")
                raw_idf = details["idf"]
                idf = [raw_idf[token] for token in vocabulary] if isinstance(raw_idf, dict) else raw_idf
                if not isinstance(idf, list) or len(idf) != len(vocabulary) or any(type(v) not in {int, float} for v in idf):
                    raise ValueError("D-01 idf must align with the vocabulary")
                # 只验证模型与预测一致，不在 Agent 中重建或训练参考算法。
                for text, prediction in zip(data["texts"], value):
                    counts = Counter(re.findall(r"[a-z0-9]+", text.lower()))
                    features = [counts[token] * weight for token, weight in zip(vocabulary, idf)]
                    norm = math.sqrt(sum(v*v for v in features))
                    features = [v / norm if norm else 0 for v in features] + [1]
                    logits = [sum(w * x for w, x in zip(row, features)) for row in weights]
                    predicted = classes[max(range(len(classes)), key=lambda i: logits[i])]
                    if prediction != predicted:
                        raise ValueError("D-01 candidate predictions do not match the actual returned model")
            elif labels:
                counts = Counter(row["label"] for row in data["train"])
                majority = min(labels, key=lambda label: (-counts[label], label))
                if value != [majority] * len(data["texts"]):
                    raise ValueError("D-01 majority baseline violates lexical tie handling")
        elif case_id == "D-02":
            documents = {d["id"] for d in data["documents"]}
            _d02_rankings(value, data["test_queries"], documents)
            if name == "candidate":
                details = result["details"]
                if not isinstance(details.get("selected"), dict) or not isinstance(details.get("search"), list):
                    raise ValueError("D-02 candidate requires selected parameters and search records")
                if not {"k1", "b", "title_weight"} <= set(details["selected"]):
                    raise ValueError("D-02 selected parameters are incomplete")
                search = details["search"]
                expected_grid = request["protocol"].get("grid", [])
                configs = [row.get("config") for row in search]
                canonical = lambda rows: sorted(json.dumps(row, sort_keys=True) for row in rows)
                if expected_grid and canonical(configs) != canonical(expected_grid):
                    raise ValueError("D-02 search must cover each public grid configuration exactly once")
                for row in search:
                    _d02_dev_metrics(row, data)
                    metrics = row.get("group_metrics", {})
                    if not {"groups", "worst_mrr_at_10", "macro_mrr_at_10", "macro_recall_at_5_percent"} <= set(metrics):
                        raise ValueError("D-02 search group metrics are incomplete")
                def selection_key(row):
                    metrics, config = row["group_metrics"], row["config"]
                    return tuple(-round(metrics[k], 12) for k in (
                        "worst_mrr_at_10", "macro_mrr_at_10", "macro_recall_at_5_percent")) + tuple(
                        config[k] for k in ("title_weight", "k1", "b"))
                if search and min(search, key=selection_key)["config"] != details["selected"]:
                    raise ValueError("D-02 selected config does not follow the public group-robust selection rule")
        elif case_id == "D-05":
            if not isinstance(value, dict) or set(value) != {"trace", "served_ms", "dropped"}:
                raise ValueError("D-05 run requires trace/served_ms/dropped")
            if not isinstance(value["trace"], list) or not isinstance(value["dropped"], list):
                raise ValueError("D-05 trace and dropped must be lists")
            jobs = {j["id"]: j for j in data["jobs"]}
            ids = [j.get("id") for j in value["trace"] + value["dropped"]]
            if len(ids) != len(jobs) or set(ids) != set(jobs):
                raise ValueError("D-05 every job must occur exactly once in trace or dropped")
            flows = data["flow_weights"]
            if not isinstance(value["served_ms"], dict) or set(value["served_ms"]) != set(flows):
                raise ValueError("D-05 served_ms must include every declared flow")
            if value["trace"] != sorted(value["trace"], key=lambda row: (row["dispatch_ms"], row["server"])):
                raise ValueError("D-05 trace must be ordered by dispatch time and server")
            if value["dropped"] != sorted(value["dropped"], key=lambda row: row["id"]):
                raise ValueError("D-05 dropped jobs must be ordered by id")
            if any(set(row) != {"id", "reason"} or row["reason"] not in {"deadline", "horizon"}
                   for row in value["dropped"]):
                raise ValueError("D-05 drop reasons must be deadline or horizon")
            served, last_end, last_flow = defaultdict(float), {}, {}
            for job in value["trace"]:
                if not {"server", "dispatch_ms", "setup_ms", "start_ms", "end_ms", "wait_ms"} <= set(job):
                    raise ValueError("D-05 trace timing fields missing")
                original = jobs[job["id"]]
                if any(job.get(key) != val for key, val in original.items()):
                    raise ValueError("D-05 trace must preserve original job fields")
                if job["start_ms"] < original["arrival_ms"] or job["end_ms"] < job["start_ms"]:
                    raise ValueError("D-05 trace violates public timing order")
                server = job["server"]
                if type(server) is not int or not 0 <= server < data["servers"]:
                    raise ValueError("D-05 server index is invalid")
                if job["dispatch_ms"] < max(last_end.get(server, 0), original["arrival_ms"]):
                    raise ValueError("D-05 overlapping jobs on the same server")
                expected_setup = data["setup_ms"] if server in last_flow and last_flow[server] != original["flow"] else 0
                if job["setup_ms"] != expected_setup:
                    raise ValueError(f"D-05 {name} job={job['id']} server={server}: "
                                     f"previous_flow={last_flow.get(server)!r}, next_flow={original['flow']!r}, "
                                     f"expected_setup_ms={expected_setup}, observed_setup_ms={job['setup_ms']}; "
                                     "the first assignment has zero setup; retain previous flow across idle intervals")
                if (abs(job["end_ms"] - job["start_ms"] - original["service_ms"]) > 1e-8
                        or abs(job["wait_ms"] - (job["start_ms"] - original["arrival_ms"])) > 1e-8
                        or abs(job["start_ms"] - job["dispatch_ms"] - job["setup_ms"]) > 1e-8
                        or job["start_ms"] >= original["deadline_ms"] or job["end_ms"] > data["horizon_ms"]):
                    raise ValueError("D-05 service/wait/setup/deadline/horizon invariants do not hold")
                served[original["flow"]] += original["service_ms"]
                last_end[server] = job["end_ms"]
                last_flow[server] = original["flow"]
            if any(abs(value["served_ms"][flow] - served[flow]) > 1e-8 for flow in flows):
                raise ValueError("D-05 served_ms differs from completed trace service")
    return {"status": "passed", "scope": "public_sample_structure_and_invariants",
            "candidate_present": result["candidate"] is not None}


def _run_sample(source, request, timeout):
    message = {"source": source.read_text(encoding="utf-8"), "request": request, "seconds": max(1, int(timeout))}
    # 子进程不继承模型凭据、平台会话或用户 PYTHONPATH；-I/-S 禁止加载用户包。
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH") if key in os.environ}
    command = [sys.executable, "-I", "-S", "-X", "utf8", str(Path(__file__).with_name("sample_worker.py"))]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", env=env, cwd=source.parent)
    deadline = time.monotonic() + timeout
    payload = json.dumps(message, ensure_ascii=False)
    try:
        while True:
            checkpoint()
            try:
                stdout, stderr = process.communicate(payload, timeout=min(.2, max(.01, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                payload = None
                if time.monotonic() >= deadline:
                    raise RuntimeError("public sample timed out")
    except BaseException:
        process.kill()
        process.communicate()
        raise
    if process.returncode:
        raise ValueError("public sample execution failed: " + (stdout.strip() or stderr.strip())[:1800])
    return json.loads(stdout)


def run_public_sample(source: Path, materials, case_id, *, timeout=8):
    """整个公开自测共用额度；反例与源码交回模型，绝不就地改算法。"""
    protocol, request = json.loads(materials["protocol"]), json.loads(materials["sample"])
    if request.get("case_id") != case_id or protocol.get("case_id") != case_id:
        raise ValueError("public sample or protocol belongs to another case")
    request = dict(request, protocol=protocol)
    checkpoint("d_public_sample")
    deadline = time.monotonic() + remaining_timeout(timeout, reserve=35)
    reports = []
    for name, probe, expected_completed in public_probes(case_id, request):
        budget = min(deadline - time.monotonic(), 2 if case_id == "D-05" else timeout)
        if budget <= 0:
            raise PublicSampleError(case_id, name, probe, "public test suite exhausted its shared time budget")
        try:
            body = _run_sample(source, probe, remaining_timeout(budget, reserve=35))
            report = validate_sample_output(case_id, probe, body["result"])
            if case_id == "D-02" and isinstance(expected_completed, dict):
                for strategy in ("baseline", "candidate"):
                    value = body["result"][strategy]
                    if value is None:
                        continue
                    rankings = _d02_rankings(value, probe["data"]["test_queries"],
                                             {d["id"] for d in probe["data"]["documents"]})
                    for query, expected in expected_completed["d02_expected_top"].items():
                        if rankings[query][0] != expected:
                            raise ValueError(f"D-02 {strategy} query={query}: expected first document {expected}, "
                                             f"observed {rankings[query][0]}; use query tokens and document term frequency, "
                                             "not document tokens or document length as frequency")
            elif isinstance(expected_completed, dict):
                for strategy in ("baseline", "candidate"):
                    result = body["result"][strategy]
                    if result is not None:
                        states = {row["id"]: "completed" for row in result["trace"]}
                        states.update({row["id"]: row["reason"] for row in result["dropped"]})
                        if states != expected_completed["states"]:
                            raise ValueError(f"public protocol probe {name} has wrong completed/drop states; "
                                             "check deadline priority and account for all jobs at the horizon")
                        expected_ids = expected_completed.get("trace_ids", {}).get(strategy)
                        if expected_ids is not None and [row["id"] for row in result["trace"]] != expected_ids:
                            raise ValueError("public protocol probe must count assigned service immediately "
                                             "before choosing the next simultaneously idle server")
            elif expected_completed is not None:
                for strategy in ("baseline", "candidate"):
                    result = body["result"][strategy]
                    if result is not None and sorted(row["id"] for row in result["trace"]) != sorted(expected_completed):
                        raise ValueError("ample-window probe must complete every supplied job")
            if case_id == "D-02" and body["result"]["candidate"] is not None:
                repeated = body.get("rank_result")
                if not isinstance(repeated, dict) or set(repeated) != {"candidate"} or repeated["candidate"] != body["result"]["candidate"]:
                    raise ValueError("D-02 rank must reuse the selected configuration and reproduce run rankings")
                report["rank_consistent"] = True
            if case_id == "D-01":
                first, repeated = deepcopy(body["result"]), deepcopy(body.get("repeat_result"))
                # 真实计时不是算法预测漂移；仅剥离已知 details 诊断，不忽略预测或模型字段。
                volatile = []
                for key in ("elapsed_seconds",):
                    if key in first["details"] or (isinstance(repeated, dict) and key in repeated.get("details", {})):
                        volatile.append(key)
                        first["details"].pop(key, None)
                        if isinstance(repeated, dict):
                            repeated["details"].pop(key, None)
                if first != repeated:
                    raise ValueError("D-01 repeated predictions or model fields are not deterministic")
                report.update(reproducible=True, volatile_detail_fields=volatile)
            report.update(probe=name, isolation=body["isolation"])
            reports.append(report)
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            raise PublicSampleError(case_id, name, probe, str(exc)) from None
    return {**reports[0], "probes": reports, "probe_count": len(reports),
            "scope": "public_samples_and_protocol_boundary_invariants"}
