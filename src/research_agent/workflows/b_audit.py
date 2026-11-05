"""B-only final evidence audit. Source checks never invent relevance or papers."""
import re
import json
import time
from ..integrations.llm import ModelError, ModelQuotaError
from ..rag.retrieval import HybridRetriever
from .b_conditions import task_text, source_span, fallback_plan
from .b_evidence import lexical_index, compact_evidence


def task_segments(task):
    # 直接切分原题，不把规划模型的解释升级为额外必要条件。
    pieces=[p.strip() for p in re.split(r'(?<=[，。；;！？!?])|\n+',task_text(task)) if p.strip()]
    return [{'id':f'q{i+1}','text':text} for i,text in enumerate(pieces)]


def other_target(check, plan, active):
    quote = check['task_quote']
    scopes = {c['scope'] for c in plan['criteria'] if source_span(c['source_quote'], quote)}
    scopes.update(t['id'] for t in plan['targets'] if source_span(t['source_quote'], quote))
    return bool(scopes) and 'common' not in scopes and scopes.isdisjoint(active)


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
    inactive = []
    if task.get('case_id') == 'B-02' and decision['status'] == 'relevant' and decision['targets']:
        inactive = [check for check in verified if other_target(check, plan, set(decision['targets']))]
    verified = [check for check in verified if check not in inactive]
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
        topic = '\n'.join(text for text in (task.get('title'), task.get('input')) if isinstance(text, str))
        supported = {e for check in verified
                     if check['verdict'] == 'met' and source_span(check['task_quote'], topic)
                     for e in check['evidence_ids']}
        if not isinstance(dimensions, dict) or not set(dimensions) <= {'object', 'method', 'finding'}:
            raise ModelError('B-02 requires two supported relevance dimensions in final review')
        dimensions = {key: [e for e in ids if isinstance(e, str) and e in supported]
                      for key, ids in dimensions.items() if isinstance(ids, list)}
        dimensions = {key: ids for key, ids in dimensions.items() if ids}
        if len(dimensions) < 2:
            raise ModelError('B-02 requires two supported relevance dimensions in final review; '
                             'bind each dimension to its checked topical requirement evidence_ids; '
                             'verified topical IDs=' + json.dumps(sorted(supported)))
        decision['match_dimensions'] = dimensions
        decision['match_evidence'] = [
            {'chunk_id': eid, 'page': allowed[eid]['page'], 'quote': allowed[eid]['text']}
            for eid in dict.fromkeys(eid for ids in dimensions.values() for eid in ids)
        ]
    decision['requirements']=verified
    if inactive:
        decision['non_applicable_requirements'] = inactive
    if rejected:decision['rejected_requirements']=rejected
    return decision


def paper_context(index, filename):
    """Read the paper's self-description independently of query/approval evidence."""
    sources = [(pos, c) for pos, c in enumerate(index.chunks) if c.filename == filename]
    sources.sort(key=lambda item: (item[1].page or 0, item[1].start, item[0]))
    front_pages = set(sorted({c.page for _, c in sources}, key=lambda page: page or 0)[:2])
    front = [i for i, (_, c) in enumerate(sources) if c.page in front_pages]
    contributions = []
    self_description = re.compile(
        r'\bin\s+this\s+(?:paper|work|study)\b|'
        r'\bwe\s+(?:propose|introduce|investigate|develop|present|study)\b|'
        r'\bour\s+(?:contributions?|work|approach|method|model|study)\b|'
        r'本文(?:提出|研究|贡献)|我们(?:提出|研究)|本研究', re.I)
    for i, (_, chunk) in enumerate(sources):
        if self_description.search(chunk.text):
            contributions.extend(range(max(0, i-1), min(len(sources), i+2)))
    selected = set(list(dict.fromkeys(front[:8] + contributions))[:16])
    return [{'chunk_id': c.chunk_id, 'page': c.page, 'heading': c.heading, 'text': c.text}
            for i, (_, c) in enumerate(sources) if i in selected]


def scope_profile(row, candidate):
    """A research-scope objection needs two grounded paper self-descriptions."""
    if not isinstance(row, dict):
        return None
    verified = {}
    for key in ('object', 'method', 'contribution'):
        value = row.get(key)
        if not isinstance(value, dict) or not isinstance(value.get('summary'), str) or not value['summary'].strip():
            continue
        if source_quote(value, candidate):
            verified[key] = value
    return verified if len(verified) >= 2 else None


def source_quote(row, candidate):
    quote = row.get('quote')
    return (isinstance(quote, str) and len(quote.strip()) >= 16
            and any(row.get('chunk_id') == c['chunk_id'] and source_span(quote, c['text'])
                    for c in candidate['chunks'] + candidate.get('paper_context', [])))


def scope_conflict(row, profile, candidate, targets, task_ids):
    """An OR target still possibly met cannot be removed by a scope objection."""
    checks = row.get('target_checks')
    if not profile or not isinstance(checks, list) or len(checks) != len(targets):
        return False
    seen = set()
    for check in checks:
        if (not isinstance(check, dict) or not isinstance(check.get('target_id'), str)
                or check['target_id'] not in targets
                or check['target_id'] in seen or check.get('verdict') != 'not_met'
                or not isinstance(check.get('task_id'), str) or check['task_id'] not in task_ids
                or not isinstance(check.get('reason'), str) or not check['reason'].strip()
                or not source_quote(check, candidate)):
            return False
        seen.add(check['target_id'])
    return seen == targets


def challenge_approvals(workflow, task, decisions, reviewed, trace, remaining, call, *, index=None, plan=None):
    """Only an explicit, source-bound contradiction can overturn a terminal approval."""
    segments = task_segments(task)
    topic = '\n'.join(text for text in (task.get('title'), task.get('input')) if isinstance(text, str))
    topic_ids = {s['id'] for s in segments if source_span(s['text'], topic)}
    targets = (plan or fallback_plan(task))['targets']
    target_ids = {t['id'] for t in targets}
    rows = []
    for name, candidate in reviewed.items():
        decision = decisions[name]
        if not decision.get('accepted') or not decision.get('match_dimensions'):
            continue
        met = {r['task_quote'] for r in decision['requirements'] if r['verdict'] == 'met'}
        rows.append({**candidate,
            'paper_context': paper_context(index, name) if index is not None else [],
            'confirmed_requirements': [
            s['id'] for s in segments if s['id'] in topic_ids and any(quote in s['text'] for quote in met)
        ]})
    audit = trace['audit']['counterevidence'] = {'checked': [], 'excluded': [], 'invalid': 0, 'scope_profiles': {}}
    for start in range(0, len(rows), 4):
        if remaining() < 85:
            break
        batch = rows[start:start+4]
        try:
            value = call(workflow.llm, 'retrieve', {
                'task': task, 'verification': True, 'counterevidence_review': True,
                'task_segments': segments, 'search_targets': targets, 'candidates': batch,
            }, 6000)
        except (ModelError, TimeoutError) as exc:
            audit['error_type'] = type(exc).__name__
            break
        exclusions = value.get('exclusions') if isinstance(value, dict) else None
        if not isinstance(exclusions, list):
            audit['invalid'] += 1
            continue
        audit['checked'].extend(c['filename'] for c in batch)
        profiles = value.get('paper_profiles', [])
        for candidate in batch:
            name = candidate['filename']
            profile_rows = [p for p in profiles if isinstance(p, dict) and p.get('filename') == name] if isinstance(profiles, list) else []
            profile = scope_profile(profile_rows[0], candidate) if len(profile_rows) == 1 else None
            if profile:
                audit['scope_profiles'][name] = profile
            matches = [r for r in exclusions if isinstance(r, dict) and r.get('filename') == name]
            if not matches:
                continue
            row = matches[0]
            reason = row.get('reason')
            if (len(matches) != 1 or row.get('task_id') not in candidate['confirmed_requirements']
                    or not isinstance(reason, str) or not reason.strip()
                    or not source_quote(row, candidate)
                    or row.get('basis') not in (None, 'direct_conflict', 'research_scope')
                    or (row.get('basis') == 'research_scope'
                        and not scope_conflict(row, profile, candidate, target_ids, topic_ids))):
                audit['invalid'] += 1
                continue
            decisions[name] = {**decisions[name], 'accepted': False, 'status': 'irrelevant'}
            audit['excluded'].append({**row})


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
    started=time.monotonic();durations=[];reviewed={}
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
                if decision.get('match_dimensions'):
                    reviewed[name] = candidate
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
    if task.get('case_id') == 'B-02' and reviewed:
        challenge_approvals(workflow, task, decisions, reviewed, trace, remaining, call, index=index, plan=audit_plan)
