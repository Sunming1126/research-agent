"""C-only syntax recovery; never manufacture a claim, evidence ID or page."""
import re

from ..evidence import EvidenceError
from ..evidence.reporting import apply_report_patches


TOKEN = re.compile(r"[\[［]\s*(?:[Ee]\s*[:：]\s*)?(?:[Ee][Vv]\s*-\s*\d+)[^\]］\n]*[\]］]")
IDS = re.compile(r"[Ee][Vv]\s*-\s*(\d+)")
NUMERIC = re.compile(r"\[(\d{1,2}(?:\s*,\s*\d{1,2})*)(?:,\s*p\.\s*(\d+))?\]")


def normalize_response(value, evidence):
    if not isinstance(value, dict) or not isinstance(value.get('markdown'), str) or not value['markdown'].strip():
        raise EvidenceError('Survey response needs a complete nonempty markdown string')
    # References are rebuilt from the body, not trusted as a second source of IDs.
    body = value['markdown'].split('## 参考文献', 1)[0].strip()
    known = {row['evidence_id'] for row in evidence}
    def replace(match):
        text = match.group()[1:-1].strip()
        text = re.sub(r'^[Ee]\s*[:：]\s*', '', text)
        parts = re.split(r'[,，;；]', text)
        ids = []
        for part in parts:
            item = IDS.fullmatch(part.strip())
            if item is None:
                raise EvidenceError('malformed Survey evidence citation', passages=[match.group()])
            key = 'ev-' + item.group(1)
            if key not in known:
                raise EvidenceError('draft contains unknown evidence citation: ' + key, passages=[match.group()])
            if key not in ids:
                ids.append(key)
        return '[E:' + ','.join(ids) + ']'
    body = TOKEN.sub(replace, body)
    # Unclosed/unsupported explicit evidence syntax cannot slip through as prose.
    rest = re.sub(r'\[E:ev-\d+(?:,ev-\d+)*\]', '', body)
    if re.search(r'[\[［]\s*(?:[Ee]\s*[:：]|[Ee][Vv]\s*-)', rest):
        raise EvidenceError('malformed internal evidence citation')
    # The auxiliary list is not part of the official output. Rebuild only from
    # actual citations later; never guess an ID from a malformed list.
    return {'markdown': body, 'used_evidence_ids': []}


def cited_ids(body, evidence):
    pairs = {(int(ref), int(m[2]) if m[2] else None)
             for m in NUMERIC.finditer(body) for ref in re.split(r'\s*,\s*', m[1])}
    return [row['evidence_id'] for row in evidence
            if (row['ref'], row['page']) in pairs or (row['ref'], None) in pairs]


def repair_request(data, body):
    paragraphs = body.split('\n\n')
    targets = data['paragraphs']
    indexed = [{'paragraph_id': f'p{i+1}', 'text': p} for i, p in enumerate(paragraphs)
               if p.strip() and any(t in p for t in targets if t)]
    if not indexed:
        indexed = [{'paragraph_id': f'p{i+1}', 'text': p} for i, p in enumerate(paragraphs) if p.strip()]
    return {**data, 'paragraphs': indexed, 'full_document': body,
            'response_fields': {'patches': [{'paragraph_id': 'p1', 'replacement': '完整替换段落'}]},
            'repair_contract': '只修改给定段落ID，最多5段。保留已正确的事实和引用；不得猜测页码。'}


def apply_repair(body, value, data):
    if not isinstance(value, dict) or not isinstance(value.get('patches'), list):
        raise EvidenceError('Survey repair requires a patches array')
    allowed = {row['paragraph_id']: row['text'] for row in data['paragraphs']}
    patches, seen = [], set()
    for row in value['patches']:
        if not isinstance(row, dict) or not isinstance(row.get('replacement'), str):
            raise EvidenceError('invalid Survey repair patch')
        if 'paragraph_id' in row:
            key = row['paragraph_id']
            if not isinstance(key, str) or key not in allowed or key in seen:
                raise EvidenceError('Survey repair paragraph ID invalid or repeated')
            original = allowed[key]
            seen.add(key)
        else:
            original = row.get('original')
            if not isinstance(original, str) or not any(original in text for text in allowed.values()):
                raise EvidenceError('Survey repair original outside editable paragraphs')
        patches.append({'original': original, 'replacement': row['replacement']})
    # Stage replacements against the original, not against earlier replacements.
    # Duplicate/overlapping targets are rejected, never partially applied.
    spans = []
    for patch in patches:
        original = patch['original']
        if len(original) < 8 or body.count(original) != 1:
            raise EvidenceError('Survey repair target is not unique')
        start = body.index(original); end = start + len(original)
        if any(start < b and a < end for a, b in spans):
            raise EvidenceError('Survey repair targets overlap')
        spans.append((start, end))
    apply_report_patches(body, patches)  # preserve shared size/count/no-op checks
    result = body
    for (start, end), patch in sorted(zip(spans, patches), reverse=True):
        result = result[:start] + patch['replacement'] + result[end:]
    return result, patches


def generate(workflow, data, maximum, reserve, *, retry=False):
    """C generation reserves actual wall time for repair and final export."""
    import time
    from ..core.session import checkpoint, current_session
    llm = workflow.llm
    names = ('deadline', 'REQUEST_TIMEOUT_CAP', 'RETRY_ATTEMPTS', 'thinking_override')
    saved = {name: (name in vars(llm), getattr(llm, name, None)) for name in names}
    cap, tail = (55, 10) if retry else (90, 55)
    try:
        deadline = time.monotonic() + cap
        if saved['deadline'][1] is not None:
            deadline = min(deadline, saved['deadline'][1])
        session = current_session()
        if session:
            deadline = min(deadline, session.deadline - tail)
        if deadline <= time.monotonic():
            raise TimeoutError('insufficient Survey generation time')
        llm.deadline = deadline
        llm.REQUEST_TIMEOUT_CAP = cap
        llm.RETRY_ATTEMPTS = 1
        llm.thinking_override = 'disabled'
        checkpoint('survey_generate')
        return workflow._budgeted_model_step('report', data, maximum, reserve=reserve, minimum=1600)
    finally:
        for name, (present, value) in saved.items():
            if present:
                setattr(llm, name, value)
            else:
                vars(llm).pop(name, None)
