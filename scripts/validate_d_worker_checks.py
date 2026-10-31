"""Validate actual public-worker outputs; local method checks are not official scores."""
import json
from pathlib import Path
from research_agent.workflows.sample_validation import validate_sample_output
from research_agent.workflows.d_public_review import review_public_result
from research_agent.workflows.d_algorithms.d01 import run_d01
from research_agent.workflows.d_algorithms.d02 import run_d02
from research_agent.workflows.d_algorithms.d05 import run_d05

root=Path('/results')
rows=[];rank_count=0
for path in sorted(root.glob('D-*.json')):
    row=json.loads(path.read_text())
    case_id,request,result=row['case_id'],row['request'],row['result']
    assert row['returncode']==0 and result['candidate'] is not None,path.name
    validate_sample_output(case_id,request,result)
    review_public_result(case_id,request,result)
    reference={'D-01':run_d01,'D-02':run_d02,'D-05':run_d05}[case_id](request)
    if case_id=='D-02':
        ids=lambda rankings:{q:[item['id'] for item in values] for q,values in rankings.items()}
        for part in ('baseline','candidate'):
            assert ids(result[part])==ids(reference[part]),(path.name,part)
        assert result['details']['selected']==reference['details']['selected'],path.name
        assert row['rank_returncode']==0 and ids(row['rank_result']['candidate'])==ids(result['candidate']),path.name
        rank_count+=1
    else:
        for part in ('baseline','candidate'):
            assert result[part]==reference[part],(path.name,part)
    rows.append({'case_id':case_id,'scenario':row['scenario'],'source_sha256':row['source_sha256'],
                 'worker_sha256':row['worker_sha256'],'python':row['python'],'passed':True})
summary={'all_passed':True,'run_cases':len(rows),'rank_cases':rank_count,'cases':rows,
         'scope':'unmodified public worker, public/derived inputs, local method checks; not hidden evaluation'}
assert len(rows)==17 and rank_count==7
(root/'final-validation.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print('17 public/derived run cases + 7 independent rank cases passed')
