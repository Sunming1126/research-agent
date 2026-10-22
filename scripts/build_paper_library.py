"""从 arXiv 官方元数据接口建立跨学科的 100 篇本地 PDF 论文库。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "paper_library"
PDFS = LIBRARY / "pdfs"
MANIFEST = LIBRARY / "manifest.json"
FAILURES = LIBRARY / "download-failures.json"
LOCAL_SEEDS = ROOT / "local-workspace" / "papers"
API = "https://export.arxiv.org/api/query"
USER_AGENT = "research-agent-local-evaluation/0.1"
DATE_FILTER = "submittedDate:[202301010000 TO 202512312359]"

# 7 篇上一轮已从 arXiv 下载的原始论文，纳入百篇库后保留原文件名。
SEEDS = [
    ("2004.04906", "2004.04906-DPR.pdf", "cs.IR"),
    ("2005.11401", "2005.11401-RAG.pdf", "cs.CL"),
    ("2007.01282", "2007.01282-FiD.pdf", "cs.CL"),
    ("2104.08663", "2104.08663-BEIR.pdf", "cs.IR"),
    ("2112.04426", "2112.04426-RETRO.pdf", "cs.CL"),
    ("2203.02155", "2203.02155-InstructGPT.pdf", "cs.CL"),
    ("2310.11511", "2310.11511-Self-RAG.pdf", "cs.CL"),
]

# 新增 93 篇，覆盖 10 个不同研究方向；各类配额相加恰为 93。
CATEGORIES = {
    "cs.IR": 10,
    "cs.CL": 10,
    "cs.CV": 10,
    "stat.ML": 9,
    "math.OC": 9,
    "q-bio.MN": 9,
    "astro-ph.GA": 9,
    "cond-mat.mtrl-sci": 9,
    "econ.GN": 9,
    "physics.optics": 9,
}


def request_bytes(url: str, *, max_bytes: int = 15_000_000) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=50) as response:
        declared = int(response.headers.get("Content-Length") or 0)
        if declared > max_bytes:
            raise ValueError("PDF exceeds local test size cap")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("download exceeded local test size cap")
    return data


def query_arxiv(*, category: str | None = None, ids: list[str] | None = None,
                start: int = 0, limit: int = 50) -> list[dict]:
    """单连接读取 Atom；调用者保证相邻 API 请求至少间隔 3.2 秒。"""
    params = {"start": start, "max_results": limit}
    if category:
        params.update(search_query=f"cat:{category} AND {DATE_FILTER}",
                      sortBy="submittedDate", sortOrder="descending")
    else:
        params["id_list"] = ",".join(ids or [])
    data = request_bytes(API + "?" + urllib.parse.urlencode(params), max_bytes=2_000_000)
    atom = "{http://www.w3.org/2005/Atom}"
    arxiv = "{http://arxiv.org/schemas/atom}"
    root = ET.fromstring(data)
    result = []
    for entry in root.findall(atom + "entry"):
        url = (entry.findtext(atom + "id") or "").strip()
        match = re.search(r"/abs/(\d{4}\.\d{4,5})(?:v\d+)?$", url)
        if not match:
            continue
        title = " ".join((entry.findtext(atom + "title") or "").split())
        abstract = " ".join((entry.findtext(atom + "summary") or "").split())
        primary = entry.find(arxiv + "primary_category")
        result.append({"arxiv_id": match.group(1), "title": title, "abstract": abstract,
                       "primary_category": primary.attrib.get("term") if primary is not None else "",
                       "published": entry.findtext(atom + "published") or "",
                       "updated": entry.findtext(atom + "updated") or "",
                       "abstract_url": f"https://arxiv.org/abs/{match.group(1)}",
                       "pdf_url": f"https://arxiv.org/pdf/{match.group(1)}"})
    return result


def inspect_pdf(path: Path) -> tuple[int, int, str]:
    """拒绝伪 PDF、加密、无正文文本或异常超长文件。"""
    if path.read_bytes()[:5] != b"%PDF-":
        raise ValueError("missing PDF signature")
    import fitz
    with fitz.open(path) as document:
        pages = len(document)
        if document.is_encrypted or not 1 <= pages <= 80:
            raise ValueError("encrypted or oversized page count")
        sample = " ".join(document[index].get_text() for index in range(min(3, pages)))
        if len(re.sub(r"\s+", "", sample)) < 350:
            raise ValueError("insufficient extractable text")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return pages, path.stat().st_size, digest


def save_manifest(rows: list[dict]) -> None:
    payload = {"name": "research-agent-local-paper-library", "version": 1,
               "source_api": "https://info.arxiv.org/help/api/user-manual.html",
               "selection": "seven_prior_public_papers_plus_2023_2025_category_samples",
               "target_count": 100, "count": len(rows), "papers": sorted(rows, key=lambda row: row["filename"])}
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(*, delay: float = 3.2) -> None:
    LIBRARY.mkdir(exist_ok=True)
    PDFS.mkdir(exist_ok=True)
    rows = json.loads(MANIFEST.read_text(encoding="utf-8"))["papers"] if MANIFEST.is_file() else []
    by_id = {row["arxiv_id"]: row for row in rows}
    failures = json.loads(FAILURES.read_text(encoding="utf-8")) if FAILURES.is_file() else []
    last_api = 0.0

    def metadata(**kwargs):
        nonlocal last_api
        wait = delay - (time.monotonic() - last_api)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            try:
                result = query_arxiv(**kwargs)
                last_api = time.monotonic()
                return result
            except Exception:
                last_api = time.monotonic()
                if attempt == 2:
                    raise
                time.sleep(delay * (attempt + 1))

    seed_meta = {row["arxiv_id"]: row for row in metadata(ids=[row[0] for row in SEEDS])}
    for arxiv_id, filename, category in SEEDS:
        if arxiv_id in by_id:
            continue
        source = LOCAL_SEEDS / filename
        if not source.is_file():
            raise FileNotFoundError(f"missing previously downloaded arXiv PDF: {filename}")
        target = PDFS / filename
        shutil.copyfile(source, target)
        pages, size, digest = inspect_pdf(target)
        row = {**seed_meta.get(arxiv_id, {"arxiv_id": arxiv_id,
                                         "abstract_url": f"https://arxiv.org/abs/{arxiv_id}",
                                         "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}"}),
               "selected_category": category, "filename": filename,
               "pages": pages, "bytes": size, "sha256": digest, "origin": "prior_verified_arxiv_download"}
        rows.append(row)
        by_id[arxiv_id] = row
        save_manifest(rows)
    print(f"SEEDS {len(rows)}", flush=True)

    for category, quota in CATEGORIES.items():
        have = sum(row.get("selected_category") == category and row.get("origin") == "category_api"
                   for row in rows)
        offset = 0
        while have < quota and offset < 250:
            candidates = metadata(category=category, start=offset, limit=50)
            if not candidates:
                break
            offset += len(candidates)
            for paper in candidates:
                if have >= quota:
                    break
                arxiv_id = paper["arxiv_id"]
                if arxiv_id in by_id or paper["primary_category"] != category:
                    continue
                filename = f"{arxiv_id}.pdf"
                target = PDFS / filename
                try:
                    if not target.is_file():
                        data = request_bytes(paper["pdf_url"])
                        target.write_bytes(data)
                    pages, size, digest = inspect_pdf(target)
                    row = {**paper, "selected_category": category, "filename": filename,
                           "pages": pages, "bytes": size, "sha256": digest,
                           "origin": "category_api"}
                    rows.append(row)
                    by_id[arxiv_id] = row
                    have += 1
                    save_manifest(rows)
                    print(f"OK {len(rows):03d}/100 {category} {filename} pages={pages} bytes={size}", flush=True)
                except Exception as exc:
                    target.unlink(missing_ok=True)
                    failures.append({"category": category, "arxiv_id": arxiv_id,
                                     "error_type": type(exc).__name__})
                    FAILURES.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"SKIP {category} {arxiv_id} {type(exc).__name__}", flush=True)
                time.sleep(1.0)
        if have != quota:
            raise RuntimeError(f"category {category} incomplete: {have}/{quota}")
    if len(rows) != 100 or len({row["arxiv_id"] for row in rows}) != 100:
        raise RuntimeError("library is not exactly 100 unique papers")
    print("COMPLETE 100 unique verified PDFs", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-delay", type=float, default=3.2)
    args = parser.parse_args()
    if args.api_delay < 3.0:
        parser.error("arXiv API requires at least a three-second interval")
    build(delay=args.api_delay)


if __name__ == "__main__":
    main()
