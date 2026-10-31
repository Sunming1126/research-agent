"""从公开方法构造诊断输入；不包含隐藏实例、冻结答案或实测数值。"""
from copy import deepcopy


def boundary_cases(case_id, request):
    if case_id == 'D-02':
        # 大于两个截断位置，标题与正文冲突，不等组；小样例上退化的选参在此显现。
        data = {'documents': [], 'dev_queries': [], 'test_queries': []}
        for i in range(16):
            data['documents'].append({'id': f'd{i:02}',
                'title': ('alpha ' * (1+i%3)) if i%2 else 'beta',
                'text': ('beta ' * (1+i%4) if i%2 else 'alpha ') + 'filler ' * (i*i)})
        for i, (term, group, relevant) in enumerate([
                ('alpha', 'navigation', ['d01','d03','d05']),
                ('beta', 'navigation', ['d00','d02','d04']),
                ('alpha alpha beta', 'topic', ['d00','d02','d06']),
                ('beta', 'topic', ['d07','d09','d11']),
                ('alpha beta beta', 'topic', ['d01','d08']),
                ('unknown9841', 'topic', ['d15'])]):
            data['dev_queries'].append(dict(id='dev'+str(i), text=term, group=group, relevant=relevant))
            data['test_queries'].append(dict(id='test'+str(i), text=term, group=group))
        yield 'field_conflict_cutoffs_unequal_groups', dict(request, data=data)
        empty = deepcopy(request)
        for d in empty['data']['documents']:
            d['text'] = ''
        yield 'empty_body_independent_title', empty
    elif case_id == 'D-05':
        def job(key, flow, arrival, service, deadline=100):
            return dict(id=key, flow=flow, arrival_ms=arrival, service_ms=service, deadline_ms=deadline)
        scenarios = [
            ('weighted_service_not_job_count', dict(servers=2, setup_ms=.5, horizon_ms=16,
                flow_weights={'A':1,'B':3,'zero':2}, jobs=[
                    job('a','A',0,3),job('b','B',0,1),job('c','A',0,1),
                    job('d','B',0,5),job('e','B',1,2),job('f','A',2,2),job('g','B',3,1)])),
            ('idle_flow_setup_deadline_retry', dict(servers=1, setup_ms=2, horizon_ms=12,
                flow_weights={'A':1,'B':2}, jobs=[job('a','A',0,1),job('b','B',4,1,6),
                job('c','A',4,1),job('d','B',5,5),job('e','A',12,1)])),
            ('three_servers_expiry_simultaneous', dict(servers=3, setup_ms=1, horizon_ms=6,
                flow_weights={'A':.5,'B':1.5}, jobs=[job('a','A',0,2),job('b','B',0,2),
                job('c','A',0,2),job('d','B',2,1,2),job('e','B',2,3),job('f','A',6,1)])),
            ('deadline_before_horizon_rejection', dict(servers=1, setup_ms=3, horizon_ms=6,
                flow_weights={'A':1,'B':1}, jobs=[job('a','A',0,2),job('b','B',2,5,4),
                job('c','A',2,4),job('d','B',20,1)])),
        ]
        for name, data in scenarios:
            yield name, dict(request, data=data)
