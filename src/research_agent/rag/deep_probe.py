"""Bounded query-guided page discovery within the current authorized PDF set.

This is a lexical discovery stage, not a relevance verdict. Only original page
text enters the normal evidence pipeline; no model call or global index is used.
"""
from __future__ import annotations

import hashlib
import math
import re
import time

from ..core.session import checkpoint, current_session
from ..core.utils import tokenize


def probe_pages(index, queries, initial_page_limit, *, max_pages=1200,
                max_seconds=12., model_reserve=95., pages_per_document=2):
    from .materials import normalize_extracted_text
    import fitz

    if initial_page_limit < 1 or max_pages < 0 or max_seconds < 0 or pages_per_document < 1:
        raise ValueError('invalid deep page probe limits')
    started = time.monotonic()
    terms = {t for q in queries if isinstance(q, str) for t in tokenize(q) if len(t) >= 2}
    trace = {'policy': 'bounded_authorized_page_probe_v1', 'scanned_pages': 0,
             'documents_opened': 0, 'authorized_documents': len(index.materials),
             'initial_page_limit': initial_page_limit, 'max_pages': max_pages,
             'max_seconds': max_seconds, 'selected_pages': {}, 'coverage_limited': False,
             'unread_pages_in_opened_documents': 0, 'scanned_pages_by_document': {},
             'sampling_limited_documents': [],
             'scope': 'lexical page discovery; not exhaustive semantic relevance judgment'}
    session = current_session()

    def exhausted():
        checkpoint('deep_page_probe')
        if session is not None and session.remaining() <= model_reserve:
            trace['stop_reason'] = 'model_time_reserved'
            return True
        if time.monotonic() - started >= max_seconds:
            trace['stop_reason'] = 'probe_time_limit'
            return True
        if trace['scanned_pages'] >= max_pages:
            trace['stop_reason'] = 'probe_page_limit'
            return True
        return False

    # Stable per-question ordering prevents a persistent filename-prefix bias
    # when a time limit stops the scan. It does not depend on benchmark labels.
    salt = '|'.join(sorted(terms))
    materials = sorted((m for m in index.materials if m.kind == 'pdf'),
                       key=lambda m: hashlib.sha256((salt + m.sha256).encode()).digest())
    quota = max(1, max_pages // max(1, len(materials)))
    found = []
    if not terms:
        trace.update(stop_reason='no_search_terms', coverage_limited=bool(materials))
    else:
        for material in materials:
            if exhausted():
                trace['coverage_limited'] = True
                break
            try:
                with fitz.open(material.path) as document:
                    trace['documents_opened'] += 1
                    if document.is_encrypted:
                        raise ValueError('encrypted PDF')
                    available = max(0, len(document) - initial_page_limit)
                    trace['unread_pages_in_opened_documents'] += available
                    trace['scanned_pages_by_document'][material.filename] = 0
                    count = min(available, quota)
                    if count < available:
                        trace['coverage_limited'] = True
                        trace['sampling_limited_documents'].append(material.filename)
                    # Sample the whole unread range rather than repeatedly
                    # examining the pages immediately after the initial prefix.
                    numbers = ([initial_page_limit + i for i in range(available)] if count == available else
                               sorted({initial_page_limit + min(available - 1, math.floor((i + .5) * available / count))
                                       for i in range(count)}))
                    best = []
                    references = False
                    for number in numbers:
                        if exhausted():
                            trace['coverage_limited'] = True
                            break
                        blocks = document[number].get_text('blocks', sort=True)
                        text = normalize_extracted_text('\n\n'.join(
                            b[4].strip() for b in sorted(blocks, key=lambda b: (b[1], b[0]))
                            if len(b) > 6 and b[6] == 0))
                        trace['scanned_pages'] += 1
                        trace['scanned_pages_by_document'][material.filename] += 1
                        for line in text.splitlines():
                            if re.fullmatch(r'\s*(?:\d+\.?\s+)?(?:references|bibliography|参考文献)\s*', line, re.I):
                                references = True
                            elif re.match(r'\s*(?:appendix|appendices|supplementary\s+(?:material|information)|附录|补充材料)\b', line, re.I):
                                references = False
                        overlap = terms.intersection(tokenize(text))
                        if overlap and not references:
                            best.append((len(overlap), number + 1, text))
                            # Keep bounded text even for very long documents.
                            best.sort(key=lambda row: (-row[0], row[1]))
                            del best[pages_per_document:]
                    if best:
                        pages = [(page, text, '') for _, page, text in sorted(best, key=lambda row: row[1])]
                        found.extend(index.chunker.chunk(material, pages))
                        trace['selected_pages'][material.filename] = [p[0] for p in pages]
            except TimeoutError:
                raise
            except (OSError, ValueError, RuntimeError) as exc:
                # Timeouts/cancellation deliberately propagate. Broken PDFs do
                # not prevent scanning other PDFs already authorized for B.
                index.errors.append({'filename': material.filename, 'stage': 'deep_page_probe',
                                     'error_type': type(exc).__name__})
                trace['coverage_limited'] = True
    trace['elapsed_seconds'] = round(time.monotonic() - started, 3)
    trace['documents_unopened'] = len(materials) - trace['documents_opened']
    return found, trace
