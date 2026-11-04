"""User-provided ce-quality-v3 boundaries, not hidden benchmark answers."""
import pytest

from research_agent.contracts.validation import report_citations, validate_report_limits
from research_agent.integrations.idea_prompt import INSTRUCTION, REPAIR_INSTRUCTION
from research_agent.integrations.survey_prompt import REPAIR_INSTRUCTION as SURVEY_REPAIR


@pytest.mark.parametrize('function,count,allowed', [
    ('Survey', 4500, True), ('Survey', 4501, True), ('Survey', 5000, True), ('Survey', 5001, False),
    ('Idea', 4499, True), ('Idea', 4500, True), ('Idea', 4501, False), ('Idea', 6000, False),
])
def test_official_body_boundaries(function, count, allowed):
    text = '文' * count
    if allowed:
        validate_report_limits(text, function)
    else:
        with pytest.raises(ValueError):
            validate_report_limits(text, function)


@pytest.mark.parametrize('function,maximum', [('Survey', 5000), ('Idea', 4500)])
def test_heading_and_reference_section_are_outside_body_limit(function, maximum):
    body = '文' * maximum
    text = '# ' + '题' * 300 + '\n\n' + body + '\n\n## 参考文献\n[1] ' + '参考' * 4000
    validate_report_limits(text, function)


@pytest.mark.parametrize('count,allowed', [(39, True), (40, True), (41, False)])
def test_grouped_numeric_citation_is_one_marker(count, allowed):
    body = '已核实的论文事实。' * 100 + '[1,2]' * count
    if allowed:
        validate_report_limits(body, 'Survey')
    else:
        with pytest.raises(ValueError):
            validate_report_limits(body, 'Survey')


def test_marker_count_must_not_erase_reference_identity():
    assert report_citations('事实[1,2]，机制[3, p.4]。') == ['1', '2', '3']


@pytest.mark.parametrize('marker', ['[1]', '[1, p.4]', '[1, 2, p.4]'])
def test_forty_one_single_or_group_markers_are_rejected(marker):
    with pytest.raises(ValueError):
        validate_report_limits('文' * 900 + marker * 41, 'Survey')


def test_hash_without_heading_space_is_counted():
    with pytest.raises(ValueError):
        validate_report_limits('#' + '文' * 5000, 'Survey')


def test_heading_like_text_inside_code_still_counts():
    with pytest.raises(ValueError):
        validate_report_limits('```text\n# ' + '文' * 5000 + '\n```', 'Survey')


def test_e_prompt_limits_are_not_larger_than_official_contract():
    assert '4500' in INSTRUCTION and '6000' not in INSTRUCTION
    assert '4500' in REPAIR_INSTRUCTION and '6000' not in REPAIR_INSTRUCTION


def test_c_repair_uses_current_contract():
    assert '5000' in SURVEY_REPAIR and '4500' not in SURVEY_REPAIR


def test_e_overlong_first_draft_is_regenerated_not_delivered():
    from test_e4_resilience import BODY, good_audit, run
    from fixtures import case
    from research_agent.core.models import CaseContext
    from research_agent.core.session import CaseSession
    workflow, model = run([{'markdown': '拟议机制。' * 920 + '[E:ev-1]'}, {'markdown': BODY}, good_audit()])
    with CaseSession('E-04', 170).activate():
        result = workflow.compose_report(CaseContext.from_dict(case('E-04')))
    assert model.stages == ['report', 'report', 'audit']
    assert BODY.replace('[E:ev-1]', '[1, p.1]') in result['markdown']


def test_e_two_oversized_drafts_never_become_success():
    from test_e4_resilience import run
    from fixtures import case
    from research_agent.core.models import CaseContext
    from research_agent.evidence import EvidenceError
    oversized = {'markdown': '拟议机制。' * 920 + '[E:ev-1]'}
    workflow, model = run([oversized, oversized])
    with pytest.raises(EvidenceError):
        workflow.compose_report(CaseContext.from_dict(case('E-04')))
    assert model.stages == ['report', 'report']
