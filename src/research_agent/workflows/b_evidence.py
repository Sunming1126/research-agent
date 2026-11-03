"""B 专用多路召回与连续原文选择；不修改 C/E 使用的检索器。"""
import math
import re
from collections import defaultdict, Counter
from types import SimpleNamespace
import unicodedata
from ..core.utils import tokenize as shared_tokenize
from ..core.session import checkpoint
from ..rag.retrieval import SYNONYMS


def tokenize(text):
    """B-only lexical normalization. Evidence always retains its original PDF text."""
    text=unicodedata.normalize('NFKC',text).casefold()
    text=re.sub(r'(?<=[a-z])-\s*\n\s*(?=[a-z])','',text)
    result=[]
    for token in shared_tokenize(text):
        token=token.strip('._-/+')
        if not token:continue
        result.append(token)
        parts=re.findall(r'[a-z0-9]+',token) if re.search(r'[-./+]',token) else []
        result.extend(part for part in parts if part!=token)
    return result


def lexical_index(index):
    # Separate object: do not alter the shared retriever or another category's postings.
    if getattr(index,'b_normalized',False):return index
    counts=[Counter(tokenize(c.text)) for c in index.chunks]
    df=Counter();postings=defaultdict(set);documents=defaultdict(list)
    for pos,terms in enumerate(counts):
        if pos%256==0:checkpoint('b_index')
        documents[index.chunks[pos].filename].append(pos);df.update(terms.keys())
        for term in terms:postings[term].add(pos)
    return SimpleNamespace(chunks=index.chunks,counts=counts,df=df,postings=postings,
        documents=documents,avg_len=sum(sum(c.values()) for c in counts)/max(1,len(counts)),b_normalized=True)


def search(index, query, limit=32):
    index=lexical_index(index)
    terms=set(tokenize(query));positions=set().union(*(index.postings.get(t,set()) for t in terms))
    explicit={name for name in index.documents if name.casefold() in query.casefold()}
    for name in explicit:positions.update(index.documents[name])
    scores=defaultdict(list)
    for pos in sorted(positions):
        if pos%256==0:checkpoint('b_recall')
        chunk=index.chunks[pos];counts=index.counts[pos]
        if not chunk.filename.lower().endswith('.pdf'):continue
        matched=terms&counts.keys();length=sum(counts.values());bm25=0
        for term in matched:
            tf=counts[term];idf=math.log(1+(len(index.chunks)-index.df[term]+.5)/(index.df[term]+.5))
            bm25+=idf*tf*2.2/(tf+1.2*(.25+.75*length/max(1,index.avg_len)))
        coverage=len(matched)/max(1,len(terms))
        title=set(tokenize(chunk.heading+' '+(chunk.text[:500] if chunk.page==1 else '')))
        score=.5*bm25/(bm25+3)+.35*coverage+.15*len(terms&title)/max(1,len(terms))
        if chunk.filename in explicit:score+=.5
        scores[chunk.filename].append((score,chunk))
    return sorted([(name,max(s for s,_ in hits),sorted(hits,key=lambda x:(-x[0],x[1].start))) for name,hits in scores.items()],key=lambda x:(-x[1],x[0]))[:limit]


def channels(task, plan, *, missing=()):
    rows=[]
    def add(scope,q):
        if isinstance(q,str) and q.strip() and (scope,q.strip()) not in rows:rows.append((scope,q.strip()))
    if not missing:
        add('common',task.get('input',''))
        translated=task.get('input','')
        for term,value in SYNONYMS.items():translated=translated.replace(term,value)
        add('common',translated)
        for q in plan['queries']:add('common',q)
        for t in plan['targets']:add(t['id'],t['query'])
    for c in plan['criteria']:
        if c['kind']=='must' and (not missing or c['id'] in missing):add(c['scope'],c['query'])
    return rows


def recall(index, query_channels, limit):
    """RRF只融合名次，保留所有通道的证据；不同目标轮流入队。"""
    if index is not None:index=lexical_index(index)
    merged={};queues=defaultdict(list);scope_scores=defaultdict(lambda:defaultdict(float))
    for scope,query in query_channels:
        for rank,(name,score,hits) in enumerate(search(index,query,limit),1):
            row=merged.setdefault(name,{'filename':name,'rrf':0.,'hits':{},'origins':set()})
            row['rrf']+=1/(60+rank);row['origins'].add(scope);scope_scores[scope][name]+=1/(60+rank)
            for weight,chunk in hits[:12]:
                old=row['hits'].get(chunk.chunk_id)
                if old is None or weight>old[0]:row['hits'][chunk.chunk_id]=(weight,chunk)
            if name not in queues[scope]:queues[scope].append(name)
    for scope in queues:queues[scope].sort(key=lambda n:(-scope_scores[scope][n],-merged[n]['rrf'],n))
    order=[];scopes=sorted(queues,key=lambda s:(s=='common',s));position=0
    while len(order)<min(limit,len(merged)):
        changed=False
        for scope in scopes:
            if position<len(queues[scope]):
                name=queues[scope][position]
                if name not in order:order.append(name)
                changed=True
                if len(order)>=limit:break
        if not changed:break
        position+=1
    return [merged[name] for name in order]


def window(text, queries, maximum=1000):
    if len(text)<=maximum:return text
    terms=set(tokenize(' '.join(queries)));events=[]
    for term in sorted(terms):
        if len(term)<2:continue
        pattern=re.escape(term)
        if re.fullmatch('[a-z0-9_]+',term):pattern=r'(?<![A-Za-z0-9_])'+pattern+r'(?![A-Za-z0-9_])'
        events.extend((m.start(),m.end(),term) for m in re.finditer(pattern,text,re.I))
    starts={0,max(0,len(text)-maximum)}|{max(0,min(len(text)-maximum,a-80)) for a,_,_ in events}
    start=max(starts,key=lambda a:(len({t for x,y,t in events if a<=x and y<=a+maximum}),-a))
    return text[start:start+maximum]


def evidence(row, plan, *, more=False):
    queries=[c['query'] for c in plan['criteria']]
    facets=[set(tokenize(q)) for q in queries];covered=[set() for _ in facets]
    hits=sorted(row['hits'].values(),key=lambda x:(-x[0],x[1].start));selected=[];texts=set()
    # 至少覆盖方法和对象；复杂条件适度增加片段，不按页码强行分散。
    limit=min(7 if more else 5,max(3,len(facets)+(2 if more else 0)))
    while hits and len(selected)<limit:
        def gain(hit):
            weight,chunk=hit;terms=set(tokenize(chunk.text))
            return (sum(len((terms&f)-old)/max(1,len(f)) for f,old in zip(facets,covered)),weight,-chunk.start)
        front=[h for h in hits if h[1].page==1]
        best=max(front if not selected and front else hits,key=gain);hits.remove(best);_,chunk=best
        snippet=window(chunk.text,queries,1400 if more else 1000)
        key=re.sub(r'\s+','',snippet)
        if key in texts:continue
        texts.add(key);selected.append({'chunk_id':chunk.chunk_id,'page':chunk.page,'heading':chunk.heading,'text':snippet})
        tokens=set(tokenize(snippet))
        for i,f in enumerate(facets):covered[i].update(tokens&f)
    # 给模型选择原文短段编号，不要求它重新抄写PDF文字，避免断字和改写导致整条证据失效。
    spans=[]
    for source in selected:
        start=0;text=source['text']
        while start<len(text):
            end=min(start+360,len(text))
            if end<len(text):
                boundary=max(text.rfind('. ',start+160,end),text.rfind('\n',start+180,end))
                if boundary>start:end=boundary+1
            part=text[start:end]
            if part.strip():spans.append({**source,'source_chunk_id':source['chunk_id'],'chunk_id':source['chunk_id']+'@'+str(start),'text':part})
            start=end
    return {'filename':row['filename'],'chunks':spans}


def compact_evidence(row,plan,*,more=False,audit=False):
    """Bound per-document context, retaining complementary contiguous source windows."""
    queries=[c['query'] for c in plan['criteria']]+plan.get('queries',[])
    facets=[set(tokenize(q)) for q in queries];covered=[set() for _ in facets]
    hits=sorted(row['hits'].values(),key=lambda h:(-h[0],h[1].start))
    chunks=[];seen=set();budget=4000 if audit else 3200 if more else 1800
    while hits and budget>100 and len(chunks)<(6 if audit else 6 if more else 4):
        def gain(hit):
            terms=set(tokenize(hit[1].text))
            return (sum(len((terms&f)-old)/max(1,len(f)) for f,old in zip(facets,covered)),hit[0],-hit[1].start)
        front=[h for h in hits if h[1].page==1]
        # Reserve one slot for the paper's own actions, not just a keyword-rich
        # introduction/related-work paragraph. No paper-specific terms or IDs.
        owned=[h for h in hits if re.search(
            r'\bwe\s+(?:use|employ|adopt|consider|retrieve|train|evaluate|measure|collect|compare)\b|'
            r'\bour\s+(?:method|model|experiment|study|approach)\b|本文(?:采用|使用|研究|实验)|我们(?:使用|采用|测量)',
            h[1].text,re.I)]
        choices=front if not chunks and front else owned if len(chunks)==1 and owned else hits
        hit=max(choices,key=gain);hits.remove(hit)
        chunk=hit[1];text=window(chunk.text,queries,min(budget,1100 if audit else 900 if not chunks else 600))
        key=re.sub(r'\s+','',text)
        if key in seen:continue
        seen.add(key);budget-=len(text)
        chunks.append({'chunk_id':chunk.chunk_id,'page':chunk.page,'heading':chunk.heading,'text':text})
        terms=set(tokenize(text))
        for i,f in enumerate(facets):covered[i].update(terms&f)
    return {'filename':row['filename'],'chunks':chunks}
