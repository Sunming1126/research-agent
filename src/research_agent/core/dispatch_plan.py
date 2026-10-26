from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import CaseContext
from .utils import confined, read_json, sha256_file


FUNCTIONS = {
    "A": "Chat",
    "B": "Literature_Retrieval",
    "C": "Survey",
    "D": "Reproduction",
    "E": "Idea",
}
V3_COUNTS = {letter: count for letter, count in zip("ABCDE", (1, 3, 2, 3, 2))}
CASE_ID_RE = re.compile(r"([A-E])-(\d{2,3})\Z")


@dataclass(frozen=True)
class DispatchPlan:
    """已校验的派题计划；题量和类别来自当前 dispatch，而不是代码常量。"""

    team: str
    schema_version: int
    rules_version: str
    case_ids: tuple[str, ...]
    category_counts: dict[str, int]
    difficulty_by_case: dict[str, str]
    cases: tuple[dict[str, Any], ...]

    @property
    def expected_count(self) -> int:
        return len(self.case_ids)

    @classmethod
    def from_dict(cls, dispatch: dict[str, Any], settings) -> "DispatchPlan":
        """校验 v3 正式派题，同时保留旧 v2/25 题历史联调格式。"""
        if not isinstance(dispatch, dict):
            raise ValueError("dispatch must be an object")
        if dispatch.get("team") != settings.team:
            raise ValueError("dispatch team mismatch")

        schema = dispatch.get("schema_version")
        counts = dispatch.get("cases_per_category")
        rules = dispatch.get("rules_version")
        if schema == 3:
            expected_counts = V3_COUNTS
            if rules != "research-assistant-objective-v3":
                raise ValueError("unsupported v3 dispatch rules version")
            if counts != expected_counts:
                raise ValueError("v3 dispatch category counts must be 1/3/2/3/2")
        elif schema == 2:
            # 旧记录仅用于兼容公开测试，正式 v3 不会走这里。
            if counts != 5 or rules != "research-assistant-objective-v2":
                raise ValueError("unsupported legacy dispatch version")
            expected_counts = {letter: 5 for letter in "ABCDE"}
        else:
            raise ValueError("unsupported dispatch schema version")

        ids = dispatch.get("case_ids")
        entries = dispatch.get("cases")
        if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
            raise ValueError("dispatch case_ids must be a nonempty unique array")
        if not isinstance(entries, list) or len(entries) != len(ids):
            raise ValueError("dispatch case entries mismatch")
        if schema == 3 and len(ids) != sum(V3_COUNTS.values()):
            raise ValueError("v3 dispatch requires 11 cases")
        if schema == 2 and len(ids) != 25:
            raise ValueError("legacy dispatch requires 25 cases")

        actual_counts = {letter: 0 for letter in "ABCDE"}
        for case_id in ids:
            if not isinstance(case_id, str):
                raise ValueError("case_id must be a string")
            match = CASE_ID_RE.fullmatch(case_id)
            if not match or not 1 <= int(match.group(2)) <= 100:
                raise ValueError("invalid dispatch case_id")
            actual_counts[match.group(1)] += 1
        if actual_counts != expected_counts:
            raise ValueError("dispatch category counts do not match its case_ids")

        by_id = {}
        for row in entries:
            if not isinstance(row, dict) or row.get("case_id") in by_id:
                raise ValueError("invalid or duplicate dispatch case entry")
            by_id[row.get("case_id")] = row
        if set(by_id) != set(ids):
            raise ValueError("dispatch entries and IDs differ")

        cases: list[dict[str, Any]] = []
        difficulties: dict[str, str] = {}
        for case_id in ids:
            row = by_id[case_id]
            expected = f"data/{settings.team}/assigned/{case_id}.json"
            if row.get("question_file") != expected:
                raise ValueError("question must be in this team's assigned directory")
            path = confined(settings.workspace, expected)
            if not path.is_file():
                raise ValueError("assigned question file is missing")
            if sha256_file(path) != row.get("question_sha256"):
                raise ValueError("question hash mismatch")
            payload = read_json(path)
            case = CaseContext.from_dict(payload)
            if case.case_id != case_id or case.function != row.get("function") or case.title != row.get("title"):
                raise ValueError("question/dispatch identity mismatch")
            if case_id.startswith("D-") and schema == 3:
                difficulty = row.get("difficulty")
                if difficulty not in {"easy", "medium", "hard"}:
                    raise ValueError("v3 D case requires difficulty")
                difficulties[case_id] = difficulty
            cases.append(case.to_dict())
        return cls(settings.team, schema, rules, tuple(ids), dict(expected_counts), difficulties, tuple(cases))


def load_dispatch_plan(settings) -> DispatchPlan:
    """从本队工作区读取并校验当前派题计划。"""
    relative = f"data/{settings.team}/dispatch.json"
    # 平台把派题与 assigned 挂载在 /output；旧本地工作区保持兼容。
    root = settings.output if confined(settings.output, relative).is_file() else settings.workspace
    dispatch = read_json(confined(root, relative))
    return DispatchPlan.from_dict(dispatch, replace(settings, workspace=root))


def verify_current_assignment(settings, case):
    """Single 模式只读本队派题记录和当前 assigned，不遍历其他题目。"""
    dispatch = read_json(confined(settings.output, f"data/{settings.team}/dispatch.json"))
    if dispatch.get("team") != settings.team or dispatch.get("example_only"):
        raise ValueError("invalid current team dispatch")
    schema = dispatch.get("schema_version")
    expected = V3_COUNTS if schema == 3 else {letter: 5 for letter in "ABCDE"}
    rule = f"research-assistant-objective-v{schema}"
    counts = expected if schema == 3 else 5
    if schema not in {2, 3} or dispatch.get("rules_version") != rule or dispatch.get("cases_per_category") != counts:
        raise ValueError("unsupported dispatch metadata")
    ids, rows = dispatch.get("case_ids"), dispatch.get("cases")
    if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
        raise ValueError("invalid dispatch IDs")
    if len(ids) != sum(expected.values()) or len(set(ids)) != len(ids):
        raise ValueError("invalid dispatch case count")
    for letter, count in expected.items():
        if sum(value.startswith(letter + "-") for value in ids) != count:
            raise ValueError("invalid dispatch category count")
    if any(not CASE_ID_RE.fullmatch(value) or not 1 <= int(value.split("-")[1]) <= 100 for value in ids):
        raise ValueError("invalid dispatch case ID")
    if not isinstance(rows, list) or len(rows) != len(ids) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("invalid dispatch entries")
    entry_ids = [row.get("case_id") for row in rows]
    if any(not isinstance(value, str) for value in entry_ids) or set(entry_ids) != set(ids):
        raise ValueError("dispatch entries and IDs differ")
    current = [row for row in rows if row.get("case_id") == case.case_id]
    if case.case_id not in ids or len(current) != 1:
        raise ValueError("question was not assigned to this team")
    row = current[0]
    relative = f"data/{settings.team}/assigned/{case.case_id}.json"
    if row.get("question_file") != relative:
        raise ValueError("assigned path is outside current team")
    path = confined(settings.output, relative)
    if sha256_file(path) != row.get("question_sha256"):
        raise ValueError("assigned question hash mismatch")
    assigned = CaseContext.from_dict(read_json(path))
    if (assigned.to_dict() != case.to_dict() or row.get("function") != case.function
            or row.get("title") != case.title):
        raise ValueError("HTTP question differs from assigned question")

