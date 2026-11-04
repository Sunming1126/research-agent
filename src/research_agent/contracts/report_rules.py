"""Current C/E hard limits, isolated from report generation and factual checks."""
import re


BODY_CHARACTER_LIMITS = {'Survey': 5000, 'Idea': 4500}
CITATION_MARKER = re.compile(r'\[(\d{1,2}(?:\s*,\s*\d{1,2})*)(?:,\s*p\.\s*\d+)?\]')


def body_character_limit(function):
    return BODY_CHARACTER_LIMITS[function]


def counted_report_body(markdown):
    """Exclude references and ATX heading lines; keep code and actual prose.

    Blank separation around the body is formatting, not additional body text.
    Hashes inside fenced code and hashes without heading syntax still count.
    """
    before_references = markdown.split('## 参考文献', 1)[0]
    kept = []
    fence = None
    for line in before_references.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if fence is not None:
            kept.append(line)
            if (marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1]
                    and not marker[2].strip()):
                fence = None
        elif marker:
            kept.append(line)
            fence = (marker[1][0], len(marker[1]))
        elif not re.match(r'^ {0,3}#{1,6}(?:[ \t]+|$)', line):
            kept.append(line)
    return ''.join(kept).strip()


def citation_marker_count(markdown):
    """One bracketed citation group is one instance, regardless of ref count."""
    return len(CITATION_MARKER.findall(markdown.split('## 参考文献', 1)[0]))
