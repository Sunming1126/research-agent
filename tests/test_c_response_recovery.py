"""Whole-response evidence faults cannot be repaired by paragraph-only patches."""
import re

import pytest

from research_agent.core.models import CaseContext, Evidence
from research_agent.evidence import EvidenceError
from research_agent.workflows.tasks import TaskHandlers
from fixtures import case


class RecoveryModel:
    """Deterministic protocol fixture; never contacts a model service."""
    def __init__(self, fault=None, repeat_fault=False):
        self.fault=fault;self.repeat_fault=repeat_fault;self.stages=[];self.report_calls=0

    def json(self, stage, data, **kwargs):
        self.stages.append(stage)
        if stage=='report':
            self.report_calls+=1
            text=('# 离线合成材料比较\n\n'+
                  '这些材料仅为软件测试夹具，讨论研究对象、观察条件与适用边界，不代表真实论文结论。'*28+
                  '\n\n'+''.join(f'[E:ev-{i}]' for i in range(1,6)))
            used=[f'ev-{i}' for i in range(1,6)]
            if self.fault and (self.report_calls==1 or self.repeat_fault):
                if self.fault in {'empty_ids','unknown_ids'}:
                    # Numeric references are legal, but metadata must still be selected by the model.
                    text=re.sub(r'\[E:ev-(\d+)\]',lambda m:f'[{m[1]}, p.1]',text)
                    used=[] if self.fault=='empty_ids' else ['ev-999']
                elif self.fault=='unknown_binding':
                    text+='\n\n[E:ev-999] [E:ev-999]'
                elif self.fault=='bound_ids_without_metadata':
                    used=[]
            return {'markdown':text,'used_evidence_ids':used}
        if stage=='repair':
            # A paragraph repair cannot update used_evidence_ids in this protocol.
            # The patch is valid and uniquely matches the title; the failure must
            # come from unchanged evidence metadata, not an ambiguous patch.
            return {'patches':[{'original':'# 离线合成材料比较','replacement':'# 离线合成材料的比较'}]}
        if stage=='audit':return {'ok':True,'issues':[]}
        raise AssertionError('Unexpected model stage '+stage)


def context(model):
    handler=TaskHandlers(model,'synthetic-workspace')
    handler._source_report=True
    handler.ledger=[Evidence(f'ev-{i}','仅为离线测试而构造的原文片段。',f'Study_{i}.pdf',1,
                             f'chunk-{i}','仅为离线测试而构造的原文片段。',
                             kind='source_context',role='source_context',verification='verbatim_only')
                    for i in range(1,6)]
    task=CaseContext.from_dict(case('C-01',[f'Study_{i}.pdf' for i in range(1,6)]))
    return handler,task


@pytest.mark.parametrize('fault',['empty_ids','unknown_ids','unknown_binding'])
def test_invalid_evidence_response_gets_one_complete_regeneration(fault):
    model=RecoveryModel(fault);handler,task=context(model)
    result=handler.compose_report(task)
    assert result['case_id']=='C-01'
    assert '[E:' not in result['markdown'] and 'ev-999' not in result['markdown']
    assert all(f'[{i}, p.1]' in result['markdown'] for i in range(1,6))
    assert model.report_calls==2 and 'repair' not in model.stages
    assert len(handler.report_trace)==2


def test_valid_bound_citations_do_not_require_extra_generation():
    model=RecoveryModel('bound_ids_without_metadata');handler,task=context(model)
    result=handler.compose_report(task)
    assert result['case_id']=='C-01' and model.report_calls==1
    assert model.stages==['report','audit']


def test_two_invalid_full_responses_are_rejected_with_original_evidence_retained():
    model=RecoveryModel('empty_ids',repeat_fault=True);handler,task=context(model)
    with pytest.raises(EvidenceError):handler.compose_report(task)
    assert sum(stage in {'report','repair'} for stage in model.stages)<=2
    assert len(handler.ledger)==5 and handler.audit['ok'] is False
