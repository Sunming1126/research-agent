"""在独立目录扩充 300 篇可复查 arXiv PDF；支持中断后续跑。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from collections import Counter
from pathlib import Path

from build_paper_library import LIBRARY, inspect_pdf, query_arxiv, request_bytes


SOURCE = LIBRARY / "manifest.json"
DESTINATION = LIBRARY / "manifest-300.json"
PDFS = LIBRARY / "pdfs-300"
TARGET_PER_CATEGORY = 30


def save(rows: list[dict]) -> None:
    payload = {"name": "research-agent-300-paper-simulation", "version": 1,
               "selection": "100 previously verified PDFs plus 20 arXiv category samples per field",
               "target_count": 300, "count": len(rows),
               "papers": sorted(rows, key=lambda row: row["filename"])}
    DESTINATION.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build() -> None:
    PDFS.mkdir(parents=True, exist_ok=True)
    source = json.loads(SOURCE.read_text(encoding="utf-8"))["papers"]
    rows = json.loads(DESTINATION.read_text(encoding="utf-8"))["papers"] if DESTINATION.is_file() else list(source)
    by_id = {row["arxiv_id"] for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("duplicate arXiv ID")
    for row in source:
        old, new = LIBRARY / "pdfs" / row["filename"], PDFS / row["filename"]
        if not new.exists():
            # 同盘硬链接避免重复占用空间；不支持时退回复制。
            try:
                os.link(old, new)
            except OSError:
                shutil.copy2(old, new)
    save(rows)
    counts = Counter(row["selected_category"] for row in rows)
    last_api = 0.0
    for category in sorted(counts):
        offset = 0
        while counts[category] < TARGET_PER_CATEGORY and offset < 1000:
            gap = 3.2 - (time.monotonic() - last_api)
            if gap > 0:
                time.sleep(gap)
            for attempt in range(3):
                try:
                    candidates = query_arxiv(category=category, start=offset, limit=100)
                    break
                except Exception as exc:
                    if attempt == 2:
                        raise
                    print(f"API_RETRY {category} {type(exc).__name__}", flush=True)
                    time.sleep(3.2 * (attempt + 1))
            last_api = time.monotonic()
            if not candidates:
                break
            offset += len(candidates)
            for paper in candidates:
                if counts[category] >= TARGET_PER_CATEGORY:
                    break
                arxiv_id = paper["arxiv_id"]
                if arxiv_id in by_id or paper["primary_category"] != category:
                    continue
                filename = f"{arxiv_id}.pdf"
                target = PDFS / filename
                try:
                    if not target.exists():
                        target.write_bytes(request_bytes(paper["pdf_url"]))
                    pages, size, digest = inspect_pdf(target)
                    rows.append({**paper, "selected_category": category, "filename": filename,
                                 "pages": pages, "bytes": size, "sha256": digest,
                                 "origin": "category_api_300"})
                    by_id.add(arxiv_id)
                    counts[category] += 1
                    save(rows)
                    print(f"OK {len(rows):03d}/300 {category} {filename}", flush=True)
                except Exception as exc:
                    target.unlink(missing_ok=True)
                    print(f"SKIP {category} {arxiv_id} {type(exc).__name__}", flush=True)
                time.sleep(1.0)
        if counts[category] != TARGET_PER_CATEGORY:
            raise RuntimeError(f"{category}: {counts[category]}/{TARGET_PER_CATEGORY}")
    if len(rows) != 300:
        raise RuntimeError(f"incomplete: {len(rows)}/300")
    print("COMPLETE 300 verified PDFs", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.parse_args()
    build()
