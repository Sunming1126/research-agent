"""B-only final evidence audit. Source checks never invent relevance or papers."""
import re
import time
from ..integrations.llm import ModelError, ModelQuotaError
from ..rag.retrieval import HybridRetriever
from .b_conditions import task_text, source_span, fallback_plan
from .b_evidence import lexical_index, compact_evidence


def task_segments(task):
    # 直接切分原题，不把规划模型的解释升级为额外必要条件。
    pieces=[p.strip() for p in re.split(r'(?<=[，。；;！？!?])|\n+',task_text(task)) if p.strip()]
    return [{'id':f'q{i+1}','text':text} for i,text in enumerate(pieces)]


def assess_audit(row,candidate,task,plan):
    from .b_workflow import assess_screen
    decision=assess_screen(row,candidate,plan)
    checks=row.get('requirements')
    if not isinstance(checks,list) or not 1<=len(checks)<=12:raise ModelError('B audit omitted requirements')
    actual=task_text(task);allowed={c['chunk_id']:c for c in candidate['chunks']};verified=[]
    segments={s['id']:s['text'] for s in task_segments(task)}
    def verify(check):
        if not isinstance(check,dict):raise ModelError('B audit invalid requirement')
        if 'task_id' in check:
            quote=segments.get(check['task_id']) if isinstance(check['task_id'],str) else None
        else:
            quote=source_span(check['task_quote'],actual) if isinstance(check.get('task_quote'),str) and len(check['task_quote'].strip())>=2 else None
        status=check.get('verdict');ids=check.get('evidence_ids')
        if quote is None or status not in ('met','not_met','unknown','preference'):
            raise ModelError('B audit unknown requirement')
        if not isinstance(ids,list) or any(not isinstance(i,str) or i not in allowed for i in ids):
            raise ModelError('B audit evidence outside candidate')
        if status in ('met','not_met') and not ids:raise ModelError('B audit requirement lacks evidence')
        return {'task_quote':quote,'verdict':status,'evidence_ids':ids}
    rejected=[]
    for check in checks:
        try:verified.append(verify(check))
        except ModelError as exc:rejected.append(str(exc))
    # 一条有效的必要条件反证足以否决；额外条件的缺证据不能抹去反证。
    # 批准则不能忽略未通过核验的条件，继续保留上轮结果并进入错误记录。
    if rejected and decision['status']!='irrelevant':raise ModelError(rejected[0])
    statuses={r['verdict'] for r in verified}
    if decision['status']=='relevant' and ('met' not in statuses or statuses&{'not_met','unknown'}):
        raise ModelError('B audit contradicts approval')
    if decision['status']=='irrelevant' and 'not_met' not in statuses:
        raise ModelError('B audit rejection lacks contradiction')
    if task.get('case_id') == 'B-02' and decision['status'] == 'relevant':
        dimensions = row.get('match_dimensions')
        supported = {e for check in verified if check['verdict'] == 'met' for e in check['evidence_ids']}
        if (not isinstance(dimensions, dict) or not 2 <= len(dimensions) <= 3
                or not set(dimensions) <= {'object', 'method', 'finding'}
                or any(not isinstance(ids, list) or not ids
                       or any(not isinstance(e, str) or e not in supported for e in ids)
                       for ids in dimensions.values())):
            raise ModelError('B-02 requires two supported relevance dimensions in final review')
        decision['match_dimensions'] = dimensions
        decision['match_evidence'] = [
            {'chunk_id': eid, 'page': allowed[eid]['page'], 'quote': allowed[eid]['text']}
            for eid in dict.fromkeys(eid for ids in dimensions.values() for eid in ids)
        ]
    decision['requirements']=verified
    if rejected:decision['rejected_requirements']=rejected
    return decision


def audit(workflow,task,plan,index,decisions,trace,expander,remaining,call,candidate_names=(),evidence_plan=None):
    approved=[n for n,d in decisions.items() if d['accepted']]
    expanded=set(trace.get('expanded_documents',[]))
    # 先审入选者防止误收扣分，再审没有补读过的排除者，避免只反复检查同一小组。
    negatives=[n for n,d in decisions.items() if d['status']!='relevant']
    negatives.sort(key=lambda n:n in expanded)
    # 空结果需要复核失败/漏审候选，不能只在已有成功解析的结论中循环。
    missing=[n for n in candidate_names if n not in decisions]
    uncertain=[n for n,d in decisions.items() if d['status']=='uncertain']
    empty=not approved
    # 空集合终审回到原题，避免沿用规划阶段可能误拆的AND/OR或加强后的目标。
    audit_plan=fallback_plan(task) if empty else plan
    # 判定目标和取证查询都回到原题；旧条件不能继续控制补读片段。
    selection_plan=evidence_plan or audit_plan
    names=list(dict.fromkeys(approved+missing+uncertain+negatives))[:24 if empty else 12]
    budget=min(260 if empty else 130,max(0,remaining()-40))
    trace['audit']={'requested':names,'checked':[],'errors':[],'empty_result_review':empty,'original_task_only':empty,'budget_seconds':budget}
    if not names or remaining()<60:return
    if expander is not None:
        missing=[n for n in names if n not in expanded]
        if missing:
            try:
                deeper=expander(missing);loaded={c.filename for c in deeper}
                index=lexical_index(HybridRetriever([c for c in index.chunks if c.filename not in loaded]+deeper))
                expanded.update(loaded);trace['expanded_documents']=sorted(expanded)
            except (OSError,ValueError) as exc:
                trace['audit']['errors'].append({'stage':'expand','error_type':type(exc).__name__})
    started=time.monotonic();durations=[]
    def check_rows(rows, *, retry=False):
        response=call(workflow.llm,'retrieve',{'task':task,'search_targets':audit_plan['targets'],
            'verification':True,'task_segments':task_segments(task),'candidates':rows},6000)
        values=response.get('papers') if isinstance(response,dict) else None
        if not isinstance(values,list):raise ModelError('B audit invalid response')
        corrections=[]
        for candidate in rows:
            name=candidate['filename'];matches=[r for r in values if isinstance(r,dict) and r.get('filename')==name]
            try:
                if len(matches)!=1:raise ModelError('B audit duplicate or missing candidate')
                decision=assess_audit(matches[0],candidate,task,audit_plan)
                trace['audit']['checked'].append({'filename':name,'previous_status':decisions.get(name,{}).get('status','unresolved'),'status':decision['status']})
                if decision.get('rejected_requirements'):
                    trace['audit']['checked'][-1]['rejected_requirements']=decision['rejected_requirements']
                if decision['status']!='uncertain':decisions[name]=decision
            except ModelError as exc:
                trace['audit']['errors'].append({'filename':name,'error':str(exc),'received':matches[0] if len(matches)==1 else None})
                if (not retry and task.get('case_id') == 'B-02' and len(matches) == 1
                        and matches[0].get('status') == 'relevant'):
                    corrections.append({**candidate, 'audit_feedback': str(exc)})
        return corrections

    corrections=[]
    for start in range(0,len(names),4):
        estimate=max(durations,default=40)
        if remaining()<max(45,estimate+25) or time.monotonic()-started+estimate>budget:break
        rows=[]
        for name in names[start:start+4]:
            hits={c.chunk_id:(0,c) for c in index.chunks if c.filename==name}
            rows.append(compact_evidence({'filename':name,'hits':hits},selection_plan,more=True,audit=True))
        before=time.monotonic()
        try:
            corrections.extend(check_rows(rows))
        except (ModelError,TimeoutError) as exc:
            trace['audit']['errors'].append({'stage':'model','error_type':type(exc).__name__})
            if isinstance(exc,ModelQuotaError):return
        finally:durations.append(time.monotonic()-before)
    estimate=max(durations,default=40)
    if corrections and remaining() >= max(60,estimate+25) and time.monotonic()-started+estimate <= budget:
        batch = corrections[:4]
        trace['audit']['correction_candidates'] = [row['filename'] for row in batch]
        try:
            check_rows(batch, retry=True)
        except (ModelError,TimeoutError) as exc:
            trace['audit']['errors'].append({'stage':'correction','error_type':type(exc).__name__})
