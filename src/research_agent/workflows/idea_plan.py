"""E的短规划：逐字核验出处后给写作使用，失败不伪造规划或答卷。"""
from ..core.utils import source_quote_matches
from ..core.session import current_session
from ..integrations.llm import ModelError
from ..evidence import EvidenceError
from .report_context import report_task, compact_claims


def prepare_plan(workflow,case,rows):
    session=current_session()
    if session and session.remaining()<125:
        return None
    data={'task':report_task(case),'purpose':'idea_plan','claims':compact_claims(rows)}
    value=None
    try:
        value=workflow._model_step('extract_batch',data,1500,reserve=10000,minimum=1000)
        known={row.evidence_id:row for row in workflow.ledger}
        facts=value.get('source_facts')
        if not isinstance(facts,list) or not 1<=len(facts)<=4:
            raise EvidenceError('Idea plan needs one to four actual source facts')
        names=set()
        for row in facts:
            entry=known.get(row['evidence_id']) if isinstance(row,dict) and isinstance(row.get('evidence_id'),str) else None
            quote=row.get('quote') if isinstance(row,dict) else None
            if entry is None or not isinstance(quote,str) or not 12<=len(quote)<=220 or not source_quote_matches(quote,entry.quote):
                raise EvidenceError('Idea plan quote is not a continuous authorized excerpt')
            if not isinstance(row.get('meaning'),str) or not row['meaning'].strip():
                raise EvidenceError('Idea plan missing proposed interpretation')
            names.add(entry.filename)
        if len(names)<min(2,len({e.filename for e in workflow.ledger})):
            raise EvidenceError('Idea plan must ground complementary mechanisms in distinct supplied papers')
        if any(not isinstance(value.get(key),str) or not value[key].strip() for key in ('hypothesis','design','experiment')):
            raise EvidenceError('Idea plan missing proposed design or validation')
        workflow.evidence_audit['idea_plan']={'status':'source_quotes_checked','semantic_claims_verified':False,'plan':value}
        return value
    except (ModelError,EvidenceError,TimeoutError) as exc:
        workflow.evidence_audit['idea_plan']={'status':'unavailable','error':str(exc),'raw_response':value}
        return None
