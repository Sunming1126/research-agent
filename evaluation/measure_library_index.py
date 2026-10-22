"""在本机单逻辑核上测量百篇论文索引的完整冷启动。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

from .benchmark import ROOT
from .library_benchmark import LIBRARY, LIBRARY_ROUNDS, case_payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page-limit", type=int, default=None)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "src"))
    from research_agent.core.config import Settings
    from research_agent.rag.materials import DocumentIndex

    import psutil

    suffix = f"-first-{args.page_limit}-pages" if args.page_limit else ""
    out = ROOT / "output" / "library-evaluation" / f"cold-index-benchmark{suffix}"
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "document-cache"
    if list(cache.glob("*.json")):
        raise RuntimeError("冷启动测量目录已有缓存；请使用新的输出目录")
    spec = LIBRARY_ROUNDS[0]
    files = case_payload("B", spec)["materials"]
    settings = Settings(workspace=ROOT, output=out, team="cold_index_probe",
                        material_roots=[LIBRARY])
    process = psutil.Process(os.getpid())
    original_affinity = process.cpu_affinity() if hasattr(process, "cpu_affinity") else []
    one_core = bool(original_affinity)
    if one_core:
        process.cpu_affinity(original_affinity[:1])
    stop = threading.Event()
    samples = [process.memory_info().rss]

    def sample_memory() -> None:
        while not stop.wait(0.05):
            samples.append(process.memory_info().rss)

    sampler = threading.Thread(target=sample_memory, daemon=True)
    sampler.start()
    started = time.monotonic()
    try:
        index = DocumentIndex(settings, cache)
        chunks = index.build(files, page_limit=args.page_limit)
    finally:
        seconds = time.monotonic() - started
        stop.set()
        sampler.join()
        samples.append(process.memory_info().rss)
        if one_core:
            process.cpu_affinity(original_affinity)
    result = {"environment": "Windows host; one logical CPU affinity; no Docker memory limit",
              "pdfs": len(index.materials), "source_pages": 2311,
              "indexed_pages_with_chunks": len({(chunk.filename, chunk.page) for chunk in chunks}),
              "chunks": len(chunks),
              "page_limit_per_pdf": args.page_limit,
              "seconds": round(seconds, 2), "initial_rss_mib": round(samples[0] / 1048576, 2),
              "peak_rss_mib": round(max(samples) / 1048576, 2),
              "cache_files": len(list(cache.glob("*.json")))}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
