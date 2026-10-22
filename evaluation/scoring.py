"""依据 research-assistant-objective-v2 公式计算本地诊断分。"""

from __future__ import annotations


MAXIMA = {"A": 5, "B": 15, "C": 25, "D": 40, "E": 15}
IDEA_WEIGHTS = {
    "motivation": 1.5,
    "route": 1.5,
    "experiment": 1.5,
    "benchmark": 1.5,
    "metrics": 1.5,
    "bottleneck": 3.0,
    "complement": 3.0,
    "feasibility": 1.5,
}


def clamp(value: float, maximum: float) -> float:
    return max(0.0, min(maximum, value))


def score_chat(result: dict) -> float:
    """A 类按两个非空文本字段给分；外层须先校验 Schema。"""
    return 3.0 * bool(str(result.get("answer") or "").strip()) + 2.0 * bool(
        str(result.get("clarifying_question") or "").strip()
    )


def score_retrieval(returned: list[str], gold: list[str]) -> float:
    """B 类完整文件名大小写不敏感去重，空金标准只接受空列表。"""
    found = {name.casefold() for name in returned}
    expected = {name.casefold() for name in gold}
    if not expected:
        return 15.0 if not found else 0.0
    hits = len(found & expected)
    unrelated = len(found - expected)
    return clamp(15.0 * hits / len(expected) - 0.75 * unrelated, 15.0)


def score_survey(correct: int, frozen: int, severe_errors: int, valid_refs: int, required_refs: int = 5) -> dict:
    """C 类事实与引文分别计分；事实判断需人工对照冻结证据。"""
    if frozen < 1 or required_refs < 1 or min(correct, severe_errors, valid_refs) < 0:
        raise ValueError("invalid survey judgment")
    if correct > frozen or valid_refs > required_refs:
        raise ValueError("survey judgment exceeds frozen set")
    facts = clamp(15.0 * correct / frozen - 3.0 * severe_errors, 15.0)
    citations = clamp(10.0 * valid_refs / required_refs, 10.0)
    return {"facts": facts, "citations": citations, "total": facts + citations}


def score_idea(covered: set[str]) -> float:
    """E 类仅对已核对文本证据的冻结要点计分。"""
    if not covered <= set(IDEA_WEIGHTS):
        raise ValueError("unknown Idea criterion")
    return sum(IDEA_WEIGHTS[key] for key in covered)


def score_metric(observed: float | None, reference: float | None, tolerance: float,
                 absolute_tolerance: float = 0.0, verified: bool = True) -> float:
    """D 类单指标公式；零参考值使用绝对容差。"""
    if not verified or observed is None or reference is None:
        return 0.0
    if tolerance < 0 or absolute_tolerance < 0:
        raise ValueError("negative metric tolerance")
    error = abs(observed - reference) / abs(reference) if reference else abs(observed)
    allowed = tolerance if reference else absolute_tolerance
    if error <= allowed:
        return 4.0
    if error == 0:
        return 4.0
    return clamp(max(0.4, 4.0 * allowed / error), 4.0)


def score_experiment(checkpoints: list[bool], metric_scores: list[float], chart_checks: list[bool]) -> dict:
    """D 类八个流水线检查点、三项指标、六项图表检查。"""
    if len(checkpoints) != 8 or len(metric_scores) != 3 or len(chart_checks) != 6:
        raise ValueError("wrong experiment check count")
    pipeline = 20.0 * sum(checkpoints) / 8.0
    metrics = sum(metric_scores)
    chart = 8.0 * sum(chart_checks) / 6.0
    return {"pipeline": pipeline, "metrics": metrics, "chart": chart,
            "total": pipeline + metrics + chart}


def aggregate(categories: dict[str, list[float]], mode: str) -> dict:
    """正式形态须每类五题；五题诊断形态须每类一题。"""
    needed = {"official_shape": 5, "diagnostic": 1}.get(mode)
    if needed is None or set(categories) != set(MAXIMA):
        raise ValueError("invalid round shape")
    if any(len(categories[letter]) != needed for letter in MAXIMA):
        raise ValueError("incomplete round; cannot calculate aggregate")
    if any(not 0 <= score <= MAXIMA[letter] for letter, values in categories.items() for score in values):
        raise ValueError("case score outside category maximum")
    averages = {letter: sum(categories[letter]) / needed for letter in MAXIMA}
    return {"mode": mode, "category_scores": averages, "total": sum(averages.values()),
            "composite_score": None}
