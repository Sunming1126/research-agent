import math
import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text):
    """Lowercase regex [a-z0-9]+ tokenization per method.md."""
    if text is None:
        return []
    return _TOKEN_RE.findall(str(text).lower())


def _build_features(texts, vocabulary, idf):
    """Raw TF * IDF vectors, L2-normalized, with bias=1 appended."""
    index = {tok: i for i, tok in enumerate(vocabulary)}
    dim = len(vocabulary)
    rows = []
    for text in texts:
        vec = [0.0] * dim
        for tok in _tokenize(text):
            pos = index.get(tok)
            if pos is not None:
                vec[pos] += idf.get(tok, 0.0)
        norm_sq = 0.0
        for v in vec:
            norm_sq += v * v
        if norm_sq > 0.0:
            norm = math.sqrt(norm_sq)
            vec = [v / norm for v in vec]
        else:
            vec = [0.0] * dim
        vec.append(1.0)
        rows.append(vec)
    return rows


def _softmax(logits):
    m = max(logits)
    exps = [math.exp(z - m) for z in logits]
    total = sum(exps)
    if total <= 0.0:
        n = len(exps)
        return [1.0 / n] * n
    return [e / total for e in exps]


def _predict(rows, classes, weights):
    preds = []
    for row in rows:
        logits = []
        for c in range(len(classes)):
            w = weights[c]
            s = 0.0
            for j in range(len(row)):
                s += w[j] * row[j]
            logits.append(s)
        best = 0
        for k in range(1, len(classes)):
            if logits[k] > logits[best]:
                best = k
        preds.append(classes[best])
    return preds


def _fit(train, epochs, learning_rate, penalty):
    classes = sorted({str(item["label"]) for item in train})
    texts = [item.get("text", "") for item in train]
    label_index = {c: i for i, c in enumerate(classes)}
    targets = [label_index[str(item["label"])] for item in train]

    # Vocabulary + IDF fitted on training set only.
    df = {}
    for text in texts:
        seen = set()
        for tok in _tokenize(text):
            if tok not in seen:
                seen.add(tok)
                df[tok] = df.get(tok, 0) + 1
    vocabulary = sorted(df.keys())
    n_docs = len(texts)
    idf = {}
    for tok in vocabulary:
        doc_freq = df[tok]
        idf[tok] = 1.0 + math.log((1.0 + n_docs) / (1.0 + doc_freq))

    rows = _build_features(texts, vocabulary, idf)

    n_classes = len(classes)
    dim = len(vocabulary) + 1
    weights = [[0.0] * dim for _ in range(n_classes)]

    n_samples = len(rows)
    if n_samples == 0 or n_classes == 0:
        return classes, vocabulary, idf, weights, [], []

    for _ in range(epochs):
        grad = [[0.0] * dim for _ in range(n_classes)]
        for row, t in zip(rows, targets):
            logits = []
            for c in range(n_classes):
                w = weights[c]
                s = 0.0
                for j in range(dim):
                    s += w[j] * row[j]
                logits.append(s)
            probs = _softmax(logits)
            for c in range(n_classes):
                diff = probs[c] - (1.0 if c == t else 0.0)
                if diff != 0.0:
                    gc = grad[c]
                    for j in range(dim):
                        gc[j] += diff * row[j]
        inv_n = 1.0 / n_samples
        for c in range(n_classes):
            w = weights[c]
            gc = grad[c]
            for j in range(dim):
                data_grad = gc[j] * inv_n + penalty * w[j]
                w[j] -= learning_rate * data_grad

    return classes, vocabulary, idf, weights, rows, targets


def _macro_f1(true_labels, pred_labels):
    if not true_labels:
        return 0.0
    labels = sorted(set(true_labels) | set(pred_labels))
    scores = []
    for label in labels:
        tp = fp = fn = 0
        for t, p in zip(true_labels, pred_labels):
            if p == label and t == label:
                tp += 1
            elif p == label and t != label:
                fp += 1
            elif p != label and t == label:
                fn += 1
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        if precision + recall > 0.0:
            scores.append(2.0 * precision * recall / (precision + recall))
        else:
            scores.append(0.0)
    return sum(scores) / len(scores)


def _accuracy(true_labels, pred_labels):
    if not true_labels:
        return 0.0
    correct = sum(1 for t, p in zip(true_labels, pred_labels) if t == p)
    return correct / len(true_labels)


def run_experiment(request):
    request = request or {}
    protocol = request.get("protocol") or {}
    epochs = int(protocol.get("epochs", 100))
    learning_rate = float(protocol.get("learning_rate", 0.8))
    penalty = float(protocol.get("penalty", 0.001))

    data = request.get("data") or {}
    train = data.get("train") or []
    texts = data.get("texts") or []
    if texts is None:
        texts = []

    train_labels = [str(item.get("label")) for item in train]

    # Majority-class baseline (lexicographic tie-break, lexical label order).
    if train_labels:
        counts = {}
        for lab in train_labels:
            counts[lab] = counts.get(lab, 0) + 1
        best_label = None
        best_count = -1
        for lab in sorted(counts.keys()):
            if counts[lab] > best_count:
                best_count = counts[lab]
                best_label = lab
        baseline = [best_label for _ in texts]
    else:
        baseline = ["" for _ in texts]

    classes, vocabulary, idf, weights, train_rows, train_targets = _fit(
        train, epochs, learning_rate, penalty
    )

    details = {}
    candidate = None

    if classes:
        # Model must reproduce candidate predictions from the reported model.
        try:
            test_rows = _build_features(texts, vocabulary, idf)
            candidate = _predict(test_rows, classes, weights)
        except Exception:
            candidate = None

        if candidate is not None:
            all_finite = True
            for row in weights:
                for v in row:
                    if not math.isfinite(v):
                        all_finite = False
                        break
                if not all_finite:
                    break
            for tok in vocabulary:
                if not math.isfinite(idf[tok]):
                    all_finite = False
                    break

            if all_finite:
                # Sanity check: reported model reproduces its own predictions.
                reproduced = _predict(
                    _build_features(texts, vocabulary, idf), classes, weights
                )
                if reproduced != candidate:
                    candidate = reproduced

                predictions = candidate
                accuracy = _accuracy(
                    train_labels, _fit_predictions_inline(
                        train_rows, classes, weights
                    )
                ) if False else None  # training-set check not needed for output

                details = {
                    "classes": list(classes),
                    "vocabulary": list(vocabulary),
                    "idf": {tok: idf[tok] for tok in vocabulary},
                    "weights": [[float(v) for v in row] for row in weights],
                    "train_confusion_macro_f1": _macro_f1(
                        [classes[t] for t in train_targets],
                        _predict(train_rows, classes, weights),
                    ),
                    "train_acc": _accuracy(
                        [classes[t] for t in train_targets],
                        _predict(train_rows, classes, weights),
                    ),
                }
            else:
                candidate = None
                details = {}

    return {
        "baseline": list(baseline),
        "candidate": candidate,
        "details": details,
    }
