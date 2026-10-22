#!/usr/bin/env python3
"""Run one public D-class CPU experiment.

The data and protocol in this directory are deliberately small and public.  This
module does not read a private task configuration or use a paper value as an
observation.  Platform-level file/syscall events are collected by the runner;
stdout only reports events that this program actually completed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import train_test_split


SEED = 42
TASK_IDS = {
    "D-01": "TASK_D_01_SINGLE_MACHINE",
    "D-02": "TASK_D_02_SINGLE_MACHINE",
    "D-03": "TASK_D_03_SINGLE_MACHINE",
    "D-04": "TASK_D_04_SINGLE_MACHINE",
    "D-05": "TASK_D_05_SINGLE_MACHINE",
}
METRIC_SPECS = {
    "D-01": [("Macro-F1", "分数"), ("Accuracy", "%"), ("运行时间", "秒")],
    "D-02": [("Recall@5", "%"), ("MRR@10", "分数"), ("查询吞吐", "queries/s")],
    "D-03": [("推理吞吐", "samples/s"), ("P95 延迟", "ms"), ("缓存命中率", "%")],
    "D-04": [("验证集准确率", "%"), ("约束通过率", "%"), ("搜索耗时", "分钟")],
    "D-05": [("公平性 Jain 指数", "分数"), ("归一化吞吐", "%"), ("平均等待时间", "ms")],
}
SAFE_TEAM = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


def emit(event_type: str, **fields: Any) -> None:
    """Emit one actual event; the runner is responsible for trusted evidence."""

    payload = {"type": event_type, **fields}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"public data must be an object: {path}")
    return value


def safe_team(value: str) -> str:
    cleaned = SAFE_TEAM.sub("_", value.strip()).strip("._-")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("team must contain at least one safe character")
    return cleaned[:80]


def finite_float(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} is not finite")
    return number


def canonical_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    path.write_text(encoded + "\n", encoding="utf-8")


def csv_write(path: Path, rows: Iterable[dict[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def versions() -> dict[str, str]:
    versions_map = {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "matplotlib": matplotlib.__version__,
        "scikit-learn": __import__("sklearn").__version__,
    }
    try:
        versions_map["scipy"] = __import__("scipy").__version__
    except ImportError:
        versions_map["scipy"] = "unavailable"
    return versions_map


def metric_items(specs: Sequence[tuple[str, str]], values: Sequence[float]) -> list[dict[str, Any]]:
    if len(specs) != len(values):
        raise ValueError("metric value count does not match the public metric contract")
    return [
        {"name": name, "unit": unit, "value": finite_float(value, name)}
        for (name, unit), value in zip(specs, values)
    ]


def submission_metrics(metrics: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": item["name"],
            "unit": item["unit"],
            "reference": None,
            "observed": item["value"],
            "relative_error": None,
        }
        for item in metrics
    ]


def prepare_output(args: argparse.Namespace) -> tuple[Path, Path, str, str]:
    case_id = args.case
    if case_id not in TASK_IDS:
        raise ValueError(f"unsupported public case: {case_id}")
    if args.task_id != TASK_IDS[case_id]:
        raise ValueError(f"task_id does not match {case_id}: {args.task_id}")
    if not args.run_id.strip() or any(char.isspace() for char in args.run_id):
        raise ValueError("run-id must be a non-empty token without whitespace")
    output = Path(args.output).resolve()
    data_root = Path(args.data).resolve()
    output.mkdir(parents=True, exist_ok=True)
    data_file = data_root / f"{case_id}.json"
    if not data_file.is_file():
        raise FileNotFoundError(f"public data file is missing: {data_file}")
    public_data = read_json(data_file)
    if public_data.get("case_id") != case_id:
        raise ValueError(f"public data case_id mismatch in {data_file}")
    return output, data_file, safe_team(args.team), case_id


def d01(data: dict[str, Any], output: Path, team: str) -> tuple[list[float], list[dict[str, Any]], str]:
    records = data.get("records")
    if not isinstance(records, list) or len(records) < 12:
        raise ValueError("D-01 requires at least 12 public records")
    texts = [str(item["text"]) for item in records]
    labels = np.asarray([str(item["label"]) for item in records])
    ids = [str(item["id"]) for item in records]
    indices = np.arange(len(texts))
    train_idx, test_idx = train_test_split(
        indices,
        test_size=float(data["test_size"]),
        random_state=SEED,
        stratify=labels,
    )
    emit("stage_event", stage="data_preprocess", case_id="D-01", records=len(records), train=len(train_idx), test=len(test_idx))
    vectorizer = TfidfVectorizer(lowercase=True, token_pattern=r"(?u)\b\w+\b")
    x_train = vectorizer.fit_transform([texts[i] for i in train_idx])
    x_test = vectorizer.transform([texts[i] for i in test_idx])
    train_labels = labels[train_idx]
    test_labels = labels[test_idx]
    majority = str(sorted(set(train_labels), key=lambda label: (-int(np.sum(train_labels == label)), label))[0])
    t0 = time.perf_counter()
    model = LogisticRegression(max_iter=200, random_state=SEED, solver="liblinear", multi_class="auto")
    model.fit(x_train, train_labels)
    predictions = model.predict(x_test)
    elapsed = time.perf_counter() - t0
    macro_f1 = f1_score(test_labels, predictions, average="macro", zero_division=0)
    accuracy = accuracy_score(test_labels, predictions) * 100.0
    baseline_predictions = np.full(test_labels.shape, majority, dtype=object)
    baseline_f1 = f1_score(test_labels, baseline_predictions, average="macro", zero_division=0)
    baseline_accuracy = accuracy_score(test_labels, baseline_predictions) * 100.0
    emit("loop_iteration", case_id="D-01", iteration=1, operation="majority_baseline_evaluated", accuracy=baseline_accuracy)
    emit("loop_iteration", case_id="D-01", iteration=2, operation="tfidf_logistic_regression_evaluated", accuracy=accuracy)
    rows = [
        {"model": "majority_baseline", "macro_f1": f"{baseline_f1:.12f}", "accuracy_pct": f"{baseline_accuracy:.12f}", "elapsed_s": "0"},
        {"model": "tfidf_logistic_regression", "macro_f1": f"{macro_f1:.12f}", "accuracy_pct": f"{accuracy:.12f}", "elapsed_s": f"{elapsed:.12f}"},
    ]
    csv_write(output / "data" / "source_data.csv", rows, ["model", "macro_f1", "accuracy_pct", "elapsed_s"])
    figure_dir = output / "data" / "figure" / team
    figure_dir.mkdir(parents=True, exist_ok=True)
    fig_path = figure_dir / "D-01.png"
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.4), constrained_layout=True)
    names = ["Majority", "TF-IDF LR"]
    axes[0].bar(names, [100 * baseline_f1, 100 * macro_f1], color=["#9aa5b1", "#2364aa"])
    axes[0].set_ylabel("Macro-F1 (%)")
    axes[0].set_title("Classification quality")
    axes[0].set_xlabel("Method")
    axes[1].bar(names, [baseline_accuracy, accuracy], color=["#9aa5b1", "#2364aa"], label="held-out test")
    axes[1].set_ylabel("Accuracy (%)")
    axes[1].set_title("Held-out accuracy")
    axes[1].set_xlabel("Method")
    axes[1].legend(loc="lower right")
    fig.suptitle("D-01 public CPU experiment, seed=42")
    fig.savefig(fig_path, dpi=130)
    plt.close(fig)
    return [macro_f1, accuracy, elapsed], [{
        "path": f"data/figure/{team}/D-01.png",
        "caption": "Public 30-record text classification split; seed=42; TF-IDF plus Logistic Regression is compared with the majority baseline on the held-out test set.",
        "x_label": "Model",
        "y_label": "Macro-F1 (score) and Accuracy (%)",
        "legend": "Gray: majority baseline; blue: TF-IDF + Logistic Regression; held-out test data.",
    }], "D-01 TF-IDF classification"


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def bm25_rank(documents: Sequence[dict[str, Any]], query: str, k1: float, b: float) -> list[tuple[str, float]]:
    doc_tokens = [tokens(str(doc["text"])) for doc in documents]
    lengths = np.asarray([len(item) for item in doc_tokens], dtype=float)
    average_length = float(np.mean(lengths))
    query_tokens = tokens(query)
    document_frequency: dict[str, int] = {}
    for term in set(query_tokens):
        document_frequency[term] = sum(term in item for item in doc_tokens)
    scores: list[tuple[str, float]] = []
    for doc, terms, length in zip(documents, doc_tokens, lengths):
        score = 0.0
        counts = {term: terms.count(term) for term in set(query_tokens)}
        for term in query_tokens:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            df = document_frequency[term]
            idf = math.log(1.0 + (len(documents) - df + 0.5) / (df + 0.5))
            denominator = frequency + k1 * (1.0 - b + b * length / average_length)
            score += idf * frequency * (k1 + 1.0) / denominator
        scores.append((str(doc["id"]), score))
    return sorted(scores, key=lambda item: (-item[1], item[0]))


def d02(data: dict[str, Any], output: Path, team: str) -> tuple[list[float], list[dict[str, Any]], str]:
    documents = data.get("documents")
    queries = data.get("queries")
    if not isinstance(documents, list) or not isinstance(queries, list) or not documents or not queries:
        raise ValueError("D-02 requires public documents and queries")
    emit("stage_event", stage="data_preprocess", case_id="D-02", documents=len(documents), queries=len(queries))
    t0 = time.perf_counter()
    rows: list[dict[str, Any]] = []
    recall_values: list[float] = []
    reciprocal_values: list[float] = []
    for iteration, query in enumerate(queries, start=1):
        ranked = bm25_rank(documents, str(query["text"]), float(data["k1"]), float(data["b"]))
        relevant = {str(item) for item in query["relevant_ids"]}
        top5 = [item[0] for item in ranked[:5]]
        top10 = [item[0] for item in ranked[:10]]
        found5 = bool(relevant.intersection(top5))
        rank = next((index + 1 for index, item in enumerate(top10) if item in relevant), None)
        reciprocal = 1.0 / rank if rank is not None else 0.0
        recall_values.append(100.0 if found5 else 0.0)
        reciprocal_values.append(reciprocal)
        emit("loop_iteration", case_id="D-02", iteration=iteration, operation="bm25_query_ranked", query_id=query["id"], top1=ranked[0][0])
        rows.append({
            "query_id": query["id"],
            "top1": ranked[0][0],
            "top5": "|".join(top5),
            "relevant_ids": "|".join(sorted(relevant)),
            "recall_at_5_pct": f"{recall_values[-1]:.12f}",
            "reciprocal_rank_at_10": f"{reciprocal:.12f}",
        })
    elapsed = time.perf_counter() - t0
    throughput = len(queries) / max(elapsed, np.finfo(float).tiny)
    recall = float(np.mean(recall_values))
    mrr = float(np.mean(reciprocal_values))
    csv_write(output / "data" / "source_data.csv", rows, ["query_id", "top1", "top5", "relevant_ids", "recall_at_5_pct", "reciprocal_rank_at_10"])
    figure_dir = output / "data" / "figure" / team
    figure_dir.mkdir(parents=True, exist_ok=True)
    fig_path = figure_dir / "D-02.png"
    ks = np.arange(1, 11)
    cumulative = [100.0 * np.mean([any(item in {str(x) for x in q["relevant_ids"]} for item in [r[0] for r in bm25_rank(documents, str(q["text"]), float(data["k1"]), float(data["b"]))[:k]]) for q in queries]) for k in ks]
    fig, ax = plt.subplots(figsize=(6.6, 3.8), constrained_layout=True)
    ax.plot(ks, cumulative, marker="o", color="#2364aa", label="BM25 cumulative recall")
    ax.axvline(5, color="#c77d00", linestyle="--", label="Recall@5 cutoff")
    ax.set_xlabel("Rank k (documents)")
    ax.set_ylabel("Recall (%)")
    ax.set_title("D-02 BM25 retrieval, seed=42")
    ax.set_xticks(ks)
    ax.legend()
    fig.savefig(fig_path, dpi=130)
    plt.close(fig)
    return [recall, mrr, throughput], [{
        "path": f"data/figure/{team}/D-02.png",
        "caption": "Offline BM25 ranking over 12 public documents and 12 fixed queries; recall is measured against the provided relevant document IDs.",
        "x_label": "Rank k (documents)",
        "y_label": "Recall (%)",
        "legend": "Blue: cumulative BM25 recall; orange dashed: the Recall@5 reporting cutoff.",
    }], "D-02 BM25 retrieval"


def sequence_step(state: np.ndarray, token: int, matrix: np.ndarray, bias: np.ndarray) -> np.ndarray:
    token_offset = float(token) / 256.0
    return np.tanh(matrix @ state + bias + token_offset)


def d03(data: dict[str, Any], output: Path, team: str) -> tuple[list[float], list[dict[str, Any]], str]:
    sequences = data.get("sequences")
    if not isinstance(sequences, list) or len(sequences) < 4:
        raise ValueError("D-03 requires at least four public sequences")
    hidden_size = int(data["hidden_size"])
    prefix_length = int(data["prefix_length"])
    passes = int(data["passes"])
    rng = np.random.default_rng(SEED)
    matrix = rng.normal(0.0, 0.08, size=(hidden_size, hidden_size))
    bias = rng.normal(0.0, 0.02, size=(hidden_size,))
    emit("stage_event", stage="data_preprocess", case_id="D-03", sequences=len(sequences), prefix_length=prefix_length, passes=passes)
    rows: list[dict[str, Any]] = []
    mode_results: dict[str, list[float]] = {"no_cache": [], "prefix_cache": []}
    cache_hits = 0
    cache_lookups = 0
    for mode in ("no_cache", "prefix_cache"):
        cache: dict[tuple[int, ...], np.ndarray] = {}
        for iteration, item in enumerate(sequences, start=1):
            sequence = [int(token) for token in item["tokens"]]
            t0 = time.perf_counter()
            final_norm = 0.0
            for _ in range(passes):
                state = np.zeros(hidden_size, dtype=float)
                start = 0
                prefix_key = tuple(sequence[:prefix_length])
                if mode == "prefix_cache":
                    cache_lookups += 1
                    if prefix_key in cache:
                        state = cache[prefix_key].copy()
                        start = prefix_length
                        cache_hits += 1
                for token in sequence[start:]:
                    state = sequence_step(state, token, matrix, bias)
                if mode == "prefix_cache" and prefix_key not in cache:
                    # The cached state is computed once from the shared prefix.
                    prefix_state = np.zeros(hidden_size, dtype=float)
                    for token in sequence[:prefix_length]:
                        prefix_state = sequence_step(prefix_state, token, matrix, bias)
                    cache[prefix_key] = prefix_state
                final_norm = float(np.linalg.norm(state))
            elapsed = time.perf_counter() - t0
            mode_results[mode].append(elapsed)
            rows.append({"mode": mode, "sequence_id": item["id"], "elapsed_s": f"{elapsed:.12f}", "final_state_norm": f"{final_norm:.12f}"})
            emit("loop_iteration", case_id="D-03", iteration=(0 if mode == "no_cache" else len(sequences)) + iteration, operation="sequence_inference", mode=mode, sequence_id=item["id"])
    cached_times = np.asarray(mode_results["prefix_cache"], dtype=float)
    cached_total = float(np.sum(cached_times))
    throughput = len(sequences) / max(cached_total, np.finfo(float).tiny)
    p95_ms = float(np.percentile(cached_times * 1000.0, 95, method="linear"))
    hit_rate = 100.0 * cache_hits / max(cache_lookups, 1)
    csv_write(output / "data" / "source_data.csv", rows, ["mode", "sequence_id", "elapsed_s", "final_state_norm"])
    figure_dir = output / "data" / "figure" / team
    figure_dir.mkdir(parents=True, exist_ok=True)
    fig_path = figure_dir / "D-03.png"
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.4), constrained_layout=True)
    labels = ["No cache", "Prefix cache"]
    throughputs = [len(sequences) / max(float(np.sum(mode_results["no_cache"])), np.finfo(float).tiny), throughput]
    p95s = [float(np.percentile(np.asarray(mode_results["no_cache"]) * 1000.0, 95)), p95_ms]
    axes[0].bar(labels, throughputs, color=["#9aa5b1", "#2364aa"], label="measured mode")
    axes[0].set_ylabel("Throughput (samples/s)")
    axes[0].set_title("Inference throughput")
    axes[1].bar(labels, p95s, color=["#9aa5b1", "#2364aa"], label="measured mode")
    axes[1].set_ylabel("P95 latency (ms)")
    axes[1].set_title("Latency")
    axes[1].legend()
    fig.suptitle(f"D-03 public sequence experiment, seed=42; cache hit rate={hit_rate:.1f}%")
    fig.savefig(fig_path, dpi=130)
    plt.close(fig)
    return [throughput, p95_ms, hit_rate], [{
        "path": f"data/figure/{team}/D-03.png",
        "caption": "Offline NumPy sequence-state experiment over 12 public sequences; repeated shared prefixes are reused only in the prefix-cache mode.",
        "x_label": "Inference mode",
        "y_label": "Throughput (samples/s) and P95 latency (ms)",
        "legend": "Gray: no-cache execution; blue: prefix-cache execution; cache hit rate is computed from observed prefix lookups.",
    }], "D-03 prefix-cache inference"


def d04(data: dict[str, Any], output: Path, team: str) -> tuple[list[float], list[dict[str, Any]], str]:
    points = data.get("points")
    grid = data.get("grid")
    if not isinstance(points, list) or not isinstance(grid, list) or len(points) < 12 or len(grid) < 2:
        raise ValueError("D-04 requires public points and a non-empty hyperparameter grid")
    x = np.asarray([point["x"] for point in points], dtype=float)
    y = np.asarray([int(point["label"]) for point in points])
    indices = np.arange(len(points))
    train_idx, valid_idx = train_test_split(indices, test_size=float(data["test_size"]), random_state=SEED, stratify=y)
    emit("stage_event", stage="data_preprocess", case_id="D-04", points=len(points), train=len(train_idx), validation=len(valid_idx), candidates=len(grid))
    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    best: tuple[float, dict[str, Any]] | None = None
    passed_count = 0
    for iteration, candidate in enumerate(grid, start=1):
        model = LogisticRegression(C=float(candidate["C"]), max_iter=int(candidate["max_iter"]), random_state=SEED, solver="lbfgs")
        model.fit(x[train_idx], y[train_idx])
        predictions = model.predict(x[valid_idx])
        probabilities = model.predict_proba(x[valid_idx])
        accuracy = float(accuracy_score(y[valid_idx], predictions) * 100.0)
        mean_confidence = float(np.max(probabilities, axis=1).mean())
        passed = accuracy >= float(data["constraint_accuracy_min"]) and mean_confidence >= float(data["constraint_confidence_min"])
        passed_count += int(passed)
        record = {"C": float(candidate["C"]), "max_iter": int(candidate["max_iter"]), "validation_accuracy_pct": accuracy, "mean_confidence": mean_confidence, "constraint_pass": passed}
        rows.append({key: str(value) for key, value in record.items()})
        if passed and (best is None or accuracy > best[0]):
            best = (accuracy, record)
        emit("loop_iteration", case_id="D-04", iteration=iteration, operation="grid_candidate_evaluated", C=float(candidate["C"]), max_iter=int(candidate["max_iter"]), constraint_pass=passed)
    elapsed_minutes = (time.perf_counter() - t0) / 60.0
    selected_accuracy = best[0] if best is not None else max(float(row["validation_accuracy_pct"]) for row in rows)
    pass_rate = 100.0 * passed_count / len(grid)
    csv_write(output / "data" / "source_data.csv", rows, ["C", "max_iter", "validation_accuracy_pct", "mean_confidence", "constraint_pass"])
    figure_dir = output / "data" / "figure" / team
    figure_dir.mkdir(parents=True, exist_ok=True)
    fig_path = figure_dir / "D-04.png"
    labels = [f"C={row['C']}\niter={row['max_iter']}" for row in rows]
    accuracy_values = [float(row["validation_accuracy_pct"]) for row in rows]
    colors = ["#2364aa" if row["constraint_pass"] == "True" else "#c9d1d9" for row in rows]
    fig, ax = plt.subplots(figsize=(7.2, 3.8), constrained_layout=True)
    ax.plot(np.arange(len(labels)), accuracy_values, color="#173f5f", marker="o", label="validation accuracy")
    ax.scatter(np.arange(len(labels)), accuracy_values, c=colors, s=60, zorder=3, label="blue=constraint pass; gray=reject")
    ax.axhline(float(data["constraint_accuracy_min"]), color="#c77d00", linestyle="--", label="accuracy constraint (%)")
    ax.set_xlabel("Grid candidate (C / max_iter)")
    ax.set_ylabel("Validation accuracy (%)")
    ax.set_xticks(np.arange(len(labels)), labels, rotation=25)
    ax.set_title("D-04 public constrained grid search, seed=42")
    ax.legend(fontsize=8)
    fig.savefig(fig_path, dpi=130)
    plt.close(fig)
    return [selected_accuracy, pass_rate, elapsed_minutes], [{
        "path": f"data/figure/{team}/D-04.png",
        "caption": "Six-candidate Logistic Regression grid search on a fixed public two-dimensional dataset; candidates pass only when validation accuracy and mean confidence satisfy the public constraints.",
        "x_label": "Grid candidate (C / max_iter)",
        "y_label": "Validation accuracy (%)",
        "legend": "Line: validation accuracy; blue points: both constraints pass; gray points: rejected; orange dashed: accuracy threshold.",
    }], "D-04 constrained hyperparameter search"


def simulate_queue(jobs: Sequence[dict[str, Any]], classes: Sequence[str], policy: str, horizon: float) -> tuple[list[dict[str, Any]], dict[str, int]]:
    ordered = sorted((dict(job, _order=index) for index, job in enumerate(jobs)), key=lambda job: (float(job["arrival_ms"]), int(job["_order"])))
    pending: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    class_service = {name: 0 for name in classes}
    clock = 0.0
    cursor = 0
    while cursor < len(ordered) or pending:
        if not pending and cursor < len(ordered):
            clock = max(clock, float(ordered[cursor]["arrival_ms"]))
        while cursor < len(ordered) and float(ordered[cursor]["arrival_ms"]) <= clock:
            pending.append(ordered[cursor])
            cursor += 1
        if not pending:
            continue
        if policy == "fifo":
            selected_index = min(range(len(pending)), key=lambda index: (float(pending[index]["arrival_ms"]), int(pending[index]["_order"])))
        elif policy == "fair":
            selected_index = min(
                range(len(pending)),
                key=lambda index: (class_service[str(pending[index]["class"])], float(pending[index]["arrival_ms"]), int(pending[index]["_order"])),
            )
        else:
            raise ValueError(f"unknown queue policy: {policy}")
        job = pending.pop(selected_index)
        start = max(clock, float(job["arrival_ms"]))
        finish = start + float(job["service_ms"])
        if finish > horizon:
            break
        completed.append({
            "id": str(job["id"]),
            "class": str(job["class"]),
            "arrival_ms": float(job["arrival_ms"]),
            "start_ms": start,
            "finish_ms": finish,
            "wait_ms": start - float(job["arrival_ms"]),
        })
        class_service[str(job["class"])] += 1
        clock = finish
    return completed, class_service


def jain(values: Sequence[float]) -> float:
    array = np.asarray(values, dtype=float)
    denominator = len(array) * float(np.sum(array * array))
    return float((np.sum(array) ** 2) / denominator) if denominator else 0.0


def d05(data: dict[str, Any], output: Path, team: str) -> tuple[list[float], list[dict[str, Any]], str]:
    jobs = data.get("jobs")
    classes = data.get("classes")
    if not isinstance(jobs, list) or not isinstance(classes, list) or len(jobs) < 6:
        raise ValueError("D-05 requires public jobs and classes")
    emit("stage_event", stage="data_preprocess", case_id="D-05", jobs=len(jobs), classes=len(classes), horizon_ms=data["horizon_ms"])
    rows: list[dict[str, Any]] = []
    policy_results: dict[str, dict[str, Any]] = {}
    for iteration, policy in enumerate(("fifo", "fair"), start=1):
        completed, class_counts = simulate_queue(jobs, classes, policy, float(data["horizon_ms"]))
        fairness = jain([class_counts[name] for name in classes])
        throughput = 100.0 * len(completed) / len(jobs)
        mean_wait = float(np.mean([item["wait_ms"] for item in completed])) if completed else 0.0
        policy_results[policy] = {"fairness": fairness, "throughput": throughput, "mean_wait": mean_wait, "completed": completed, "class_counts": class_counts}
        emit("loop_iteration", case_id="D-05", iteration=iteration, operation="queue_policy_simulated", policy=policy, completed=len(completed), fairness=fairness)
        for item in completed:
            rows.append({"policy": policy, **{key: item[key] for key in ("id", "class", "arrival_ms", "start_ms", "finish_ms", "wait_ms")}, "class_completed": class_counts[item["class"]]})
    selected = policy_results["fair"]
    csv_write(output / "data" / "source_data.csv", rows, ["policy", "id", "class", "arrival_ms", "start_ms", "finish_ms", "wait_ms", "class_completed"])
    figure_dir = output / "data" / "figure" / team
    figure_dir.mkdir(parents=True, exist_ok=True)
    fig_path = figure_dir / "D-05.png"
    policies = ["FIFO", "Fair"]
    fairness_values = [100 * policy_results["fifo"]["fairness"], 100 * selected["fairness"]]
    throughput_values = [policy_results["fifo"]["throughput"], selected["throughput"]]
    wait_values = [policy_results["fifo"]["mean_wait"], selected["mean_wait"]]
    fig, axes = plt.subplots(1, 3, figsize=(9.4, 3.2), constrained_layout=True)
    for ax, values, label, ylabel in zip(axes, (fairness_values, throughput_values, wait_values), ("Jain fairness", "Normalized throughput", "Mean waiting"), ("Jain index (%)", "Completed jobs (%)", "Waiting time (ms)")):
        ax.bar(policies, values, color=["#9aa5b1", "#2364aa"], label="policy")
        ax.set_title(label)
        ax.set_ylabel(ylabel)
        ax.set_xlabel("Policy")
    axes[2].legend()
    fig.suptitle("D-05 public queue simulation, seed=42")
    fig.savefig(fig_path, dpi=130)
    plt.close(fig)
    return [selected["fairness"], selected["throughput"], selected["mean_wait"]], [{
        "path": f"data/figure/{team}/D-05.png",
        "caption": "Offline discrete-event queue simulation with fixed public arrivals and service times; FIFO and class-fair selection are compared within the 190 ms horizon.",
        "x_label": "Scheduling policy",
        "y_label": "Jain index displayed as percent, completed jobs (%), and waiting time (ms)",
        "legend": "Gray: FIFO; blue: fair class scheduler; all values are computed from completed public jobs.",
    }], "D-05 FIFO and fair queue simulation"


RUNNERS = {"D-01": d01, "D-02": d02, "D-03": d03, "D-04": d04, "D-05": d05}


def run(args: argparse.Namespace) -> None:
    output, data_file, team, case_id = prepare_output(args)
    data = read_json(data_file)
    runner = RUNNERS[case_id]
    started = time.perf_counter()
    values, figures, description = runner(data, output, team)
    metrics = metric_items(METRIC_SPECS[case_id], values)
    metrics_payload = {"run_id": args.run_id, "task_id": args.task_id, "metrics": metrics}
    canonical_write(output / "metrics.json", metrics_payload)
    figure_paths = [output / item["path"] for item in figures]
    if any(not path.is_file() for path in figure_paths):
        raise RuntimeError("experiment figure was not written")
    submission = {
        "case_id": case_id,
        "status": "completed",
        "metrics": submission_metrics(metrics),
        "figures": figures,
    }
    canonical_write(output / "submission.json", submission)
    command = {
        "case_id": case_id,
        "run_id": args.run_id,
        "task_id": args.task_id,
        "seed": SEED,
        "description": description,
        "command": sys.argv,
        "data_file": data_file.name,
        "data_sha256": sha256_file(data_file),
        "versions": versions(),
        "pid": os.getpid(),
        "elapsed_wall_s": time.perf_counter() - started,
        "metric_contract": [{"name": name, "unit": unit} for name, unit in METRIC_SPECS[case_id]],
    }
    canonical_write(output / "command.json", command)
    emit("result_generated", case_id=case_id, run_id=args.run_id, task_id=args.task_id, output_files=["metrics.json", "submission.json", "data/source_data.csv", figures[0]["path"]])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=sorted(RUNNERS))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--team", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True, choices=sorted(set(TASK_IDS.values())))
    return parser.parse_args()


if __name__ == "__main__":
    try:
        run(parse_args())
    except Exception as exc:
        print(json.dumps({"type": "experiment_error", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr, flush=True)
        raise
