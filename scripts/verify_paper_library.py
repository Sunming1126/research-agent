"""独立回读百篇论文清单，验证数量、来源、文件和哈希。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "paper_library"


def verify() -> dict:
    manifest = json.loads((LIBRARY / "manifest.json").read_text(encoding="utf-8"))
    rows = manifest["papers"]
    files = list((LIBRARY / "pdfs").glob("*.pdf"))
    if manifest["count"] != 100 or len(rows) != 100 or len(files) != 100:
        raise ValueError("paper library does not contain exactly 100 PDFs")
    names = [row["filename"] for row in rows]
    ids = [row["arxiv_id"] for row in rows]
    if len(set(name.casefold() for name in names)) != 100 or len(set(ids)) != 100:
        raise ValueError("duplicate filename or arXiv ID")
    if {path.name for path in files} != set(names):
        raise ValueError("manifest and PDF directory differ")
    for row in rows:
        path = LIBRARY / "pdfs" / row["filename"]
        if path.read_bytes()[:5] != b"%PDF-" or path.stat().st_size != row["bytes"]:
            raise ValueError(f"invalid PDF signature or size: {path.name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != row["sha256"]:
            raise ValueError(f"PDF hash mismatch: {path.name}")
        if not row["abstract_url"].startswith("https://arxiv.org/abs/"):
            raise ValueError(f"invalid source URL: {path.name}")
    counts = dict(sorted(Counter(row["selected_category"] for row in rows).items()))
    return {"verified_pdfs": 100, "fields": len(counts), "categories": counts,
            "total_bytes": sum(path.stat().st_size for path in files)}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
