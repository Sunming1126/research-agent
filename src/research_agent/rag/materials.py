from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from ..core.config import Settings
from ..core.models import Chunk, MaterialRef
from ..core.utils import confined, sha256_file, sha256_bytes, read_json, write_json
from ..integrations.instrumentation import tool
from ..core.session import checkpoint, current_session, remaining_timeout

EXTENSIONS = {".pdf", ".md", ".txt", ".json", ".csv", ".tsv"}


def normalize_extracted_text(text: str) -> str:
    """把 PDF 提取器返回的孤立代理码位替换为可编码字符。"""
    if not any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        return text
    return "".join("\ufffd" if 0xD800 <= ord(char) <= 0xDFFF else char for char in text)


class MaterialError(ValueError):
    pass


class MaterialResolver:
    """将题目材料描述映射到专用材料目录内的唯一实际文件。"""
    def __init__(self, settings: Settings):
        self.settings = settings

    def _path(self, item: str) -> Path:
        if not item or "\\" in item or ":" in item or item.startswith("/"):
            raise MaterialError("materials must use relative paths or configured aliases")
        matches = []
        for root in self.settings.material_roots:
            # 明确路径可以相对于工作区或已配置的材料根目录。
            options = [confined(root, item)]
            if item == root.name:
                options.append(root)
            workspace_path = confined(self.settings.workspace, item)
            if workspace_path == root or root in workspace_path.parents:
                options.append(workspace_path)
            for candidate in options:
                if candidate.exists() and candidate not in matches:
                    matches.append(candidate)
            if "/" not in item:
                # 只在专用材料目录内按文件名查找，不遍历整个工作区或 data。
                for candidate in root.rglob(item):
                    checked = confined(root, candidate.relative_to(root).as_posix())
                    if checked not in matches:
                        matches.append(checked)
        if not matches:
            raise MaterialError(f"authorized material not found: {item}")
        if len(matches) != 1:
            raise MaterialError(f"ambiguous material filename: {item}")
        return matches[0]

    @tool(name="resolve_authorized_materials")
    def resolve(self, materials: list[str]) -> list[MaterialRef]:
        selected = {}
        for description in materials:
            checkpoint("material_resolution")
            items = self.settings.material_aliases.get(description, [description])
            if not isinstance(items, list) or not items:
                raise MaterialError("material alias must map to a nonempty list")
            for item in items:
                target = self._path(item)
                candidates = [target] if target.is_file() else list(target.rglob("*"))
                for path in candidates:
                    if path.is_file() and path.suffix.lower() in EXTENSIONS:
                        root = next(root for root in self.settings.material_roots if root in path.parents)
                        path = confined(root, path.relative_to(root).as_posix())
                        selected[path] = MaterialRef(path, path.name, path.suffix.lower()[1:], sha256_file(path))
        refs = sorted(selected.values(), key=lambda ref: ref.filename)
        names = [ref.filename.casefold() for ref in refs]
        if len(names) != len(set(names)):
            raise MaterialError("non-unique case-insensitive filenames in authorized materials")
        return refs


class DocumentParser:
    """逐页读取文档，保留用于引用的 PDF 物理页码。"""
    VERSION = "page-block-tables-v7-appendix-short-pages"

    def __init__(self, ocr=False, work: Path | None = None):
        self.ocr = ocr
        self.work = work

    @tool(name="read_document_pages")
    def pages(self, material: MaterialRef, page_limit: int | None = None) -> list[tuple[int | None, str, str]]:
        if material.kind != "pdf":
            text = material.path.read_text(encoding="utf-8-sig")
            if material.kind == "json":
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            return [(None, text, material.filename)]
        with material.path.open("rb") as stream:
            signature = stream.read(5)
        if signature != b"%PDF-":
            raise MaterialError(f"invalid PDF signature: {material.filename}")
        try:
            import fitz
            document = fitz.open(material.path)
        except Exception:
            return self._pypdf(material, page_limit)
        pages = []
        with document as doc:
            if doc.is_encrypted:
                raise MaterialError("encrypted PDF")
            for page in doc:
                checkpoint("document_parsing")
                if page_limit is not None and page.number >= page_limit:
                    break
                # 按页面位置排列文本块，尽量保留分栏和表格的阅读顺序。
                blocks = page.get_text("blocks", sort=True)
                tables = []
                try:
                    for table in page.find_tables().tables:
                        cells = table.extract()
                        rows = ["| " + " | ".join((cell or "").replace("\n", " ") for cell in row) + " |" for row in cells]
                        if rows:
                            tables.append((table.bbox, "\n".join(rows)))
                except (AttributeError, ValueError):
                    tables = []
                pieces = [(b[1], b[0], b[4].strip()) for b in blocks if len(b) > 6 and b[6] == 0
                          and not any(box[0] <= b[0] and box[1] <= b[1] and b[2] <= box[2] and b[3] <= box[3] for box, _ in tables)]
                pieces += [(box[1], box[0], text) for box, text in tables]
                text = "\n\n".join(item[2] for item in sorted(pieces))
                if len(re.sub(r"\s", "", text)) < 20:
                    if self.ocr and self.work and shutil.which("tesseract"):
                        self.work.mkdir(parents=True, exist_ok=True)
                        image = self.work / f"ocr-{material.sha256[:12]}-{page.number+1}.png"
                        page.get_pixmap(dpi=180).save(image)
                        run = subprocess.run(["tesseract", str(image), "stdout", "-l", "eng+chi_sim"],
                                             capture_output=True, encoding="utf-8", timeout=remaining_timeout(45), check=False)
                        if run.returncode:
                            raise MaterialError(f"OCR failed: {material.filename}, page {page.number+1}")
                        text = run.stdout
                    elif page.get_images():
                        # 混合文本 PDF 的单独扫描页不能使整篇失败；该页不给事实证据。
                        text = ""
                pages.append((page.number + 1, normalize_extracted_text(text), ""))
        if not any(re.search(r"\w", text) for _, text, _ in pages):
            raise MaterialError(f"PDF has no extractable text: {material.filename}")
        # 只去除多页重复的页首和页尾行，避免误删正文中的常见句子。
        counts = Counter()
        for _, text, _ in pages:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            counts.update(set(lines[:2] + lines[-2:]))
        repeated = {s for s, count in counts.items() if count >= max(3, len(pages) * .6)}
        return [(p, text if len([line for line in text.splitlines() if line.strip()]) <= 4 else
                 "\n".join(line for line in text.splitlines() if line.strip() not in repeated), h)
                for p, text, h in pages]

    def _pypdf(self, material, page_limit=None):
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(material.path))
            if reader.is_encrypted:
                raise MaterialError("encrypted PDF")
            pages = [(i+1, normalize_extracted_text(page.extract_text() or ""), "")
                     for i, page in enumerate(reader.pages) if page_limit is None or i < page_limit]
            if not any(len(re.sub(r"\s", "", text)) >= 20 for _, text, _ in pages):
                raise MaterialError("fallback parser found no readable text; OCR-capable parser required")
            return pages
        except Exception as exc:
            raise MaterialError(f"unable to parse PDF: {material.filename} ({type(exc).__name__})") from None


class Chunker:
    """按页和内容块切分；每段保留来源、偏移与内容哈希。"""
    def __init__(self, max_chars=850, overlap=110):
        self.max_chars, self.overlap = max_chars, overlap

    @tool(name="chunk_pages")
    def chunk(self, material: MaterialRef, pages) -> list[Chunk]:
        result = []
        heading = material.filename
        reference_section = False
        for page, raw, _ in pages:
            checkpoint("document_chunking")
            raw = raw.replace("\r", "").replace("\u00a0", " ")
            for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", raw):
                block = match.group().strip()
                if not block:
                    continue
                first = block.splitlines()[0]
                if re.match(r"^(?:#{1,6}\s*)?(?:appendix|appendices|supplementary\s+(?:material|information)|附录|补充材料)\b", first, re.I):
                    reference_section = False
                    heading = first.lstrip("# ")[:160]
                if re.match(r"^(#{1,6} |\d+(?:\.\d+)*\s+|abstract\b|conclusion\b|references\b|摘要|参考文献)", first, re.I):
                    heading = first.lstrip("# ")[:160]
                if re.fullmatch(r"(?:\d+\.?\s+)?(?:references|bibliography|参考文献)", first, re.I):
                    reference_section = True
                kind = "references" if reference_section else "table" if block.count("|") >= 4 else "caption" if re.match(r"^(fig(?:ure)?\.?|table|图|表)\s*\d", first, re.I) else "text"
                # 中英文采用不同字符上限，控制后续模型输入长度。
                chinese = len(re.findall(r"[\u4e00-\u9fff]", block))
                limit = self.max_chars if chinese > len(block) * .2 else 1800
                start = 0
                while start < len(block):
                    end = min(start + limit, len(block))
                    if kind in {"table", "caption"} and len(block) <= 2400:
                        end = len(block)
                    elif end < len(block):
                        boundary = max(block.rfind("\n", start+limit//2, end),
                                       block.rfind("。", start+limit//2, end),
                                       block.rfind(". ", start+limit//2, end))
                        if boundary > start:
                            end = boundary + 1
                    text = block[start:end].strip()
                    digest = sha256_bytes(f"{material.sha256}|{page}|{match.start()+start}|{text}".encode())
                    result.append(Chunk(digest[:20], material.filename, page, heading, text, kind, digest,
                                        match.start()+start, match.start()+end, material.sha256))
                    if end == len(block):
                        break
                    start = max(start+1, end-self.overlap)
        return result


class DocumentIndex:
    """仅为本题授权材料建索引，并按文件哈希及切分版本隔离缓存。"""
    def __init__(self, settings: Settings, cache: Path):
        self.resolver = MaterialResolver(settings)
        self.parser = DocumentParser(settings.ocr, cache / "ocr")
        self.chunker = Chunker()
        self.cache = cache
        self.materials = []
        self.chunks = []

    @tool(name="build_authorized_index")
    def build(self, descriptions: list[str], page_limit: int | None = None, *, resolved=None) -> list[Chunk]:
        if page_limit is not None and page_limit < 1:
            raise ValueError("page_limit must be positive")
        self.materials = resolved if resolved is not None else self.resolver.resolve(descriptions)
        self.chunks = []
        for material in self.materials:
            checkpoint("document_index")
            identity = f"{material.filename}|{material.sha256}|{self.parser.VERSION}|850|110|{self.parser.ocr}"
            if page_limit is not None:
                identity += f"|first-{page_limit}-pages"
            key = sha256_bytes(identity.encode())
            cache_path = confined(self.cache, key + ".json")
            if cache_path.is_file():
                rows = read_json(cache_path)["chunks"]
                chunks = [Chunk(**row) for row in rows]
                if any(c.document_sha256 != material.sha256 or c.filename != material.filename for c in chunks):
                    raise MaterialError("cache identity mismatch")
                if any(c.sha256 != sha256_bytes(f"{material.sha256}|{c.page}|{c.start}|{c.text}".encode()) for c in chunks):
                    raise MaterialError("cache content hash mismatch")
            else:
                # B 类候选库先读取前页；缓存键含页数，避免与全文证据混用。
                chunks = self.chunker.chunk(material, self.parser.pages(material, page_limit))
                write_json(cache_path, {"chunks": [asdict(c) for c in chunks]})
            self.chunks.extend(chunks)
        return self.chunks

    def expand_documents(self, filenames):
        """仅深入当前题已经授权的候选论文，补齐前页索引以外的正文。"""
        selected = [m for m in self.materials if m.filename in set(filenames)]
        original_materials, original_chunks = self.materials, self.chunks
        try:
            deeper = []
            for material in selected:
                session = current_session()
                if session and session.remaining() < 55:
                    break
                deeper.extend(self.build([], resolved=[material]))
            return deeper
        finally:
            # 候选补读不能缩小本题最终核验的授权文件集合。
            self.materials, self.chunks = original_materials, original_chunks

