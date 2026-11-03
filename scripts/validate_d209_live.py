"""Fresh developer-authored contract; no official task, metric or submission."""
import copy
import json
import time
from pathlib import Path
from research_agent.core.config import Settings
from research_agent.core.models import CaseContext
from research_agent.core.session import CaseSession
from research_agent.integrations.llm import GatewayLLM
from research_agent.workflows.layered_d_runner import LayeredDRunner
from research_agent.workflows.d_selftest import run_tests

METHOD = '''Implement grouped event-window aggregation. Input is an object with phase equal to bucket or window, events a list of {group:string,time:integer,value:number or null,weight:nonnegative number optional default 1}. Empty events is legal. Do not mutate input. First combine events by exact (group,time). A null value or zero weight contributes nothing. Each bucket's value is sum(value*weight)/sum(weight) across usable events, or null if no usable events. Its weight is the sum of usable weights (zero if none). Preserve buckets even when value is null. Group keys sort lexicographically and times numerically ascending. Bucket phase returns exactly {groups:[{group:string,points:[{time:int,value:number or null,weight:number}]}]}. Window phase additionally requires a nonnegative integer horizon and returns the SAME schema, with each point's value and weight recomputed from that group's ORIGINAL buckets whose times are in the CLOSED interval [point.time-horizon,point.time]. The output weight is the sum of these original bucket weights. The output value is the weighted mean of original bucket values using their bucket weights; skip null values and zero weights. A window with no usable weight returns null and zero. Never feed a previous output window back into another. No cross-group borrowing, no timestamp insertion, no future events, and do not count duplicate time buckets multiple times. Horizon zero uses only the current bucket. Zero is a valid value; null is missing.'''


def reference(q):
    groups = {}
    for row in q['events']:
        point = groups.setdefault(row['group'], {}).setdefault(row['time'], [0, 0])
        weight = row.get('weight', 1)
        if row['value'] is not None and weight > 0:
            point[0] += row['value'] * weight
            point[1] += weight
    result = []
    for group, buckets in sorted(groups.items()):
        points = []
        for t in sorted(buckets):
            selected = [v for s, v in buckets.items() if (s == t if q['phase'] == 'bucket' else t-q['horizon'] <= s <= t)]
            numerator = sum(v[0] for v in selected)
            denominator = sum(v[1] for v in selected)
            points.append({'time': t, 'value': numerator/denominator if denominator else None, 'weight': float(denominator)})
        result.append({'group': group, 'points': points})
    return {'groups': result}


EVENTS = [
    {'group': 'b', 'time': 4, 'value': 20},
    {'group': 'a', 'time': 3, 'value': 8, 'weight': 2},
    {'group': 'a', 'time': 1, 'value': 0},
    {'group': 'a', 'time': 3, 'value': 2},
    {'group': 'a', 'time': 2, 'value': None, 'weight': 9},
    {'group': 'a', 'time': 4, 'value': 100, 'weight': 0},
    {'group': 'a', 'time': 5, 'value': 4},
]
REQUESTS = [{'phase': 'bucket', 'events': EVENTS}]
REQUESTS += [{'phase': 'window', 'events': EVENTS, 'horizon': h} for h in (0, 1, 2, 20)]
REQUESTS += [
    {'phase': 'window', 'events': [], 'horizon': 0},
    {'phase': 'bucket', 'events': [{'group': 'x', 'time': -2, 'value': None}]},
    {'phase': 'window', 'horizon': 2, 'events': [{'group': 'x', 'time': -2, 'value': -2}, {'group': 'x', 'time': 0, 'value': 4, 'weight': 3}]},
]


def main():
    root = Path('/audit/D-02'); root.mkdir(parents=True, exist_ok=True)
    (root/'method.md').write_text(METHOD)
    (root/'protocol.json').write_text(json.dumps({'entrypoint': 'run_experiment(request)', 'python': '3.9 standard library only'}))
    (root/'sample.json').write_text(json.dumps({'phase': 'bucket', 'events': []}))
    tests = [{'name': f'heldout-{i}', 'request': copy.deepcopy(q), 'expected': reference(q)} for i, q in enumerate(REQUESTS)]
    (root/'frozen-heldout.json').write_text(json.dumps(tests, indent=2))
    case = CaseContext.from_dict({'case_id': 'D-02', 'function': 'Reproduction', 'title': 'Self-authored grouped windows', 'input': 'Implement the current method exactly', 'materials': [], 'constraints': [], 'expected_output': 'current contract', 'output_schema_ref': 'Reproduction'})
    llm = GatewayLLM(token_budget=float('inf'), timeout=1780)
    runner = LayeredDRunner(Settings(root, root/'out'), root/'work', llm)
    start = time.monotonic()
    with CaseSession('D-02', 1780).activate():
        path = runner.prepare_checked_implementation(case)
        initial = run_tests(path.read_text(), tests)
        path = runner.review_implementation(case, path)
        final = run_tests(path.read_text(), tests)
    result = {'initial': initial, 'final': final, 'review': json.loads((root/'work/source-review.json').read_text()), 'usage': llm.usage, 'seconds': round(time.monotonic()-start, 2)}
    (root.parent/'results.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({'initial_passed': sum(r['passed'] for r in initial['results']), 'final_passed': sum(r['passed'] for r in final['results']), 'tests': len(tests), 'seconds': result['seconds'], 'review': result['review']}), flush=True)


if __name__ == '__main__':
    main()
