"""One citation grammar shared by output checks, audits and repair routing."""
import re

# Three-digit crystal orientations such as [111] remain outside this grammar.
# A page suffix applies to each reference in a group, as in [1,2, p.4].
CITATION_PATTERN = re.compile(r'\[(\d{1,2}(?:\s*,\s*\d{1,2})*)(?:,\s*p\.\s*(\d+))?\]')


def citation_pairs(markdown):
    return {(int(ref), int(match[2]) if match[2] else None)
            for match in CITATION_PATTERN.finditer(markdown)
            for ref in re.split(r'\s*,\s*', match[1])}
