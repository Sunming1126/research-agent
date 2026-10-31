"""D-05 公共方法的轨迹回放校验；不产生答卷或替代模型源码。"""
from .d_public_review import require_close


def check_dispatch_policy(data, result):
    for kind in ('baseline', 'candidate'):
        value = result[kind]
        if value is None:
            continue
        jobs = sorted(data['jobs'], key=lambda j: (j['arrival_ms'], j['id']))
        dropped = {j['id']: j['reason'] for j in value['dropped']}
        rows = iter(value['trace'])
        free = [0] * data['servers']
        last = [None] * data['servers']
        allocated = {flow: 0 for flow in data['flow_weights']}
        waiting, consumed = [], set()
        index, now = 0, 0

        def reject(job, reason):
            if dropped.get(job['id']) != reason:
                raise ValueError(f"D-05 {kind} task {job['id']} must be dropped for {reason}, got {dropped.get(job['id'])}")
            consumed.add(job['id'])

        while now < data['horizon_ms']:
            while index < len(jobs) and jobs[index]['arrival_ms'] <= now:
                waiting.append(jobs[index]); index += 1
            for job in waiting[:]:
                if job['deadline_ms'] <= now:
                    reject(job, 'deadline'); waiting.remove(job)
            for server in range(data['servers']):
                if free[server] > now:
                    continue
                while waiting:
                    def priority(job):
                        prefix = (allocated[job['flow']] / data['flow_weights'][job['flow']],) if kind == 'candidate' else ()
                        return prefix + (job['arrival_ms'], job['id'])
                    job = min(waiting, key=priority)
                    waiting.remove(job)
                    setup = data['setup_ms'] if last[server] is not None and last[server] != job['flow'] else 0
                    start = now + setup
                    end = start + job['service_ms']
                    if start >= job['deadline_ms']:
                        reject(job, 'deadline'); continue
                    if end > data['horizon_ms']:
                        reject(job, 'horizon'); continue
                    actual = next(rows, None)
                    if actual is None or actual.get('id') != job['id'] or actual.get('server') != server:
                        raise ValueError(f"D-05 {kind} dispatch policy at time {now}, server {server}: "
                                         f"expected task {job['id']}, got {None if actual is None else actual.get('id')}; "
                                         "use allocated service/weight immediately, not completed service or task count")
                    for key, expected in (('dispatch_ms', now), ('setup_ms', setup), ('start_ms', start),
                                          ('end_ms', end), ('wait_ms', start-job['arrival_ms'])):
                        require_close(actual[key], expected, f'D-05 {kind} {job["id"]}/{key}')
                    consumed.add(job['id'])
                    allocated[job['flow']] += job['service_ms']
                    free[server], last[server] = end, job['flow']
                    break
            events = [t for t in free if t > now]
            if index < len(jobs):
                events.append(jobs[index]['arrival_ms'])
            if not events or (not waiting and index == len(jobs)):
                break
            now = min(events)
        for job in jobs:
            if job['id'] not in consumed:
                reject(job, 'horizon')
        if next(rows, None) is not None:
            raise ValueError('D-05 extra trace after public event replay')
