"""B 条件表、可核验判断和明确数量约束。"""
import re
import unicodedata
from ..integrations.llm import ModelError


def text_key(text):
    return re.sub(r'\s+', '', text).casefold()


def task_text(task):
    return '\n'.join(x for x in [task.get('title',''),task.get('input',''),*task.get('constraints',[]),task.get('expected_output','')] if isinstance(x,str))


def make_plan(value, task):
    original=task_text(task)
    def sourced(row):
        quote=row.get('source_quote')
        return isinstance(quote,str) and len(quote.strip())>=2 and text_key(quote) in text_key(original)
    if not isinstance(value,dict):raise ModelError('invalid B condition plan')
    targets=value.get('targets');criteria=value.get('criteria')
    if not isinstance(targets,list) or not 1<=len(targets)<=6 or not isinstance(criteria,list) or not 1<=len(criteria)<=12:
        raise ModelError('B plan needs bounded targets and criteria')
    ids=set();clean_targets=[];clean_criteria=[]
    for row in targets:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id'] or row['id']=='common' or row['id'] in ids or not sourced(row):
            raise ModelError('invalid B target/source')
        if not isinstance(row.get('text'),str) or not row['text'].strip():raise ModelError('empty B target')
        ids.add(row['id']);clean_targets.append({k:row[k] for k in ('id','text','source_quote')})
        clean_targets[-1]['query']=row.get('query') if isinstance(row.get('query'),str) else row['text']
    seen=set()
    for row in criteria:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id'] or row['id'] in seen or not sourced(row):
            raise ModelError('invalid B criterion/source')
        if row.get('kind') not in ('must','exclude') or (not isinstance(row.get('scope'),str) or row['scope'] not in ids|{'common'}) or not isinstance(row.get('text'),str) or not row['text'].strip():
            raise ModelError('invalid B criterion scope/kind')
        seen.add(row['id']);clean_criteria.append({k:row[k] for k in ('id','text','kind','scope','source_quote')})
        clean_criteria[-1]['query']=row.get('query') if isinstance(row.get('query'),str) else row['text']
    if len(clean_targets)>1:
        # 只在条件原句恰好就是某个独立目标的完整原句时纠正作用域，不能猜测共同条件。
        for criterion in clean_criteria:
            owners=[t['id'] for t in clean_targets if text_key(t['source_quote'])==text_key(criterion['source_quote'])]
            if criterion['scope']=='common' and len(owners)==1:criterion['scope']=owners[0]
        if any(not any(c['kind']=='must' and c['scope']==target for c in clean_criteria) for target in ids):
            raise ModelError('independent B target requires its own positive condition')
    for target in ids:
        if not any(c['kind']=='must' and c['scope'] in ('common',target) for c in clean_criteria):raise ModelError('B target has no positive condition')
    queries=value.get('queries',[])
    return {'targets':clean_targets,'criteria':clean_criteria,
            'queries':[q[:400] for q in queries if isinstance(q,str) and q.strip()][:4] if isinstance(queries,list) else []}


def fallback_plan(task):
    text=task_text(task)
    return {'targets':[{'id':'t0','text':'按完整题面判断','query':task.get('input',text),'source_quote':text}],
            'criteria':[{'id':'r0','text':text,'kind':'must','scope':'common','query':task.get('input',text),'source_quote':text}], 'queries':[]}


def source_span(quote, text):
    """只恢复可逐字定位的原文；兼容PDF断行连字符和排版字形，不作模糊语义匹配。"""
    def normalized(value, remove_line_hyphens=False):
        omitted={m.start() for m in re.finditer(r"-(?=\s*\n\s*\w)",value)} if remove_line_hyphens else set()
        letters=[];positions=[]
        for i,char in enumerate(value):
            if i in omitted or char=='\u00ad':continue
            for c in unicodedata.normalize('NFKC',char).casefold():
                if not c.isspace():letters.append(c);positions.append(i)
        return ''.join(letters),positions
    needle,_=normalized(quote)
    if not needle:return None
    for remove in (False,True):
        haystack,positions=normalized(text,remove)
        offset=haystack.find(needle)
        if offset>=0:return text[positions[offset]:positions[offset+len(needle)-1]+1]
    return None


def assess(item, candidate, plan):
    """每个条件独立核验；坏证据仅转unknown，不能推翻另一目标的完整有效证据。"""
    if not isinstance(item,dict) or item.get('filename')!=candidate['filename'] or not isinstance(item.get('reason'),str) or not item['reason'].strip():
        raise ModelError('invalid B filename/reason')
    checks=item.get('checks')
    if not isinstance(checks,list):raise ModelError('B checks must be an array')
    known={c['id']:c for c in plan['criteria']}
    chunks={c['chunk_id']:c['text'] for c in candidate['chunks']}
    states={};verified=[];issues=[]
    for check in checks:
        if not isinstance(check,dict) or not isinstance(check.get('criterion_id'),str) or check['criterion_id'] not in known or check['criterion_id'] in states:
            raise ModelError('unknown or duplicate B criterion')
        cid=check['criterion_id'];status=check.get('status');entries=check.get('evidence')
        if status not in ('met','not_met','unknown') or not isinstance(entries,list) or len(entries)>2:
            raise ModelError('invalid B criterion status/evidence')
        valid=[];invalid=False
        for entry in entries:
            if not isinstance(entry,dict) or not isinstance(entry.get('chunk_id'),str) or entry['chunk_id'] not in chunks:
                invalid=True;continue
            quote=entry.get('quote') if 'quote' in entry else chunks[entry['chunk_id']]
            if not isinstance(quote,str) or not 8<=len(quote.strip())<=400:
                invalid=True;continue
            chunk_id=entry['chunk_id'];span=source_span(quote,chunks[chunk_id])
            if span is None:
                matches=[(key,source_span(quote,text)) for key,text in chunks.items() if key!=chunk_id]
                matches=[pair for pair in matches if pair[1] is not None]
                if len(matches)==1:chunk_id,span=matches[0]
            if span is None:invalid=True
            else:
                source=next(c for c in candidate['chunks'] if c['chunk_id']==chunk_id)
                valid.append({'chunk_id':source.get('source_chunk_id',chunk_id),'quote':span})
        if invalid or status!='unknown' and not valid:
            issues.append({'criterion_id':cid,'issue':'unsupported_evidence','received':check})
            status='unknown';valid=[]
        states[cid]=status;verified.append({'criterion_id':cid,'status':status,'evidence':valid})
    for cid in known.keys()-states.keys():
        states[cid]='unknown';verified.append({'criterion_id':cid,'status':'unknown','evidence':[]})
        issues.append({'criterion_id':cid,'issue':'omitted_check'})
    matched=[];missing=set();review=set()
    for target in plan['targets']:
        conditions=[c for c in known.values() if c['scope'] in ('common',target['id'])]
        expected=lambda c:'met' if c['kind']=='must' else 'not_met'
        if all(states[c['id']]==expected(c) for c in conditions):matched.append(target['id'])
        elif not any(states[c['id']] not in ('unknown',expected(c)) for c in conditions):
            missing.update(c['id'] for c in conditions if states[c['id']]=='unknown')
        else:
            failures=[c['id'] for c in conditions if states[c['id']] not in ('unknown',expected(c))]
            if len(failures)==1 and any(states[c['id']]==expected(c) for c in conditions):
                review.update(failures)
                review.update(c['id'] for c in conditions if states[c['id']]=='unknown')
    return {'filename':candidate['filename'],'reason':item['reason'].strip(),'targets':matched,
            'missing':sorted(missing),'review_criteria':sorted(review) if not matched else [],'checks':verified,'issues':issues,'accepted':bool(matched)}


def requested_limit(text):
    """仅明确总数/上限；至少、每目标与同一篇不截断。"""
    digits={x:i+1 for i,x in enumerate('一二三四五六七八九')};digits['两']=2
    words=dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split(),range(21)))
    def number(s):
        if s.isdigit():return int(s)
        if s in words:return words[s]
        if s in digits:return digits[s]
        if s.count('十')==1:
            a,b=s.split('十')
            if (not a or a in digits) and (not b or b in digits):return (digits.get(a,1)*10)+digits.get(b,0)
    found=[]
    pattern=r'(\d{1,3}|[一二三四五六七八九十两]{1,3})\s*篇|\b('+'|'.join(words)+r'|\d{1,3})\s+(?:papers?|articles?|studies)\b'
    for match in re.finditer(pattern,text,re.I):
        raw=(match.group(1) or match.group(2)).lower();value=number(raw)
        before=text[max(0,match.start()-50):match.start()].lower();after=text[match.end():match.end()+25].lower()
        if value is None:continue
        total=bool(re.search(r'(?:合计|总计|总共|一共|共|in total|total of|overall)\s*$',before))
        if not total and (re.search(r'(?:至少|不少于|最少|同|每|各|分别|at least|minimum of|more than)\s*$',before) or re.match(r'\s*(?:以上|或更多|per\b|for each\b|or more\b)',after) or re.search(r'(?:(?:每|各).{0,8}(?:方向|目标|主题|领域).{0,8}|分别(?:找|选|取|推荐|返回)?)$',before)):
            continue
        exact=bool(re.search(r'(?:最多|至多|不超过|恰好|仅|只|at most|up to|no more than|exactly|only)\s*$',before))
        request=bool(re.search(r'(?:找|查找|检索|返回|列出|提供|推荐|find|return|list|retrieve|select)\s*(?:top\s+)?$',before))
        if total or exact or request or not before.strip():found.append((2 if total or exact else 1,value))
    hard=[v for priority,v in found if priority==2]
    return min(hard) if hard else (found[0][1] if found else None)
