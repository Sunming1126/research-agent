"""E 通过稳定段落编号寻址修订；正文仍完全来自模型回复。"""
from ..evidence import EvidenceError


def indexed_paragraphs(paragraphs):
    return [{'paragraph_id': f'p{i+1}', 'text': text} for i,text in enumerate(paragraphs)]


def resolve_patches(value, paragraphs):
    known={row['paragraph_id']:row['text'] for row in indexed_paragraphs(paragraphs)}
    if not isinstance(value,list) or not 1<=len(value)<=3:
        raise EvidenceError('Idea repair requires one to three indexed paragraphs')
    patches=[];seen=set()
    for row in value:
        key=row.get('paragraph_id') if isinstance(row,dict) else None
        if not isinstance(key,str) or key not in known or key in seen or not isinstance(row.get('replacement'),str):
            raise EvidenceError('Idea repair paragraph ID invalid or repeated')
        patches.append({'original':known[key],'replacement':row['replacement']});seen.add(key)
    return patches
