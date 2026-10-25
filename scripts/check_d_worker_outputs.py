"""Check public-worker outputs against structural/conservation invariants, not scores."""
import json
from pathlib import Path
from research_agent.workflows.sample_validation import validate_sample_output

root = Path('/materials')
audit = Path('/audit')
rows = json.loads((audit/'official-worker/summary.json').read_text())
for row in rows:
    request = json.loads((root/row['request']).read_text())
    label = row['case_id']+'-'+Path(row['request']).stem
    value = json.loads((audit/'official-worker'/(label+'.stdout.json')).read_text())
    if request['stage'] == 'rank':
        # The supplied default rank fixture has no fitted candidate selection.
        data = request['payload']['data']
        ids = {d['id'] for d in data['documents']}
        assert set(value['candidate']) == {q['id'] for q in data['test_queries']}
        for ranking in value['candidate'].values():
            assert len(ranking)==len(ids) and {r['id'] for r in ranking} == ids
            assert ranking == sorted(ranking,key=lambda r:(-r['score'],r['id']))
        row['invariants'] = 'default rank completeness and ordering'
    else:
        row['invariants'] = validate_sample_output(row['case_id'], request['payload'],value)
(audit/'official-worker/validated-summary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
print('All '+str(len(rows))+' public worker outputs passed structural/conservation checks; no formal score claimed.')
