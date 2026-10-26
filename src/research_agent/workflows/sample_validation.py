"""公开输入自测：只判断运行和公开可验证约束，不生成官方指标。"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..core.session import checkpoint, current_session, remaining_timeout


def _finite(value):
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, list):
        return all(_finite(v) for v in value)
    return not isinstance(value, float) or math.isfinite(value)


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
                    raise ValueError('D-01 majority baseline violates lexical tie handling: public train counts='
                                     + json.dumps(dict(counts), sort_keys=True) + '; expected constant label='
                                     + repr(majority) + '; actual public predictions=' + repr(value[:6]))
        elif case_id == "D-02":
            queries = {q["id"] for q in data["test_queries"]}
            documents = {d["id"] for d in data["documents"]}
            if not isinstance(value, dict) or set(value) != queries:
                raise ValueError("D-02 rankings must cover every test query")
            for ranking in value.values():
                if (not isinstance(ranking, list) or any(not isinstance(row, dict) or set(row) != {"id", "score"}
                    or type(row["score"]) not in {int, float} for row in ranking)):
                    raise ValueError("D-02 ranking items require id and numeric score")
                if len(ranking) != len(documents) or {row["id"] for row in ranking} != documents:
                    raise ValueError("D-02 ranking must contain every document exactly once")
                if ranking != sorted(ranking, key=lambda row: (-row["score"], row["id"])):
                    raise ValueError("D-02 rankings must use descending scores and lexical ties")
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
            served, last_end = defaultdict(float), {}
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
                if job["dispatch_ms"] < last_end.get(server, 0):
                    raise ValueError("D-05 overlapping jobs on the same server")
                if (abs(job["end_ms"] - job["start_ms"] - original["service_ms"]) > 1e-8
                        or abs(job["wait_ms"] - (job["start_ms"] - original["arrival_ms"])) > 1e-8
                        or abs(job["start_ms"] - job["dispatch_ms"] - job["setup_ms"]) > 1e-8
                        or job["start_ms"] >= original["deadline_ms"] or job["end_ms"] > data["horizon_ms"]):
                    raise ValueError("D-05 service/wait/setup/deadline/horizon invariants do not hold")
                served[original["flow"]] += original["service_ms"]
                last_end[server] = job["end_ms"]
            if any(abs(value["served_ms"][flow] - served[flow]) > 1e-8 for flow in flows):
                raise ValueError("D-05 served_ms differs from completed trace service")
    return {"status": "passed", "scope": "public_sample_structure_and_invariants",
            "candidate_present": result["candidate"] is not None}


def run_public_sample(source: Path, materials, case_id, *, timeout=8, _edge_check=True):
    protocol, request = json.loads(materials["protocol"]), json.loads(materials["sample"])
    if request.get("case_id") != case_id or protocol.get("case_id") != case_id:
        raise ValueError("public sample or protocol belongs to another case")
    request = dict(request, protocol=protocol)
    checkpoint("d_public_sample")
    timeout = remaining_timeout(timeout, reserve=35)
    message = {"source": source.read_text(encoding="utf-8"), "request": request, "seconds": max(1, int(timeout))}
    # 子进程不继承模型凭据、平台会话或用户 PYTHONPATH；-I/-S 禁止加载用户包。
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH") if key in os.environ}
    command = [sys.executable, "-I", "-S", "-X", "utf8", str(Path(__file__).with_name("sample_worker.py"))]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", env=env, cwd=source.parent)
    import time
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
    body = json.loads(stdout)
    report = validate_sample_output(case_id, request, body["result"])
    if case_id == "D-02" and body["result"]["candidate"] is not None:
        repeated = body.get("rank_result")
        identity = lambda rankings: {qid: [row["id"] for row in ranking] for qid, ranking in rankings.items()}
        if (not isinstance(repeated, dict) or set(repeated) != {"candidate"} or not _finite(repeated)
                or identity(repeated["candidate"]) != identity(body["result"]["candidate"])):
            raise ValueError("D-02 rank must reuse the selected configuration and reproduce run rankings")
        report["rank_consistent"] = True
    report["isolation"] = body["isolation"]
    if case_id == 'D-05' and _edge_check:
        # 由当前公开样例派生，不使用隐藏实例或参考答案。首个样例不含
        # 同时到达时，heap(time, kind, dict)的比较错误过去会漏过自检。
        import copy
        simultaneous = copy.deepcopy(request)
        for job in simultaneous['data']['jobs']:
            job['arrival_ms'] = 0
        empty = copy.deepcopy(request)
        empty['data']['jobs'] = []
        for name, edge in (('simultaneous_public_arrivals', simultaneous), ('empty_public_jobs', empty)):
            try:
                run_public_sample(source, {**materials, 'sample': json.dumps(edge)}, case_id,
                                  timeout=timeout, _edge_check=False)
            except (ValueError, RuntimeError) as exc:
                raise ValueError(name + ': ' + str(exc)) from exc
        report['derived_public_checks'] = ['simultaneous_public_arrivals', 'empty_public_jobs']
    return report
