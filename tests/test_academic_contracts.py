from dataclasses import replace

import pytest

from research_agent.rag.retrieval import HybridRetriever
from research_agent.workflows.d_selftest import test_plan_result as parse_test_plan
from research_agent.workflows.tasks import TaskHandlers
from test_b5_conditions import chunk, task
from test_db206 import audit_response, reply
from test_b6_retrieval import ScreeningModel, response


@pytest.mark.parametrize('fault', ['missing', 'one_dimension', 'wrong_source', 'extra_dimension', 'none'])
def test_b02_final_review_owns_admission(fault):
    def respond(data):
        rows = []
        for candidate in data['candidates']:
            if not data.get('verification'):
                rows.append(response(candidate))
                continue
            if fault == 'missing':
                continue
            row = audit_response(candidate)
            evidence = row['requirements'][0]['evidence_ids'][0]
            row['match_dimensions'] = {'object': [evidence]}
            if fault != 'one_dimension':
                row['match_dimensions']['method'] = ['unrelated-id' if fault == 'wrong_source' else evidence]
            if fault == 'extra_dimension':
                row['match_dimensions']['finding'] = ['unrelated-id']
            rows.append(row)
        return {'papers': rows}

    workflow = TaskHandlers(ScreeningModel(respond), 'test')
    workflow.document_expander = None
    request = replace(task(), case_id='B-02')
    result = workflow.handle(request, HybridRetriever([chunk(
        'study.pdf', 'We measure urban cooling using satellite remote sensing in an original experiment.'
    )]))
    assert [p['filename'] for p in result['papers']] == (['study.pdf'] if fault in {'none', 'extra_dimension'} else [])
    if fault == 'extra_dimension':
        assert set(workflow.retrieval_trace['decisions']['study.pdf']['match_dimensions']) == {'object', 'method'}


def test_d_duplicate_inputs_cannot_inflate_or_contradict_coverage():
    materials = {'method': 'Return the requested value unchanged for each legal input.'}
    row = {'name': 'base', 'material_id': 'method:1', 'derivation': 'Copy value',
           'request': {'phase': 'base', 'value': 1}, 'expected': 1}
    repeated = {**row, 'name': 'duplicate', 'request': {'value': 1, 'phase': 'base'}}
    other = {**row, 'name': 'other', 'request': {'phase': 'other', 'value': 1}}
    duplicate = parse_test_plan(reply({'tests': [row, repeated, other]}), materials)
    assert [t['name'] for t in duplicate['tests']] == ['base', 'other']
    assert duplicate['rejected'] == ['duplicate_request']
    conflict = parse_test_plan(reply({'tests': [row, {**repeated, 'expected': 2}, row, other]}), materials)
    assert [t['name'] for t in conflict['tests']] == ['other']
    assert conflict['rejected'] == ['conflicting_oracle', 'conflicting_oracle']


def test_b02_repairs_one_contradictory_terminal_review():
    def respond(data):
        rows = []
        for candidate in data['candidates']:
            if not data.get('verification'):
                rows.append(response(candidate))
                continue
            row = audit_response(candidate)
            evidence = row['requirements'][0]['evidence_ids'][0]
            row['match_dimensions'] = {'object': [evidence], 'method': [evidence]}
            if not candidate.get('audit_feedback'):
                row['requirements'].append(
                    {'task_id': 'q2', 'verdict': 'not_met', 'evidence_ids': [evidence]})
            rows.append(row)
        return {'papers': rows}

    workflow = TaskHandlers(ScreeningModel(respond), 'test')
    workflow.document_expander = None
    request = replace(task(), case_id='B-02')
    result = workflow.handle(request, HybridRetriever([chunk(
        'study.pdf', 'We measure urban cooling using satellite remote sensing in an original experiment.'
    )]))
    assert [p['filename'] for p in result['papers']] == ['study.pdf']
    assert workflow.retrieval_trace['audit']['correction_candidates'] == ['study.pdf']


def test_b02_removes_only_source_bound_counterevidence():
    quote = 'Remote sensing appears only as a baseline.'

    def respond(data):
        if data.get('counterevidence_review'):
            exclusions = []
            for candidate in data['candidates']:
                chunk_id = candidate['chunks'][0]['chunk_id']
                exclusion = {'filename': candidate['filename'], 'task_id': 'q2',
                             'chunk_id': chunk_id, 'quote': quote, 'reason': '仅作对照。'}
                if candidate['filename'] == 'invented.pdf':
                    exclusion['quote'] = 'The paper contains this invented sentence.'
                exclusions.append(exclusion)
            return {'exclusions': exclusions}
        rows = []
        for candidate in data['candidates']:
            row = audit_response(candidate) if data.get('verification') else response(candidate)
            if data.get('verification'):
                row['requirements'][0]['task_quote'] = 'remote sensing'
                ids = row['requirements'][0]['evidence_ids']
                row['match_dimensions'] = {'object': ids, 'method': ids}
            rows.append(row)
        return {'papers': rows}

    workflow = TaskHandlers(ScreeningModel(respond), 'test')
    workflow.document_expander = None
    request = replace(task(), case_id='B-02')
    result = workflow.handle(request, HybridRetriever([
        chunk('baseline-only.pdf', 'Urban cooling. ' + quote),
        chunk('invented.pdf', 'We measure urban cooling using satellite remote sensing.'),
    ]))
    assert [p['filename'] for p in result['papers']] == ['invented.pdf']
    review = workflow.retrieval_trace['audit']['counterevidence']
    assert [row['filename'] for row in review['excluded']] == ['baseline-only.pdf']
    assert review['invalid'] == 1
