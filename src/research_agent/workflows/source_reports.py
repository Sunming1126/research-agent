"""用授权连续原文直接支持写作，不把程序摘录冒充模型核验后的事实。"""
from collections import defaultdict

from ..core.models import Evidence
from ..core.utils import source_quote_matches
from ..evidence import EvidenceError
from ..evidence.reporting import focused_excerpt, fallback_evidence_chunks


def source_ledger(case, selected, all_chunks):
    groups = defaultdict(list)
    for chunk in selected:
        if chunk.kind != 'references' and len(chunk.text.strip()) >= 40:
            groups[chunk.filename].append(chunk)
    names = sorted({c.filename for c in all_chunks if c.filename.lower().endswith('.pdf')})
    ledger = []
    for name in names:
        candidates = groups[name] or fallback_evidence_chunks(all_chunks, name)
        # 保留召回顺序和不同页，单篇不占满请求；每个片段都来自真实PDF。
        chosen, seen = [], set()
        for chunk in candidates:
            key = (chunk.page, chunk.text)
            if key not in seen:
                chosen.append(chunk)
                seen.add(key)
        # 开头是题意召回，中段是本篇方法，末尾是实验/局限补检。
        # 不能简单取前几条，把数据、结果和局限全部挤掉。
        diverse = list({c.chunk_id: c for c in [*chosen[:2], *chosen[-4:], *chosen[2:-4]]}.values())
        maximum = 8 if case.function == 'Survey' else 10
        for chunk in diverse[:maximum]:
            quote = focused_excerpt(chunk, case.input, 500 if case.function == 'Survey' else 650)
            if not source_quote_matches(quote, chunk.text):
                raise EvidenceError('source excerpt is not continuous authorized text')
            ledger.append(Evidence(f'ev-{len(ledger)+1}', quote, name, chunk.page,
                                   chunk.chunk_id, quote, kind='source_context',
                                   role='source_context', verification='verbatim_only'))
    if set(names) != {e.filename for e in ledger}:
        raise EvidenceError('authorized paper has no readable source context')
    return ledger
