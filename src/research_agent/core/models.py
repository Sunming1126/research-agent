from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

FUNCTIONS = {"A": "Chat", "B": "Literature_Retrieval", "C": "Survey", "D": "Reproduction", "E": "Idea"}


@dataclass(frozen=True)
class CaseContext:
    """已校验题型、编号和输出 Schema 的单题输入。"""
    case_id: str
    function: str
    title: str
    input: str
    materials: list[str]
    constraints: list[str]
    expected_output: str
    output_schema_ref: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CaseContext":
        if not isinstance(value, dict):
            raise ValueError("case must be an object")
        for key in ("case_id", "function", "title", "input", "expected_output", "output_schema_ref"):
            if not isinstance(value.get(key), str) or not value[key].strip():
                raise ValueError(f"case field {key} must be a nonempty string")
        for key in ("materials", "constraints"):
            if not isinstance(value.get(key), list) or any(not isinstance(x, str) or not x.strip() for x in value[key]):
                raise ValueError(f"case field {key} must be a string array")
        match = re.fullmatch(r"([A-E])-(\d{2,3})", value["case_id"])
        if not match or not 1 <= int(match[2]) <= 100:
            raise ValueError("invalid case_id")
        if value["function"] != FUNCTIONS[match[1]] or value["output_schema_ref"] != value["function"]:
            raise ValueError("case prefix/function/schema mismatch")
        return cls(**{key: value[key] for key in cls.__dataclass_fields__})

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class MaterialRef:
    path: Path
    filename: str
    kind: str
    sha256: str


@dataclass(frozen=True)
class Chunk:
    """可追溯到文件、PDF 页码及原文偏移的检索片段。"""
    chunk_id: str
    filename: str
    page: int | None
    heading: str
    text: str
    kind: str
    sha256: str
    start: int = 0
    end: int = 0
    document_sha256: str = ""


@dataclass(frozen=True)
class RetrievalHit:
    chunk: Chunk
    score: float
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class Evidence:
    """报告内部事实台账；quote 必须来自对应原文片段。"""
    evidence_id: str
    claim: str
    filename: str
    page: int | None
    chunk_id: str
    quote: str
    kind: str = "paper_claim"
    facet: str = "unspecified"
    role: str = "unspecified"
    verification: str = "quote_only"


@dataclass(frozen=True)
class Artifact:
    path: str
    kind: str
    bytes: int
    sha256: str

