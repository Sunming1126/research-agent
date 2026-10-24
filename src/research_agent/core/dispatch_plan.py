from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import CaseContext
from .utils import confined, read_json, sha256_bytes
import json


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
    def from_dict(cls, dispatch: dict[str, Any], settings, *, current_case_id=None, assigned_root=None) -> "DispatchPlan":
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

        if current_case_id is not None and current_case_id not in by_id:
            raise ValueError("current case is not assigned to this submission")

        cases: list[dict[str, Any]] = []
        difficulties: dict[str, str] = {}
        # Single 只读取当前题正文；其他题只检查 dispatch 中的公开元数据。
        for case_id in ([current_case_id] if current_case_id is not None else ids):
            row = by_id[case_id]
            expected = f"data/{settings.team}/assigned/{case_id}.json"
            if row.get("question_file") != expected:
                raise ValueError("question must be in this team's assigned directory")
            path = confined(assigned_root or settings.workspace, expected)
            if not path.is_file():
                raise ValueError("assigned question file is missing")
            raw = path.read_bytes()
            if sha256_bytes(raw) != row.get("question_sha256"):
                raise ValueError("question hash mismatch")
            payload = json.loads(raw.decode("utf-8-sig"))
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


def _dispatch_root(settings):
    """优先使用实际派题挂载；只检查本队精确路径，不扫描队伍目录。"""
    relative = f"data/{settings.team}/dispatch.json"
    candidates = list(dict.fromkeys([settings.output, settings.workspace]))
    existing = [root for root in candidates if confined(root, relative).is_file()]
    if len(existing) > 1:
        raise ValueError("multiple team dispatch files found; ambiguous assignment root")
    return existing[0] if existing else settings.output


def load_dispatch_plan(settings, *, current_case_id=None) -> DispatchPlan:
    """从本队工作区读取并校验当前派题计划。"""
    relative = f"data/{settings.team}/dispatch.json"
    root = _dispatch_root(settings)
    dispatch = read_json(confined(root, relative))
    return DispatchPlan.from_dict(dispatch, settings, current_case_id=current_case_id, assigned_root=root)


def validate_current_assignment(settings, payload):
    """在任何材料读取或模型调用前核对当前题的摘要、身份与完整八字段。"""
    case = CaseContext.from_dict(payload)
    plan = load_dispatch_plan(settings, current_case_id=case.case_id)
    if plan.cases != (case.to_dict(),):
        raise ValueError("request differs from the assigned question")
    return case

