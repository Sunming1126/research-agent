"""真实D02漏检回归：仅核算公开开发排名，不替模型改写算法。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from research_agent.workflows.sample_validation import PublicSampleError,run_public_sample,validate_sample_output
from research_agent.workflows.d_algorithms.d02 import run_d02
from research_agent.workflows.public_probes import public_probes

ROOT=Path('科研助手赛题材料/x新/research-experiments-20261020-source-v1/D-02')

def sample():
    request=json.loads((ROOT/'sample.json').read_text(encoding='utf-8'))
    request['protocol']=json.loads((ROOT/'protocol.json').read_text(encoding='utf-8'))
    return request

def test_original_unmodified_model_source_with_dict_unpacking_is_rejected():
    materials={k:(ROOT/n).read_text(encoding='utf-8') for k,n in [('protocol','protocol.json'),('sample','sample.json')]}
    source=Path(__file__).parent/'fixtures/d02-hybrid-v4-repeat.py-text'
    with pytest.raises(PublicSampleError,match='ordinary dev Recall') as error:
        run_public_sample(source,materials,'D-02')
    assert error.value.feedback['probe']=='provided_sample'

@pytest.mark.parametrize('encoding',['dict','array','query_records'])
def test_correct_public_metrics_accept_each_documented_dev_ranking_representation(encoding):
    request=sample();result=run_d02(request)
    for row in result['details']['search']:
        rows=row['rankings']
        pairs={q:[[r['id'],r['score']] for r in ranking] for q,ranking in rows.items()}
        if encoding=='array':row['rankings']=[pairs[q['id']] for q in request['data']['dev_queries']]
        elif encoding=='query_records':row['rankings']=[{'query_id':q,'rankings':r} for q,r in pairs.items()]
        else:row['rankings']=pairs
    assert validate_sample_output('D-02',request,result)['status']=='passed'

@pytest.mark.parametrize('field',['recall_at_5_percent','mrr_at_10','macro_mrr_at_10','worst_mrr_at_10'])
def test_metrics_cannot_be_claimed_independently_of_returned_public_rankings(field):
    request=sample();result=run_d02(request);row=result['details']['search'][0]
    metrics=row if field in ['recall_at_5_percent','mrr_at_10'] else row['group_metrics']
    metrics[field]+=0.1
    with pytest.raises(ValueError,match='inconsistent with public dev rankings'):
        validate_sample_output('D-02',request,result)

def test_unequal_group_probe_keeps_the_original_request_unchanged():
    request=sample();original=deepcopy(request)
    probes=public_probes('D-02',request)
    assert request==original
    probe=next(p for name,p,_ in probes if name=='unequal_dev_group_sizes')
    assert len(probe['data']['dev_queries'])==len(request['data']['dev_queries'])+1
    assert validate_sample_output('D-02',probe,run_d02(probe))['status']=='passed'
