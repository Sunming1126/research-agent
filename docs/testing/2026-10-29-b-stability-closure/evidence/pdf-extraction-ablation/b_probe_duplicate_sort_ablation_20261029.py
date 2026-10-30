"""Offline test of removing the redundant PyMuPDF block sort."""
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
    pages = []
    for row in corpus["papers"]:
        path = LIB / row["filename"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise SystemExit(f"frozen PDF hash mismatch: {row['filename']}")
        with fitz.open(path) as doc:
            available = max(0, len(doc) - 4)
            count = min(12, available)
            numbers = sorted({4 + min(available - 1, int((i + .5) * available / count))
                              for i in range(count)}) if count else []
        pages.append((path, numbers))

    def extract(path: Path, numbers: list[int], sort_in_pymupdf: bool) -> list[str]:
        values = []
        with fitz.open(path) as doc:
            for number in numbers:
                page = doc[number]
                blocks = page.get_text("blocks", sort=sort_in_pymupdf)
                if sort_in_pymupdf:
                    blocks = sorted(blocks, key=lambda b: (b[1], b[0]))
                else:
                    blocks.sort(key=lambda b: (b[1], b[0]))
                text = "\n\n".join(b[4].strip() for b in blocks if len(b) > 6 and b[6] == 0)
                values.append(normalize_extracted_text(text))
        return values

    out = RUNS / f"b-probe-duplicate-sort-20261029-{time.strftime('%H%M%S')}"
    out.mkdir(parents=True, exist_ok=False)
    schedule = [True, False, False, True, True, False]
    baseline = None
    results = []
    for sequence, sort_in_pymupdf in enumerate(schedule):
        started_wall, started_cpu = time.perf_counter(), time.process_time()
        extracted = [text for path, numbers in pages
                     for text in extract(path, numbers, sort_in_pymupdf)]
        wall, cpu = time.perf_counter() - started_wall, time.process_time() - started_cpu
        if baseline is None:
            baseline = extracted
        mismatch = [i for i, value in enumerate(extracted) if value != baseline[i]]
        results.append({
            "sequence": sequence,
            "method": "pymupdf_sorted_plus_python_sort" if sort_in_pymupdf else "pymupdf_unsorted_plus_python_sort",
            "wall_seconds": round(wall, 4), "cpu_seconds": round(cpu, 4),
            "pages": len(extracted), "exact_text_match": not mismatch,
            "mismatch_count": len(mismatch), "mismatch_page_indexes": mismatch[:20],
        })

    result = {
        "stage": "offline_duplicate_sort_ablation",
        "authorization": "explicit-user-authorized-offline-no-model",
        "native_harness_execution": False,
        "target_model_calls": 0, "target_model_tokens": 0,
        "factor": "PyMuPDF sort argument only; application coordinate sort remains enabled",
        "documents": len(pages), "sampled_pages": sum(len(n) for _, n in pages),
        "sampling": "up to 12 evenly spaced pages per PDF after the first four pages",
        "pymupdf_version": fitz.VersionBind,
        "schedule": schedule, "runs": results,
        "cache_limit": "OS page cache uncontrolled; alternating order reduces but does not remove cache bias",
        "decision_rule": "candidate eligible only if every page is byte-for-byte identical and repeated median CPU and wall time improve",
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run": str(out), "pages": result["sampled_pages"], "runs": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
