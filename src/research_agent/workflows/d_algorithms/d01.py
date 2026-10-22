from __future__ import annotations

import math
import re
from collections import Counter


TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(str(text).lower())


def _features(train: list[dict], texts: list[str]):
    train_tokens = [_tokens(row.get("text", "")) for row in train]
    vocabulary = sorted({token for row in train_tokens for token in row})
    index = {token: pos for pos, token in enumerate(vocabulary)}
    document_frequency = Counter()
    for row in train_tokens:
        document_frequency.update(set(row))
    n_docs = len(train_tokens)
    idf = [1.0 + math.log((1.0 + n_docs) / (1.0 + document_frequency[token])) for token in vocabulary]

    def encode(tokens):
        counts = Counter(tokens)
        values = [counts[token] * idf[pos] for pos, token in enumerate(vocabulary)]
        norm = math.sqrt(sum(value * value for value in values))
        if norm:
            values = [value / norm for value in values]
        # 空向量也保留 bias，符合 protocol 的输入约定。
        return values + [1.0]

    return vocabulary, idf, [encode(row) for row in train_tokens], [encode(_tokens(text)) for text in texts]


def _softmax(logits: list[float]) -> list[float]:
    maximum = max(logits)
    values = [math.exp(value - maximum) for value in logits]
    total = sum(values)
    return [value / total for value in values]


def _predict(weights: list[list[float]], features: list[list[float]], classes: list[str]) -> list[str]:
    output = []
    for vector in features:
        scores = [sum(weight * value for weight, value in zip(row, vector)) for row in weights]
        best = max(range(len(scores)), key=lambda pos: scores[pos])
        output.append(classes[best])
    return output


def run_d01(request: dict) -> dict:
    """按 D-01 protocol 训练并返回 baseline、candidate 与实际模型。"""
    data = request.get("data") or {}
    train = list(data.get("train") or [])
    texts = list(data.get("texts") or [])
    if not train or not isinstance(texts, list):
        raise ValueError("D-01 requires train and texts")
    protocol = request.get("protocol") or {}
    epochs = int(protocol.get("epochs", 100))
    learning_rate = float(protocol.get("learning_rate", 0.8))
    penalty = float(protocol.get("penalty", 0.001))
    classes = sorted({str(row["label"]) for row in train})
    class_index = {label: index for index, label in enumerate(classes)}
    vocabulary, idf, train_x, test_x = _features(train, texts)
    majority = Counter(str(row["label"]) for row in train)
    majority_label = min(classes, key=lambda label: (-majority[label], label))
    baseline = [majority_label] * len(texts)
    weights = [[0.0] * (len(vocabulary) + 1) for _ in classes]
    n_docs = len(train_x)
    for _ in range(epochs):
        gradient = [[0.0] * len(weights[0]) for _ in weights]
        for row, features in zip(train, train_x):
            probabilities = _softmax([sum(weight * value for weight, value in zip(weights[pos], features))
                                      for pos in range(len(classes))])
            target = class_index[str(row["label"])]
            for pos, probability in enumerate(probabilities):
                error = probability - (1.0 if pos == target else 0.0)
                for col, value in enumerate(features):
                    gradient[pos][col] += error * value / n_docs
        for pos in range(len(weights)):
            for col in range(len(weights[pos])):
                gradient[pos][col] += penalty * weights[pos][col]
                weights[pos][col] -= learning_rate * gradient[pos][col]
    candidate = _predict(weights, test_x, classes)
    return {
        "baseline": baseline,
        "candidate": candidate,
        "details": {
            "classes": classes,
            "vocabulary": vocabulary,
            "idf": idf,
            "weights": weights,
        },
    }

