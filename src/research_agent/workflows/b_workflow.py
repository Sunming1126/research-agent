"""B 专用条件核验、一次缺口补检和有界错误恢复。"""
import json
import os
import time
from contextlib import contextmanager
from ..core.session import current_session, checkpoint
from ..integrations.llm import ModelError, ModelQuotaError
from ..rag.retrieval import HybridRetriever
from .b_conditions import make_plan, fallback_plan, requested_limit
from .b_evidence import channels, recall, compact_evidence, lexical_index


def remaining():
    session=current_session()
    return session.remaining() if session else float('inf')


@contextmanager
def window(llm,stage):
    cap,reserve=(20,75) if stage=='query' else (45,15)
    names=('deadline','REQUEST_TIMEOUT_CAP','RETRY_ATTEMPTS','thinking_override')
    saved={n:(n in vars(llm),getattr(llm,n,None)) for n in names}
    try:
        end=time.monotonic()+cap;session=current_session()
        if saved['deadline'][1] is not None:end=min(end,saved['deadline'][1])
        if session:end=min(end,session.deadline-reserve)
        if end-time.monotonic()<1:raise ModelError('B stage has insufficient remaining time')
        llm.deadline=end;llm.REQUEST_TIMEOUT_CAP=cap;llm.RETRY_ATTEMPTS=1;llm.thinking_override='disabled'
        checkpoint('b_'+stage);yield
    finally:
        for name,(present,value) in saved.items():
            if present:setattr(llm,name,value)
            else:vars(llm).pop(name,None)


def call(llm,stage,payload,maximum):
    data={'b_protocol':'conditions-v1',**payload}
    if hasattr(llm,'output_capacity'):
        maximum=min(maximum,llm.output_capacity(stage,data,maximum=maximum))
    if maximum<800:raise ModelError('insufficient B model budget')
    with window(llm,stage):return llm.json(stage,data,max_tokens=maximum)


def retryable(error):
    if isinstance(error,ModelQuotaError):return False
    return any(x in str(error).lower() for x in ('timeout','timed out','transport','invalid json','incomplete','response','checks','criterion','evidence','omitted','unknown','duplicate','filename/reason'))


def assess_screen(row,candidate,plan):
    """Validate identities and source bindings; relevance remains a semantic decision."""
    name=candidate['filename']
    if not isinstance(row,dict) or row.get('filename')!=name:raise ModelError('B invalid filename/reason')
    reason=row.get('reason');status=row.get('status');ids=row.get('evidence_ids')
    if not isinstance(reason,str) or not reason.strip() or status not in ('relevant','irrelevant','uncertain'):
        raise ModelError('B incomplete response status/reason')
    if not isinstance(ids,list) or any(not isinstance(i,str) for i in ids):raise ModelError('B invalid evidence array')
    available={c['chunk_id']:c for c in candidate['chunks']}
    if any(i not in available for i in ids):raise ModelError('B evidence ID outside candidate')
    if status!='uncertain' and not ids:raise ModelError('B decision requires evidence')
    targets=row.get('targets',[])
    if not isinstance(targets,list) or any(not isinstance(t,str) for t in targets):raise ModelError('B invalid response targets')
    targets=[t for t in targets if t in {x['id'] for x in plan['targets']}]
    return {'filename':name,'status':status,'accepted':status=='relevant','reason':reason.strip(),
            'targets':targets,'evidence':[{'chunk_id':i,'page':available[i]['page'],'quote':available[i]['text']} for i in dict.fromkeys(ids)]}


def retrieve(workflow,case,index):
    task=case.to_dict();task.pop('materials',None)
    trace={'version':'b6-document-review','rounds':[],'errors':[],'decisions':{},'unprocessed':[]}
    workflow.retrieval_trace=trace
    if not index.chunks:
        trace['outcome']='empty_authorized_library';return {'case_id':case.case_id,'papers':[]}
    proposed=None
    try:
        proposed=call(workflow.llm,'query',{'task':task},2200)
        plan=make_plan(proposed,task)
        workflow.query_plan_status='condition_plan'
    except ModelQuotaError:raise
    except (ModelError,TimeoutError,ValueError,TypeError) as exc:
        plan=fallback_plan(task);workflow.query_plan_status='original_conditions_fallback'
        # A bad criterion/scope must not discard otherwise useful bilingual search
        # variants. These remain search-only and never become relevance evidence.
        if isinstance(proposed,dict) and isinstance(proposed.get('queries'),list):
            plan['queries']=list(dict.fromkeys(q.strip() for q in proposed['queries']
                if isinstance(q,str) and q.strip() and len(q)<=240))[:4]
        trace['errors'].append({'stage':'plan','error':str(exc)})
    trace['condition_plan']=plan;trace['query_plan_status']=workflow.query_plan_status
    try:ceiling=max(8,min(60,int(os.environ.get('RESEARCH_B_CANDIDATE_LIMIT','48'))))
    except ValueError:ceiling=48
    requested=requested_limit('\n'.join([case.input,*case.constraints]));trace['requested_limit']=requested
    if requested==0:return {'case_id':case.case_id,'papers':[]}
    index=lexical_index(index)
    first_channels=channels(task,plan);pool=recall(index,first_channels,ceiling)
    # Small libraries are fully screened, including lexical zeroes (e.g. translated terminology).
    # Larger libraries keep query-driven priorities; fill remaining slots without inventing matches.
    present={r['filename'] for r in pool}
    for name,positions in index.documents.items():
        if len(pool)>=ceiling:break
        if name not in present and name.lower().endswith('.pdf'):
            pool.append({'filename':name,'rrf':0,'hits':{index.chunks[p].chunk_id:(0,index.chunks[p]) for p in positions},'origins':set()})
    trace.update(candidate_limit=ceiling,candidates=[r['filename'] for r in pool],
        library_documents=len(index.documents),fusion='document_rrf_k60_normalized_B_terms',
        outside_candidate_pool=max(0,len(index.documents)-len(pool)))
    decisions={};pending={};attempted=set();stopped=False;last_error=None
    candidates=[compact_evidence(r,plan) for r in pool]
    def batches(rows):
        result=[];batch=[]
        for row in rows:
            proposed=batch+[row]
            data={'b_protocol':'conditions-v1','task':task,'search_targets':plan['targets'],'candidates':proposed}
            estimate=workflow.llm.input_tokens('retrieve',data) if hasattr(workflow.llm,'input_tokens') else len(json.dumps(data,ensure_ascii=False))//3+700
            if batch and (len(proposed)>8 or estimate>10000):result.append(batch);batch=[row]
            else:batch=proposed
        if batch:result.append(batch)
        return result
    def review_batch(batch,round_index,batch_index):
        nonlocal stopped,last_error
        attempted.update(c['filename'] for c in batch)
        try:
            value=call(workflow.llm,'retrieve',{'task':task,'search_targets':plan['targets'],'candidates':batch},3000)
            if not isinstance(value,dict) or not isinstance(value.get('papers'),list):raise ModelError('B response requires papers array')
            rows={};duplicates=set();allowed={c['filename'] for c in batch}
            for row in value['papers']:
                if not isinstance(row,dict) or not isinstance(row.get('filename'),str) or row['filename'] not in allowed:continue
                if row['filename'] in rows:duplicates.add(row['filename'])
                rows[row['filename']]=row
            for c in batch:
                name=c['filename']
                try:
                    if name in duplicates or name not in rows:raise ModelError('B duplicate or omitted candidate')
                    decisions[name]=assess_screen(rows[name],c,plan);pending.pop(name,None)
                except ModelError as exc:
                    if not (round_index and name in decisions and decisions[name]['status']=='irrelevant'):pending[name]=c
                    last_error=exc
                    trace['errors'].append({'stage':'evidence','filename':name,'error':str(exc)})
        except (ModelError,TimeoutError) as exc:
            last_error=exc;trace['errors'].append({'stage':'retrieve','round':round_index,'batch':batch_index,'error':str(exc)})
            for c in batch:
                if not (round_index and c['filename'] in decisions and decisions[c['filename']]['status']=='irrelevant'):pending[c['filename']]=c
            if not retryable(exc):
                stopped=True
                if not decisions:raise
    initial=batches(candidates);trace['rounds'].append({'round':0,'batch_sizes':[len(b) for b in initial]})
    for i,batch in enumerate(initial):
        # Never reserve 65s after only a handful of papers. Cover at least 24 before
        # giving scarce remaining time to an uncertain paper already seen.
        uncertain=bool(pending or any(d['status']=='uncertain' for d in decisions.values()))
        reserve=65 if uncertain and len(attempted)>=24 else 30
        if stopped or remaining()<reserve:break
        review_batch(batch,0,i)
    unresolved=[n for n,d in decisions.items() if d['status']=='uncertain']
    negatives=[n for n,d in decisions.items() if d['status']=='irrelevant'] if remaining()>=45 else []
    names=list(dict.fromkeys([*pending,*unresolved,*negatives]))[:8]
    trace['negative_review_candidates']=[n for n in names if n in negatives]
    if names and not stopped and remaining()>=30:
        expand_names=[n for n in names if n in unresolved or n in negatives]
        if workflow.document_expander is not None and expand_names and remaining()>38:
            expander,workflow.document_expander=workflow.document_expander,None
            try:
                deeper=expander(expand_names);expanded={c.filename for c in deeper}
                index=lexical_index(HybridRetriever([c for c in index.chunks if c.filename not in expanded]+deeper))
                trace['expanded_documents']=sorted(expanded)
            except (OSError,ValueError) as exc:trace['errors'].append({'stage':'expand','error':str(exc)})
        followup=[]
        for name in names:
            hits={c.chunk_id:(0,c) for c in index.chunks if c.filename==name}
            c=compact_evidence({'filename':name,'hits':hits},plan,more=True)
            previous=decisions.get(name,{})
            c['review_note']={'previous_status':previous.get('status'),'previous_reason':previous.get('reason'),
                'instruction':'重新核对原题与本文方法。旧结论可能额外要求组件原创、误将关注重点作为必须条件、或把未看到证据当成不存在。只纠正有依据的错误；明确不符仍须排除。'}
            followup.append(c)
        second=batches(followup);trace['rounds'].append({'round':1,'batch_sizes':[len(b) for b in second]})
        for i,batch in enumerate(second):
            if stopped or remaining()<25:break
            review_batch(batch,1,i)
    approved={n:d for n,d in decisions.items() if d['accepted']}
    trace.update(decisions=decisions,pending=list(pending),approved=list(approved),
        unprocessed=[c['filename'] for c in candidates if c['filename'] not in attempted],
        screened_documents=len(decisions),attempted_documents=len(attempted),
        uncertain=[n for n,d in decisions.items() if d['status']=='uncertain'])
    incomplete=bool(pending or trace['uncertain'] or trace['unprocessed'] or stopped)
    if not approved and (pending or trace['uncertain'] or trace['unprocessed']):
        trace['outcome']='incomplete'
        raise ModelError('B retrieval incomplete without approved evidence: '+str(last_error))
    selected=[];covered=set();rows=list(approved.values());used=set()
    while rows and (requested is None or len(selected)<requested):
        best=max(rows,key=lambda r:len(set(r['targets'])-covered));rows.remove(best)
        if best['filename'].casefold() in used:continue
        used.add(best['filename'].casefold());covered.update(best['targets'])
        selected.append({'filename':best['filename'],'reason':best['reason']})
    trace['outcome']='partial' if incomplete or trace['outside_candidate_pool'] else 'complete'
    return {'case_id':case.case_id,'papers':selected}
