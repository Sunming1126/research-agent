"""公开输入自测：只判断运行和公开可验证约束，不生成官方指标。"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from ..core.session import checkpoint, current_session, remaining_timeout
from .d_public_cases import derived_public_cases
from .d_public_metrics import validate_search_metrics, validate_ranking_order, validate_bm25_rankings


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
                training_df = Counter()
                for row in data["train"]:
                    training_df.update(set(re.findall(r"[a-z0-9]+", row["text"].lower())))
                if vocabulary != sorted(training_df):
                    raise ValueError("D-01 vocabulary must be fitted only on all training texts")
                for token, observed in zip(vocabulary, idf):
                    expected = 1 + math.log((1 + len(data["train"])) / (1 + training_df[token]))
                    if not math.isclose(observed, expected, rel_tol=1e-9, abs_tol=1e-10):
                        raise ValueError("D-01 IDF must use smoothed training document frequency")
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
                validate_ranking_order(ranking)
            config = (request["protocol"].get("default_config") if name == "baseline"
                      else result["details"].get("selected"))
            if isinstance(config, dict) and {"k1", "b", "title_weight"} <= set(config):
                validate_bm25_rankings(value, data["test_queries"], data["documents"], config)
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
                    validate_search_metrics(row, data.get("dev_queries", []), documents)
                def selection_key(row):
                    metrics, config = row["group_metrics"], row["config"]
                    return tuple(-round(metrics[k], 12) for k in (
                        "worst_mrr_at_10", "macro_mrr_at_10", "macro_recall_at_5_percent")) + tuple(
                        config[k] for k in ("title_weight", "k1", "b"))
                if search and min(search, key=selection_key)["config"] != details["selected"]:
                    expected = min(search, key=selection_key)["config"]
                    raise ValueError("D-02 selected config does not follow the public group-robust selection rule; "
                                     + f"public selected={details['selected']}, expected={expected}. "
                                     "Use MIN of (-rounded worst MRR,-rounded macro MRR,-rounded macro recall,"
                                     "title_weight,k1,b): maximize the first three metrics, MINIMIZE tied parameters.")
        elif case_id == "D-05":
            if not isinstance(value, dict) or set(value) != {"trace", "served_ms", "dropped"}:
                raise ValueError("D-05 run requires trace/served_ms/dropped")
            if not isinstance(value["trace"], list) or not isinstance(value["dropped"], list):
                raise ValueError("D-05 trace and dropped must be lists")
            jobs = {j["id"]: j for j in data["jobs"]}
            ids = [j.get("id") for j in value["trace"] + value["dropped"]]
            if len(ids) != len(jobs) or set(ids) != set(jobs):
                raise ValueError("D-05 every job must occur exactly once in trace or dropped; "
                                 + f"missing={sorted(set(jobs) - set(ids))}, "
                                   f"duplicates={[key for key, count in Counter(ids).items() if count > 1]}. "
                                 "At horizon, account for ALL original input jobs, including future arrivals.")
            flows = data["flow_weights"]
            if not isinstance(value["served_ms"], dict) or set(value["served_ms"]) != set(flows):
                actual_keys = sorted(value["served_ms"]) if isinstance(value["served_ms"], dict) else []
                raise ValueError("D-05 served_ms must include every declared flow: "
                                 + f"expected keys={sorted(flows)}, actual keys={actual_keys}. "
                                 "Initialize served_ms from ALL data['flow_weights'] keys, NOT from jobs or trace; "
                                 "flows without jobs/completions must remain zero, including empty jobs input.")
            served, last_end, last_flow = defaultdict(float), {}, {}
            trace = value["trace"]
            if trace != sorted(trace, key=lambda row: (row["dispatch_ms"], row["server"])):
                raise ValueError("D-05 trace must follow dispatch time and server order")
            for position, job in enumerate(trace):
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
                if job["dispatch_ms"] < original["arrival_ms"] or job["dispatch_ms"] >= data["horizon_ms"]:
                    raise ValueError("D-05 dispatch must be after arrival and before horizon")
                expected_setup = data["setup_ms"] if server in last_flow and last_flow[server] != job["flow"] else 0
                if abs(job["setup_ms"] - expected_setup) > 1e-8:
                    raise ValueError("D-05 setup must retain the server's last flow across idle gaps")
                if (abs(job["end_ms"] - job["start_ms"] - original["service_ms"]) > 1e-8
                        or abs(job["wait_ms"] - (job["start_ms"] - original["arrival_ms"])) > 1e-8
                        or abs(job["start_ms"] - job["dispatch_ms"] - job["setup_ms"]) > 1e-8
                        or job["start_ms"] >= original["deadline_ms"] or job["end_ms"] > data["horizon_ms"]):
                    raise ValueError("D-05 service/wait/setup/deadline/horizon invariants do not hold")
                # 仅核验所报轨迹中已经可派发的完成任务之间的优先顺序，
                # 不生成参考轨迹，也不将这里的公开诊断当作正式评分。
                ready = [other for other in trace[position:]
                         if other["arrival_ms"] <= job["dispatch_ms"] < other["deadline_ms"]]
                def policy_key(other):
                    arrival = (other["arrival_ms"], other["id"])
                    return ((served[other["flow"]] / flows[other["flow"]],) + arrival
                            if name == "candidate" else arrival)
                if ready and min(ready, key=policy_key)["id"] != job["id"]:
                    raise ValueError("D-05 dispatch violates FIFO/fair ordering with immediately reserved service")
                served[original["flow"]] += original["service_ms"]
                last_end[server] = job["end_ms"]
                last_flow[server] = job["flow"]
            if any(abs(value["served_ms"][flow] - served[flow]) > 1e-8 for flow in flows):
                raise ValueError("D-05 served_ms differs from completed trace service")
            if value["dropped"] != sorted(value["dropped"], key=lambda row: row["id"]):
                raise ValueError("D-05 dropped jobs must be sorted by id")
            for dropped in value["dropped"]:
                if dropped.get("reason") not in {"deadline", "horizon"}:
                    raise ValueError("D-05 dropped jobs require a public reason")
                if jobs[dropped["id"]]["arrival_ms"] >= data["horizon_ms"] and dropped["reason"] != "horizon":
                    raise ValueError("D-05 unarrived jobs at horizon must be dropped as horizon, not deadline")
    return {"status": "passed", "scope": "public_sample_structure_and_invariants",
            "candidate_present": result["candidate"] is not None}


def run_public_sample(source: Path, materials, case_id, *, timeout=12, _edge_check=True, _deadline=None):
    protocol, request = json.loads(materials["protocol"]), json.loads(materials["sample"])
    if request.get("case_id") != case_id or protocol.get("case_id") != case_id:
        raise ValueError("public sample or protocol belongs to another case")
    request = dict(request, protocol=protocol)
    checkpoint("d_public_sample")
    if _deadline is None:
        _deadline = time.monotonic() + remaining_timeout(timeout, reserve=40)
    timeout = min(8, _deadline - time.monotonic(), remaining_timeout(timeout, reserve=40))
    if timeout <= 0:
        raise RuntimeError("public self-test total time budget exhausted")
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
    if _edge_check:
        checked = []
        for name, edge in derived_public_cases(case_id, request):
            try:
                run_public_sample(source, {**materials, 'sample': json.dumps(edge)}, case_id,
                                  timeout=timeout, _edge_check=False, _deadline=_deadline)
            except (ValueError, RuntimeError) as exc:
                # 模型必须看到触发问题的公开派生输入，只有抽象错误名不足以修复。
                context = json.dumps(edge['data'], ensure_ascii=False, separators=(',', ':'))
                raise ValueError(name + ': ' + str(exc) + '\nFailing derived public data: ' + context[:1400]) from exc
            checked.append(name)
        report['derived_public_checks'] = checked
    return report
