"""测量 300 篇论文前八页冷索引，供十轮复用相同内容哈希缓存。"""

from __future__ import annotations

import json
import argparse
import sys
import time

from .benchmark import ROOT
from .build_300_dispatch import BASE, MANIFEST


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page-limit", type=int, choices=(4, 8), default=4)
    page_limit = parser.parse_args().page_limit
    sys.path.insert(0, str(ROOT / "src"))
    from research_agent.core.config import Settings
    from research_agent.rag.materials import DocumentIndex, DocumentParser

    papers = json.loads(MANIFEST.read_text(encoding="utf-8"))["papers"]
    if len(papers) != 300:
        raise ValueError("incomplete paper library")
    out = BASE / f"candidate-cache-{page_limit}-{DocumentParser.VERSION}"
    cache = out / "document-cache"
    cache.mkdir(parents=True, exist_ok=True)
    settings = Settings(workspace=ROOT, output=out, team="index300",
                        material_roots=[ROOT / "paper_library" / "pdfs-300"])
    started = time.monotonic()
    index = DocumentIndex(settings, cache)
    chunks = index.build([p["filename"] for p in papers], page_limit=page_limit)
    result = {"papers": len(index.materials), "page_limit": page_limit,
              "seconds": round(time.monotonic()-started, 2), "chunks": len(chunks),
              "cache_files": len(list(cache.glob("*.json"))),
              "note": "Windows host cold parse; cache is reused in later rounds, not included in per-case times"}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
