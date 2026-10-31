"""Check public D outputs against the published method; never create submissions."""
from collections import Counter, defaultdict
import math
import re


def tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def require_close(actual, expected, label):
    if type(actual) not in (int, float) or not math.isfinite(actual) or not math.isclose(actual, expected, rel_tol=1e-7, abs_tol=1e-8):
        raise ValueError(f"{label}: public expected={expected!r}, got={actual!r}")


def check_training_fit(data, details):
    df = Counter(word for row in data['train'] for word in set(tokens(row['text'])))
    vocabulary = sorted(df)
    if details['vocabulary'] != vocabulary:
        raise ValueError('D-01 vocabulary must be fitted to training tokens only')
    values = details['idf']
    for index, word in enumerate(vocabulary):
        value = values[word] if isinstance(values, dict) else values[index]
        require_close(value, 1 + math.log((1 + len(data['train'])) / (1 + df[word])), 'D-01 training IDF '+word)


def check_bm25(documents, queries, config, rankings):
    expected = {q['id']: {d['id']: 0.0 for d in documents} for q in queries}
    for field, multiplier in (('text', 1.0), ('title', config['title_weight'])):
        counts = [Counter(tokens(d.get(field, ''))) for d in documents]
        lengths = [sum(row.values()) for row in counts]
        average = (sum(lengths) / len(documents) if documents else 0) or 1
        df = Counter(word for row in counts for word in row)
        for query in queries:
            for doc, frequencies, length in zip(documents, counts, lengths):
                for word in tokens(query['text']):
                    frequency = frequencies[word]
                    if frequency:
                        numerator = math.log1p((len(documents) - df[word] + .5) / (df[word] + .5)) * frequency * (config['k1'] + 1)
                        denominator = frequency + config['k1'] * (1 - config['b'] + config['b'] * length / average)
                        expected[query['id']][doc['id']] += multiplier * numerator / denominator
    for query in queries:
        rows = rankings[query['id']]
        scores = expected[query['id']]
        scale = None
        for row in rows:
            target = scores[row['id']]
            if target > 1e-10 and scale is None:
                scale = row['score'] / target
                if scale <= 0:
                    raise ValueError('D-02 BM25 scaling must be positive')
            require_close(row['score'], target * (scale if scale is not None else 1), 'D-02 BM25 '+query['id']+'/'+row['id'])


def dev_rankings(raw, queries):
    if isinstance(raw, dict):
        value = raw
    elif isinstance(raw, list) and all(isinstance(row, dict) and 'query_id' in row for row in raw):
        value = {row['query_id']: row['rankings'] for row in raw}
        if len(value) != len(raw):
            raise ValueError('D-02 duplicate development query')
    elif isinstance(raw, list) and len(raw) == len(queries):
        value = {q['id']: rows for q, rows in zip(queries, raw)}
    else:
        raise ValueError('D-02 missing complete development rankings')
    if set(value) != {q['id'] for q in queries}:
        raise ValueError('D-02 development query identities differ')
    return {key: [dict(id=row[0], score=row[1]) if isinstance(row, list) else row for row in rows]
            for key, rows in value.items()}


def check_retrieval(data, protocol, result):
    queries = data['test_queries']
    check_bm25(data['documents'], queries, protocol['default_config'], result['baseline'])
    if result['candidate'] is None:
        return
    details = result['details']
    check_bm25(data['documents'], queries, details['selected'], result['candidate'])
    documents = {d['id'] for d in data['documents']}
    for row in details['search']:
        rankings = dev_rankings(row['rankings'], data['dev_queries'])
        groups = defaultdict(list)
        ordinary = []
        for query in data['dev_queries']:
            ranking = rankings[query['id']]
            ids = [item['id'] for item in ranking]
            if len(ids) != len(documents) or set(ids) != documents or ranking != sorted(ranking, key=lambda item: (-item['score'], item['id'])):
                raise ValueError('D-02 development ranking must contain all documents in score/id order')
            relevant = set(query['relevant'])
            recall = 100 * len(set(ids[:5]) & relevant) / len(relevant) if relevant else 0
            mrr = next((1 / (i + 1) for i, doc in enumerate(ids[:10]) if doc in relevant), 0)
            groups[query['group']].append((recall, mrr))
            ordinary.append((recall, mrr))
        check_bm25(data['documents'], data['dev_queries'], row['config'], rankings)
        mean = lambda values, col: sum(item[col] for item in values) / len(values) if values else 0
        metrics = row['group_metrics']
        if set(metrics['groups']) != set(groups):
            raise ValueError('D-02 groups differ from public development groups')
        per_group = []
        for group, values in groups.items():
            pair = (mean(values, 0), mean(values, 1)); per_group.append(pair)
            for key, expected in zip(('recall_at_5_percent', 'mrr_at_10'), pair):
                require_close(metrics['groups'][group][key], expected, 'D-02 group '+group+'/'+key)
        expected = {'macro_recall_at_5_percent': mean(per_group, 0), 'macro_mrr_at_10': mean(per_group, 1),
                    'worst_mrr_at_10': min((pair[1] for pair in per_group), default=0)}
        for key, value in expected.items():
            require_close(metrics[key], value, 'D-02 '+key)
        require_close(row['recall_at_5_percent'], mean(ordinary, 0), 'D-02 ordinary recall')
        require_close(row['mrr_at_10'], mean(ordinary, 1), 'D-02 ordinary MRR')


def check_schedule(data, result):
    for name in ('baseline', 'candidate'):
        value = result[name]
        if value is None:
            continue
        last_flow, last_end = {}, {}
        if value['trace'] != sorted(value['trace'], key=lambda row: (row['dispatch_ms'], row['server'])):
            raise ValueError('D-05 trace must follow dispatch time and server order')
        for row in value['trace']:
            server = row['server']
            expected = data['setup_ms'] if server in last_flow and last_flow[server] != row['flow'] else 0
            require_close(row['setup_ms'], expected, 'D-05 server flow-switch setup')
            if row['dispatch_ms'] < row['arrival_ms'] or row['dispatch_ms'] >= data['horizon_ms']:
                raise ValueError('D-05 dispatch outside arrival/window bounds')
            require_close(row['dispatch_ms'], max(last_end.get(server, 0), row['arrival_ms']), 'D-05 idle server must dispatch available work immediately')
            last_flow[server] = row['flow']
            last_end[server] = row['end_ms']
        if value['dropped'] != sorted(value['dropped'], key=lambda row: row['id']):
            raise ValueError('D-05 dropped jobs must use id order')
        if any(row.get('reason') not in {'deadline', 'horizon'} for row in value['dropped']):
            raise ValueError('D-05 dropped jobs need a protocol reason')


def review_public_result(case_id, request, result):
    if case_id == 'D-01' and result['candidate'] is not None:
        check_training_fit(request['data'], result['details'])
    elif case_id == 'D-02':
        check_retrieval(request['data'], request['protocol'], result)
    elif case_id == 'D-05':
        check_schedule(request['data'], result)
        from .d05_review import check_dispatch_policy
        check_dispatch_policy(request['data'], result)
