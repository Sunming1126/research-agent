"""Regressions for C evidence loss and D-02 baseline benchmark validation."""
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fixtures import case
from research_agent.core.models import CaseContext, Chunk
from research_agent.core.config import Settings
from research_agent.rag.retrieval import HybridRetriever, QueryPlan
from research_agent.workflows.layered_d_runner import LayeredDRunner
from research_agent.workflows.sample_validation import run_public_sample
from research_agent.workflows.survey_sources import survey_ledger, excerpt
from research_agent.integrations.prompts import instructions_for, IDEA_INSTRUCTION
from research_agent.workflows.d02_policy import source_prompt

ROOT = Path('/materials/D-02')


def materials():
    return {key: (ROOT / name).read_text(encoding='utf-8') for key, name in
            (('method','method.md'), ('protocol','protocol.json'), ('sample','sample.json'))}


def fixture_source(tmp_path, tail):
    runner = LayeredDRunner(Settings(tmp_path, tmp_path/'out'), tmp_path/'work')
    source = runner.prepare_implementation('D-02')
    source.write_text(source.read_text() + '\noriginal = run_experiment\n' + tail, encoding='utf-8')
    return source


@pytest.mark.parametrize('rank_bug', [True, False])
def test_baseline_only_checks_rank(tmp_path, rank_bug):
    tail = """def run_experiment(request):
    if request['phase'] == 'rank':
        BUG
    result = original(request)
    return {'baseline':result['baseline'], 'candidate':None, 'details':{}}
""".replace('BUG', "raise ValueError('baseline rank omitted')" if rank_bug else 'return original(request)')
    source = fixture_source(tmp_path, tail)
    if rank_bug:
        with pytest.raises(ValueError, match='baseline rank omitted'):
            run_public_sample(source, materials(), 'D-02')
    else:
        check = run_public_sample(source, materials(), 'D-02')
        assert check['rank_consistent'] and check['baseline_rank_consistent']
        assert check['candidate_present'] is False


def test_rank_corrupt_scores_cannot_pass_on_ids_alone(tmp_path):
    source = fixture_source(tmp_path, """def run_experiment(request):
    result = original(request)
    if request['phase'] == 'rank':
        for rows in result['candidate'].values():
            for row in rows: row['score'] += 1000
    return result
""")
    with pytest.raises(ValueError):
        run_public_sample(source, materials(), 'D-02')


def test_default_rank_does_not_require_dev_labels(tmp_path):
    source = fixture_source(tmp_path, """def run_experiment(request):
    if request['phase'] == 'rank' and not request['data']['dev_queries']:
        raise ValueError('rank wrongly requires dev labels')
    return original(request)
""")
    with pytest.raises(ValueError, match='rank wrongly requires dev labels'):
        run_public_sample(source, materials(), 'D-02')


def chunk(name, key, heading, text, page=2):
    return Chunk(name+key, name, page, heading, text, 'text', 'hash')


def test_survey_reserves_method_results_and_limitations_with_continuous_quotes():
    chunks = []
    for n in range(5):
        name = f'paper{n}.pdf'
        chunks += [chunk(name,'m','Method', 'We propose our model with two variants. ' * 35),
                   chunk(name,'r','Results', 'Experiments on BenchmarkX achieve 73.2 accuracy against baseline 69.1. ' * 25),
                   chunk(name,'l','Limitations', 'Limitations: computational cost increases for long inputs. ' * 25),
                   chunk(name,'q','Noise study', 'Noise sensitivity was evaluated with adversarial retrieval noise. ' * 20),
                   chunk(name,'bg','Related work', 'Prior work studies experiments results method limitations. ' * 35)]
    current = replace(CaseContext.from_dict(case('C-03')), input='阅读指定论文', title='Noise sensitivity')
    retriever = HybridRetriever(chunks)
    ledger, sources = survey_ledger(current, retriever, QueryPlan(current.input), [])
    for name in {c.filename for c in chunks}:
        rows = [e for e in ledger if e.filename == name]
        assert {'method','result','limitation'} <= {e.facet for e in rows}
        assert sum(len(e.quote) for e in rows) <= 4200
        assert any('73.2' in e.quote and '69.1' in e.quote for e in rows)
        assert any('Noise sensitivity' in e.quote for e in rows)
    originals = {c.chunk_id:c for c in sources}
    assert all(e.quote in originals[e.chunk_id].text and e.verification=='verbatim_only' for e in ledger)


def test_survey_excerpt_uses_focus_beyond_input_and_sentence_boundary():
    text = ('Unrelated background statement. ' * 35 + 'Noise sensitivity measured under fixed conditions. ' * 10)
    c = chunk('a.pdf','q','Study',text)
    quote = excerpt(c, 'Noise sensitivity', 500)
    assert 'Noise sensitivity' in quote and quote in text and len(quote) <= 500
    assert quote[0].isupper()


def test_c_prompt_has_one_consistent_target_and_no_program_appended_facts():
    text = instructions_for('report', {'task':{'function':'Survey'},'claims':[{'role':'source_context'}]})
    assert '2000至2400' in text and '1600' not in text and '2500' not in text
    assert '至少实际引用五篇' in text


def test_idea_prompt_path_is_dedicated():
    text = instructions_for('report', {'task':{'function':'Idea'},'claims':[{'role':'source_context'}]})
    from research_agent.integrations.idea_prompt import INSTRUCTION
    assert text == INSTRUCTION
    assert '2000至2400' not in text


def test_d02_prompt_distinguishes_baseline_and_benchmark_field():
    current = CaseContext.from_dict(case('D-02'))
    baseline = source_prompt(current, materials(), baseline_only=True)
    full = source_prompt(current, materials(), baseline_only=False)
    assert 'phase=rank仍必须返回且只返回{candidate:排名}' in baseline
    assert '每种配置只计算一次' in full and 'list.count' in full
    assert json.loads(materials()['protocol'])['case_id'] == 'D-02'


def test_source_audit_deduplicates_excerpts_but_preserves_semantic_claims():
    from research_agent.workflows.report_context import audit_request
    from research_agent.core.models import Evidence
    from dataclasses import asdict
    for cid in ('C-01','E-01'):
        current = CaseContext.from_dict(case(cid))
        ledger = [Evidence('ev-1', 'Continuous authorized source.', 'a.pdf', 2, 'c',
                          'Continuous authorized source.', role='source_context')]
        rows = [{**asdict(ledger[0]), 'ref':1}]
        after = audit_request(current, 'An assertion [1, p.2]', rows, ledger)
        assert 'claim' not in after['source_quotes'][0]
        assert after['source_quotes'][0]['quote'] == ledger[0].quote
        rows[0]['role'] = 'own_work'
        after = audit_request(current, 'An assertion [1, p.2]', rows, ledger)
        assert after['source_quotes'][0]['claim'] == ledger[0].claim


def test_non_e_prompt_instructions_are_byte_identical():
    old = {'__package__':'research_agent.integrations'}
    exec(Path('/results/prompts-before.py').read_text(), old)
    for stage in old['INSTRUCTIONS']:
        for function in ('Chat','Literature_Retrieval','Survey','Reproduction'):
            for role in ('source_context','own_work'):
                data = {'task':{'function':function}, 'claims':[{'role':role}]}
                assert instructions_for(stage,data) == old['instructions_for'](stage,data)


def test_real_model_omitted_rank_gets_actionable_diagnostic(tmp_path):
    source = tmp_path/'model.py'
    source.write_bytes((Path(__file__).parent/'data/d02_missing_rank_source.txt').read_bytes())
    with pytest.raises(ValueError, match='phase=rank ignored or wrong schema'):
        run_public_sample(source, materials(), 'D-02')


@pytest.mark.parametrize('remaining,expected_baseline', [(80,True),(90,True),(110,False)])
def test_d02_late_repair_requests_new_baseline_source(tmp_path,remaining,expected_baseline):
    from test_eval_20261026_regressions import setup_d, response, SOURCE
    from research_agent.core.session import CaseSession
    import time
    runner,current=setup_d(tmp_path, [])
    modes=[]
    def generate(case,**kwargs):
        modes.append(kwargs['baseline_only'])
        runner.work.mkdir(parents=True,exist_ok=True)
        p=runner.work/'implementation.py';p.write_text(SOURCE)
        (runner.work/'generation-response.json').write_text(json.dumps(response()))
        return p
    runner.prepare_generated_implementation=generate
    session=CaseSession(current.case_id,170)
    def check(*args):
        if len(modes)==1:
            session.started_at=time.monotonic()+remaining-session.timeout
            raise ValueError('public group mean mismatch')
        return {'status':'passed','candidate_present':False}
    with session.activate():runner.prepare_checked_implementation(current,tester=check)
    assert modes == [False,expected_baseline]
