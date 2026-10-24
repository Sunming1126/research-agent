"""冻结真实v5反例，验证精确反馈能驱动完整源码替换而非局部手改。"""
import json
import hashlib
from pathlib import Path

import pytest

from research_agent.core.config import Settings
from research_agent.core.models import CaseContext
from research_agent.workflows.layered_d_runner import LayeredDRunner
from research_agent.workflows.sample_validation import PublicSampleError,run_public_sample

MATERIAL=Path('科研助手赛题材料/research-experiments-20261020-source-v1')
FIXTURES=Path(__file__).parent/'fixtures'
CASES=[('D-02','d02-hybrid-v5-ignored-query.py-text','query_changes_ranking','use query tokens'),
       ('D-05','d05-hybrid-v5-first-setup.py-text','provided_sample','expected_setup_ms=0, observed_setup_ms=2'),
       ('D-05','d05-hybrid-v7-reject-retry.py-text','retry_after_deadline_rejection',
        'after rejection retry the same idle server')]

@pytest.mark.parametrize('cid,filename,probe,message',CASES)
def test_unmodified_real_v5_faults_are_rejected_with_specific_counterexample(cid,filename,probe,message):
    materials={k:(MATERIAL/cid/n).read_text(encoding='utf-8') for k,n in [('protocol','protocol.json'),('sample','sample.json')]}
    with pytest.raises(PublicSampleError,match=message) as error:
        run_public_sample(FIXTURES/filename,materials,cid)
    assert error.value.feedback['probe']==probe

@pytest.mark.parametrize('cid,filename,probe,message',CASES)
def test_controlled_repair_receives_exact_failure_and_preserves_second_full_response(tmp_path,cid,filename,probe,message):
    settings=Settings(tmp_path,tmp_path/'out',material_roots=[MATERIAL.resolve()])
    good=LayeredDRunner(settings,tmp_path/'reference').prepare_implementation(cid).read_text(encoding='utf-8')
    bad=(FIXTURES/filename).read_text(encoding='utf-8')
    class FixtureLLM:
        calls=0
        def completion(self,stage,prompt,max_tokens):
            if self.calls:
                assert message in prompt and probe in prompt
                assert 'previous_source' in prompt
            content=bad if self.calls==0 else good
            self.calls+=1
            return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':content}}]}
    model=FixtureLLM();runner=LayeredDRunner(settings,tmp_path/'work',llm=model)
    case=CaseContext.from_dict(dict(case_id=cid,function='Reproduction',title='public',input='public method',
        materials=[],constraints=[],expected_output='result',output_schema_ref='Reproduction'))
    final=runner.prepare_checked_implementation(case)
    assert final.read_text(encoding='utf-8')==good and model.calls==2
    history=json.loads((runner.work/'public-selftest.json').read_text(encoding='utf-8'))['attempts']
    assert [row['status'] for row in history]==['failed','passed']


def test_frozen_v7_rejection_bug_is_deterministic_and_source_is_unchanged():
    path=FIXTURES/'d05-hybrid-v7-reject-retry.py-text'
    expected='758806f095d81dbc78b8f2818b1e7dc5b39f5224bf1723c367a32abe1a5c8a0a'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
    materials={k:(MATERIAL/'D-05'/n).read_text(encoding='utf-8') for k,n in [('protocol','protocol.json'),('sample','sample.json')]}
    feedback=[]
    for _ in range(2):
        with pytest.raises(PublicSampleError,match='after rejection retry the same idle server') as error:
            run_public_sample(path,materials,'D-05')
        feedback.append(error.value.feedback)
    assert feedback[0]==feedback[1]
    assert feedback[0]['probe']=='retry_after_deadline_rejection'
    assert 'expected_states' in feedback[0]['error'] and 'observed_states' in feedback[0]['error']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
