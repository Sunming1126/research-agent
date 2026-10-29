"""Offline one-factor benchmark for deep PDF probe text extraction.

Compares the current block-based extraction with PyMuPDF sorted text extraction
on the same frozen pages. It makes no model calls and sends no network traffic.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "镜像测试"
RUNS = HERE / "runs"
LIB = ROOT / "科研资料/2026-10-29-扩充论文库/pdfs"
CORPUS = HERE / "b-stability-20261029/corpus.json"
PINNED = HERE / "runtime/windows-python312"
SOURCE = HERE / "审核/2026-10-29-b-stability-current-43f8189/source"
AUTH = "20261029-b-stability-user-approved"


def main() -> None:
    if os.environ.get("ANIC_OFFLINE_TEST_AUTHORIZATION") != AUTH:
        raise SystemExit("missing explicit offline-test authorization")
    if os.environ.get("ANIC_NATIVE_ORCHESTRATOR_SESSION_ID"):
        raise SystemExit("offline benchmark must not claim native Harness execution")
    sys.path[:0] = [str(SOURCE / "src"), str(PINNED), str(HERE)]
    import offline_network_guard_20261029  # noqa: F401
    import fitz
    from research_agent.rag.materials import normalize_extracted_text

    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    pages: list[tuple[str, Path, list[int]]] = []
    page_count = 0
    for row in corpus["papers"]:
        path = LIB / row["filename"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise SystemExit(f"frozen PDF hash mismatch: {row['filename']}")
        with fitz.open(path) as doc:
            available = max(0, len(doc) - 4)
            count = min(12, available)
            selected = sorted({4 + min(available - 1, int((i + .5) * available / count))
                               for i in range(count)}) if count else []
        pages.append((row["filename"], path, selected))
        page_count += len(selected)

    def blocks_text(page) -> str:
        blocks = page.get_text("blocks", sort=True)
        text = "\n\n".join(
            b[4].strip() for b in sorted(blocks, key=lambda b: (b[1], b[0]))
            if len(b) > 6 and b[6] == 0
        )
        return normalize_extracted_text(text)

    def sorted_text(page) -> str:
        return normalize_extracted_text(page.get_text("text", sort=True))

    run_name = f"b-probe-text-extraction-20261029-{time.strftime('%H%M%S')}"
    out = RUNS / run_name
    out.mkdir(parents=True, exist_ok=False)
    reference: list[str] | None = None
    schedule = ["blocks", "text", "text", "blocks", "blocks", "text"]
    runs = []
    for sequence, method in enumerate(schedule):
        extract = blocks_text if method == "blocks" else sorted_text
        extracted: list[str] = []
        started_wall, started_cpu = time.perf_counter(), time.process_time()
        for _, path, numbers in pages:
            with fitz.open(path) as doc:
                for number in numbers:
                    extracted.append(extract(doc[number]))
        elapsed_wall = time.perf_counter() - started_wall
        elapsed_cpu = time.process_time() - started_cpu
        if reference is None:
            reference = extracted
        mismatches = [i for i, value in enumerate(extracted) if value != reference[i]]
        runs.append({
            "sequence": sequence, "method": method,
            "wall_seconds": round(elapsed_wall, 4),
            "cpu_seconds": round(elapsed_cpu, 4),
            "pages": len(extracted), "exact_page_text_matches_baseline": len(mismatches) == 0,
            "mismatch_count": len(mismatches),
            "mismatch_page_indexes": mismatches[:20],
        })
        if len(extracted) != page_count:
            raise SystemExit("unexpected page count")

    result = {
        "stage": "offline_text_extraction_ablation",
        "authorization": "explicit-user-authorized-offline-no-model",
        "native_harness_execution": False,
        "target_model_calls": 0, "target_model_tokens": 0,
        "factor": "PyMuPDF page text extraction API only",
        "baseline": "get_text(blocks, sort=True) plus production text-block filtering and coordinate sort",
        "candidate": "get_text(text, sort=True)",
        "frozen_corpus_documents": len(pages), "sampled_pages": page_count,
        "sampling": "up to 12 evenly spaced pages per PDF after the first four pages; same pages for all runs",
        "pymupdf_version": fitz.VersionBind,
        "order": schedule,
        "runs": runs,
        "cache_limit": "separate file opens per pass; OS page cache is uncontrolled; alternating order reduces but does not remove cache bias",
        "decision_rule": "candidate eligible only if every normalized page is byte-for-byte identical and repeated median CPU and wall time improve",
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run": str(out), "sampled_pages": page_count, "runs": runs}, ensure_ascii=False))


if __name__ == "__main__":
    main()
