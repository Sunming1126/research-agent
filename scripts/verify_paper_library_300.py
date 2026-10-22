"""独立核验 300 篇论文的文件名、PDF 签名、大小和 SHA-256。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from build_paper_library import LIBRARY


def verify() -> dict:
    manifest = json.loads((LIBRARY / "manifest-300.json").read_text(encoding="utf-8"))
    rows = manifest["papers"]
    files = list((LIBRARY / "pdfs-300").glob("*.pdf"))
    if manifest["count"] != 300 or len(rows) != 300 or len(files) != 300:
        raise ValueError("library must contain exactly 300 PDFs")
    names = [row["filename"] for row in rows]
    ids = [row["arxiv_id"] for row in rows]
    if len(set(n.casefold() for n in names)) != 300 or len(set(ids)) != 300:
        raise ValueError("duplicate paper")
    if set(names) != {p.name for p in files}:
        raise ValueError("manifest and PDF directory differ")
    total = 0
    for row in rows:
        path = LIBRARY / "pdfs-300" / row["filename"]
        if not row["abstract_url"].startswith("https://arxiv.org/abs/"):
            raise ValueError(f"unexpected source: {path.name}")
        with path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise ValueError(f"not PDF: {path.name}")
            stream.seek(0)
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != row["bytes"] or digest != row["sha256"]:
            raise ValueError(f"hash/size mismatch: {path.name}")
        total += row["bytes"]
    counts = dict(sorted(Counter(row["selected_category"] for row in rows).items()))
    if len(counts) != 10 or set(counts.values()) != {30}:
        raise ValueError("expected ten fields with thirty papers each")
    return {"verified_pdfs": 300, "categories": counts, "total_bytes": total}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
