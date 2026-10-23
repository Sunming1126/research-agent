"""Independent checks derived from the delivered public methods.

Only public requests are used. No private gold labels, official scores or
provenance credentials are produced. This file is never given to the target.
"""
from collections import Counter,defaultdict
import math
import re


def require(value,message):
    if not value: raise AssertionError(message)


def close(a,b,tol=1e-8):
    return isinstance(a,(int,float)) and not isinstance(a,bool) and math.isfinite(a) and abs(a-b)<=tol*max(1,abs(b))


def tokens(text):return re.findall(r'[a-z0-9]+',text.lower())


def ranking_ids(rows):
    return [str(x['id']) if isinstance(x,dict) else str(x[0]) for x in rows]


def rank_bm25(data,queries,config):
    docs=data['documents'];n=len(docs)
    indexed={}
    for field in ['text','title']:
        counts=[Counter(tokens(x.get(field,''))) for x in docs]
        df=Counter()
        for c in counts:df.update(c.keys())
        avg=sum(sum(c.values()) for c in counts)/max(1,n) or 1
        indexed[field]=(counts,df,avg)
    result={}
    for q in queries:
        scores=[]
        for i,doc in enumerate(docs):
            score=0
            for field,weight in [('text',1),('title',config['title_weight'])]:
                counts,df,avg=indexed[field];c=counts[i];length=sum(c.values())
                for token in tokens(q['text']):
                    f=c[token]
                    if f:
                        score+=weight*math.log(1+(n-df[token]+.5)/(df[token]+.5))*f*(config['k1']+1)/(f+config['k1']*(1-config['b']+config['b']*length/avg))
            scores.append({'id':str(doc['id']),'score':score})
        result[q['id']]=sorted(scores,key=lambda x:(-x['score'],x['id']))
    return result


def query_metrics(queries,rankings):
    values=[]
    for q in queries:
        ids=ranking_ids(rankings[q['id']]); relevant=set(map(str,q['relevant']))
        recall=len(set(ids[:5])&relevant)/len(relevant)*100 if relevant else 0
        rr=next((1/(i+1) for i,v in enumerate(ids[:10]) if v in relevant),0)
        values.append((recall,rr))
    return (sum(x[0] for x in values)/len(values),sum(x[1] for x in values)/len(values)) if values else (0,0)


def verify_rankings(actual,expected,queries):
    require(isinstance(actual,dict),'Rankings must be a query-id object')
    for q in queries:
        qid=q['id'];require(qid in actual,'Missing query '+qid)
        rows=actual[qid]
        require(len(rows)==len(expected[qid]),'Not all documents returned for '+qid)
        require(ranking_ids(rows)==ranking_ids(expected[qid]),'Wrong ordering for '+qid)
        scores=[x.get('score') if isinstance(x,dict) else x[1] for x in rows]
        require(all(isinstance(x,(int,float)) and math.isfinite(x) for x in scores),'Non-finite ranking score')


def verify_d02(request,result):
    d=request['data'];p=request['protocol'];phase=request['phase']
    if phase=='rank':
        expected=rank_bm25(d,d['test_queries'],d['selected'])
        verify_rankings(result['candidate'],expected,d['test_queries'])
        return {'rank_phase':True,'queries':len(d['test_queries'])}
    require(set(result)=={'baseline','candidate','details'},'Wrong D02 internal fields')
    baseline=rank_bm25(d,d['test_queries'],p['default_config'])
    verify_rankings(result['baseline'],baseline,d['test_queries'])
    require(result['candidate'] is not None,'Only a baseline returned; candidate method not implemented')
    detail=result['details'];search=detail['search']
    require(len(search)==len(p['grid']),'Expected every grid configuration')
    groups=sorted({q['group'] for q in d['dev_queries']})
    scored=[]
    for cfg in p['grid']:
        matches=[x for x in search if x.get('config')==cfg]
        require(len(matches)==1,'Missing/duplicate search configuration '+str(cfg))
        row=matches[0];ranking=rank_bm25(d,d['dev_queries'],cfg)
        # Publicly accepted alternate dev-ranking encodings are normalized.
        raw=row['rankings']
        if isinstance(raw,list):
            if raw and isinstance(raw[0],dict) and 'query_id' in raw[0]:
                raw={x['query_id']:x['rankings'] for x in raw}
            else:raw={q['id']:r for q,r in zip(d['dev_queries'],raw)}
        verify_rankings(raw,ranking,d['dev_queries'])
        recall,mrr=query_metrics(d['dev_queries'],ranking)
        require(close(row['recall_at_5_percent'],recall),'Wrong ordinary dev Recall')
        require(close(row['mrr_at_10'],mrr),'Wrong ordinary dev MRR')
        gm=row['group_metrics'];group_values=[]
        for g in groups:
            gr,gmrr=query_metrics([q for q in d['dev_queries'] if q['group']==g],ranking)
            require(close(gm['groups'][g]['recall_at_5_percent'],gr),'Wrong group Recall')
            require(close(gm['groups'][g]['mrr_at_10'],gmrr),'Wrong group MRR')
            group_values.append((gr,gmrr))
        worst=min(x[1] for x in group_values);macro_mrr=sum(x[1] for x in group_values)/len(group_values)
        macro_recall=sum(x[0] for x in group_values)/len(group_values)
        for key,value in [('worst_mrr_at_10',worst),('macro_mrr_at_10',macro_mrr),('macro_recall_at_5_percent',macro_recall)]:
            require(close(gm[key],value),'Wrong '+key)
        selection_key=(-round(worst,12),-round(macro_mrr,12),-round(macro_recall,12),cfg['title_weight'],cfg['k1'],cfg['b'])
        scored.append((selection_key,cfg))
    selected=min(scored,key=lambda x:x[0])[1]
    require(detail['selected']==selected,'Selected configuration does not optimize worst-group/macro metrics')
    verify_rankings(result['candidate'],rank_bm25(d,d['test_queries'],selected),d['test_queries'])
    return {'grid_checked':len(search),'groups':len(groups),'selected':selected}


def verify_d01(request,result):
    d=request['data'];train=d['train'];texts=d['texts']
    require(set(result)=={'baseline','candidate','details'},'Wrong D01 internal fields')
    classes=sorted({x['label'] for x in train})
    freq=Counter(x['label'] for x in train);majority=min(classes,key=lambda c:(-freq[c],c))
    require(result['baseline']==[majority]*len(texts),'Wrong majority baseline')
    require(result['candidate'] is not None,'Candidate model missing')
    model=result['details'];vocab=sorted({t for x in train for t in tokens(x['text'])})
    require(model['classes']==classes,'Class order must be lexicographic')
    require(model['vocabulary']==vocab,'Vocabulary differs from training-only vocabulary')
    df=Counter()
    for x in train:df.update(set(tokens(x['text'])))
    idf=[1+math.log((1+len(train))/(1+df[t])) for t in vocab]
    raw_idf=model['idf'];raw_idf=[raw_idf[t] for t in vocab] if isinstance(raw_idf,dict) else raw_idf
    require(len(raw_idf)==len(idf) and all(close(a,b) for a,b in zip(raw_idf,idf)),'Wrong training IDF')
    w=model['weights']
    require(len(w)==len(classes) and all(len(row)==len(vocab)+1 for row in w),'Wrong model dimensions')
    require(all(isinstance(v,(int,float)) and math.isfinite(v) for row in w for v in row),'Non-finite model weights')
    def vector(text):
        count=Counter(tokens(text));v=[count[t]*idf[i] for i,t in enumerate(vocab)];norm=math.sqrt(sum(x*x for x in v))
        return [x/norm if norm else 0 for x in v]+[1]
    predicted=[]
    for text in texts:
        x=vector(text);scores=[sum(a*b for a,b in zip(row,x)) for row in w]
        predicted.append(classes[max(range(len(classes)),key=lambda k:scores[k])])
    require(result['candidate']==predicted,'Reported model does not reproduce candidate labels')
    # Verify actual public training method independently, allowing numerical
    # equivalence rather than comparing target implementation text.
    expected=[[0.0]*(len(vocab)+1) for _ in classes];vectors=[vector(x['text']) for x in train]
    p=request['protocol'];indices={c:i for i,c in enumerate(classes)}
    for _ in range(p['epochs']):
        grad=[[0.0]*(len(vocab)+1) for _ in classes]
        for row,item in zip(vectors,train):
            logits=[sum(a*b for a,b in zip(weights,row)) for weights in expected];mx=max(logits)
            exp=[math.exp(v-mx) for v in logits];den=sum(exp)
            for k in range(len(classes)):
                delta=exp[k]/den-(k==indices[item['label']])
                for j in range(len(row)):grad[k][j]+=delta*row[j]
        expected=[[v-p['learning_rate']*(grad[k][j]/len(train)+p['penalty']*v) for j,v in enumerate(row)] for k,row in enumerate(expected)]
    require(all(close(a,b,tol=1e-5) for row,ref in zip(w,expected) for a,b in zip(row,ref)),'Model does not match specified full-batch softmax training within tolerance')
    return {'training_rows':len(train),'test_rows':len(texts),'vocabulary':len(vocab),'model_replay_verified':True}


def schedule(data,fair):
    jobs=sorted(data['jobs'],key=lambda x:(x['arrival_ms'],x['id']))
    weights=data['flow_weights'];servers=int(data['servers']);horizon=data['horizon_ms']
    free=[0]*servers;last=[None]*servers;assigned={k:0 for k in weights};served={k:0 for k in weights}
    waiting=[];trace=[];dropped=[];i=0;now=0
    while i<len(jobs) or waiting:
        if now>=horizon:
            dropped.extend({'id':x['id'],'reason':'horizon'} for x in waiting+jobs[i:]);break
        while i<len(jobs) and jobs[i]['arrival_ms']<=now:
            waiting.append(jobs[i]);i+=1
        expired=[x for x in waiting if x['deadline_ms']<=now]
        dropped.extend({'id':x['id'],'reason':'deadline'} for x in expired)
        waiting=[x for x in waiting if x['deadline_ms']>now]
        for server in range(servers):
            if free[server]>now:continue
            while waiting:
                job=min(waiting,key=lambda x:((assigned[x['flow']]/weights[x['flow']],x['arrival_ms'],x['id']) if fair else (x['arrival_ms'],x['id'])))
                waiting.remove(job)
                setup=data['setup_ms'] if last[server] is not None and last[server]!=job['flow'] else 0
                start=now+setup;end=start+job['service_ms']
                if start>=job['deadline_ms']:
                    dropped.append({'id':job['id'],'reason':'deadline'});continue
                if end>horizon:
                    dropped.append({'id':job['id'],'reason':'horizon'});continue
                row=dict(job);row.update(server=server,dispatch_ms=now,setup_ms=setup,start_ms=start,end_ms=end,wait_ms=start-job['arrival_ms'])
                trace.append(row);free[server]=end;last[server]=job['flow']
                assigned[job['flow']]+=job['service_ms'];served[job['flow']]+=job['service_ms'];break
        future=[x for x in free if x>now]
        if i<len(jobs):future.append(jobs[i]['arrival_ms'])
        if not future:
            require(not waiting,'Reference scheduler cannot make progress');break
        now=min(future)
    return {'trace':sorted(trace,key=lambda x:(x['dispatch_ms'],x['server'])),'served_ms':served,'dropped':sorted(dropped,key=lambda x:x['id'])}


def same_structure(actual,expected,path=''):
    if isinstance(expected,dict):
        require(isinstance(actual,dict) and set(actual)==set(expected),'Wrong fields at '+path)
        for k,v in expected.items():same_structure(actual[k],v,path+'/'+str(k))
    elif isinstance(expected,list):
        require(isinstance(actual,list) and len(actual)==len(expected),'Wrong list length at '+path)
        for i,(a,b) in enumerate(zip(actual,expected)):same_structure(a,b,path+'/'+str(i))
    elif isinstance(expected,(int,float)):
        require(close(actual,expected),'Wrong numeric value at '+path+': '+str(actual)+' != '+str(expected))
    else:require(actual==expected,'Wrong value at '+path)


def verify_d05(request,result):
    require(set(result)=={'baseline','candidate','details'},'Wrong D05 internal fields')
    require(result['candidate'] is not None,'Candidate fairness scheduler missing')
    for key,fair in [('baseline',False),('candidate',True)]:
        same_structure(result[key],schedule(request['data'],fair),key)
        ids=[x['id'] for x in result[key]['trace']]+[x['id'] for x in result[key]['dropped']]
        require(sorted(ids)==sorted(x['id'] for x in request['data']['jobs']),'Task conservation violated')
    return {'jobs':len(request['data']['jobs']),'servers':request['data']['servers'],'fifo_and_fair_verified':True}


def verify_public(request,result):
    return {'D-01':verify_d01,'D-02':verify_d02,'D-05':verify_d05}[request['case_id']](request,result)
