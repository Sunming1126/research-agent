from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def safe_relative_path(path: str, *, required_prefix: str | None = None) -> bool:
    if not isinstance(path, str) or not path or any(x in path for x in ("\\", ":", "\x00")):
        return False
    if any(part in {"", ".", ".."} for part in path.split("/")):
        return False
    return not required_prefix or path.startswith(required_prefix.rstrip("/") + "/")


def confined(root: Path, relative: str) -> Path:
    """拒绝越界、符号链接和 junction，返回授权根目录内的路径。"""
    if not safe_relative_path(relative):
        raise ValueError(f"unsafe relative path: {relative}")
    root = root.resolve()
    target = root.joinpath(*relative.split("/"))
    current = target
    while current != root:
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("symlinks/junctions are forbidden")
        current = current.parent
    resolved = target.resolve()
    if root not in resolved.parents:
        raise ValueError("path escapes authorized root")
    return resolved


def atomic_text(path: Path, content: str, *, durable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            if durable:
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if path.read_text(encoding="utf-8") != content:
        raise IOError("artifact readback mismatch")


def write_json(path: Path, value: Any, *, durable: bool = False) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", durable=durable)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def source_quote_matches(quote: str, source: str) -> bool:
    """只容忍 PDF 换行、连字符和字形差异，仍要求连续原文字符序列。"""
    def canonical(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return "".join(char for char in normalized if char.isalnum())

    excerpt = canonical(quote)
    return len(excerpt) >= 12 and excerpt in canonical(source)


def tokenize(value: str) -> list[str]:
    stops = {"the", "and", "of", "to", "in", "a", "is", "for", "with", "on", "from", "paper", "pdf"}
    terms = [x for x in re.findall(r"[a-z0-9][a-z0-9_+.#/-]*", value.lower()) if x not in stops]
    for run in re.findall(r"[\u4e00-\u9fff]+", value):
        terms.extend(run[i:i+2] for i in range(len(run)-1))
    return terms

