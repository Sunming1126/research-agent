"""B 专用缺口补检：查询只用于找原文，不能充当相关性证据。"""
from .b_conditions import fallback_plan
from .b_evidence import channels, recall
from ..integrations.llm import ModelError, ModelQuotaError


def recovery_plan(task, previous, decisions, llm, call, remaining, trace):
    # 空结果时重新解释原题，不继承可能被加强的必要条件。
    plan = fallback_plan(task)
    plan['queries'] = list(previous.get('queries', []))
    info = trace['query_recovery'] = {'state': 'original_task', 'queries': []}
    if remaining() < 180:
        info['state'] = 'insufficient_time'
        return plan
    from .b_audit import task_segments
    segments = task_segments(task)
    allowed = {row['id'] for row in segments}
    observations = [{'status': row['status'], 'reason': row.get('reason', '')[:180]}
                    for row in list(decisions.values())[:12]]
    try:
        value = call(llm, 'query', {'task': task, 'recovery': True,
                     'task_segments': segments, 'previous_queries': plan['queries'],
                     'observations': observations}, 1800)
        rows = value.get('recovery_queries') if isinstance(value, dict) else None
        if not isinstance(rows, list) or not 1 <= len(rows) <= 6:
            raise ModelError('B recovery requires bounded queries')
        queries = []
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get('task_id'), str)
                    or row['task_id'] not in allowed or not isinstance(row.get('query'), str)
                    or not 2 <= len(row['query'].strip()) <= 240):
                continue
            queries.append(row['query'].strip())
        if not queries:
            raise ModelError('B recovery queries not bound to original task')
        plan['queries'] = list(dict.fromkeys(queries + plan['queries']))[:8]
        info.update(state='replanned', queries=queries)
    except (ModelError, TimeoutError) as exc:
        info.update(state='unavailable', error_type=type(exc).__name__)
        if isinstance(exc, ModelQuotaError):
            info['quota_exhausted'] = True
    return plan


def rescue_candidates(index, task, plan, seen, limit=8):
    # 新查询能改变候选名单；不扩大授权材料范围，也不按文件名猜答案。
    return [row for row in recall(index, channels(task, plan), min(256, len(index.documents)))
            if row['filename'] not in seen][:limit]
