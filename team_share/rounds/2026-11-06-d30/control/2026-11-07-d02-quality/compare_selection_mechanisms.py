"""Codex offline: fixed, synthetic selection-gate comparison; no model calls.

Loads exact function ASTs from three Git revisions. The mock programs below are
tiny constant functions, not competition algorithms. This checks a decision gate,
not Linux isolation, an official task, or leaderboard score.
"""
import ast
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / '协同开发/research-agent-d-contract-20261106'
OUT = Path(__file__).resolve().parent
REFS = ['046324b', 'dc9ef0f', '2e6f51a']
SELFTEST = 'src/research_agent/workflows/d_selftest.py'
REVIEW = 'src/research_agent/workflows/d_review.py'
MATERIALS = {'method': 'For a legal run request, return request.x exactly. For a legal rank request, return request.x + 10. Do not substitute a constant.'}
BASE = 'def run_experiment(request):\n    return request["x"]\n'
BAD = 'def run_experiment(request):\n    return request["x"] + 10 if request["phase"] == "rank" else 0\n'


def sha(value):
    return hashlib.sha256(value).hexdigest()


def git(*args):
    return subprocess.check_output(['git', '-c', f'safe.directory={REPO}', '-C', str(REPO), *args])


def load_functions(text, names, env):
    tree = ast.parse(text)
    selected = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == set(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), '<exact-git-function-asts>', 'exec'), env)


def response(rows):
    return {'choices': [{'finish_reason': 'stop', 'message': {
        'role': 'assistant', 'content': json.dumps({'tests': rows})}}]}


def row(name, x, expected):
    return {'name': name, 'derivation': 'synthetic diagnostic oracle', 'material_id': 'method:1',
            'request': {'phase': 'run', 'x': x}, 'expected': expected}


def evaluate(source, tests, same_result):
    if not tests:
        return {'state': 'no_tests', 'results': []}
    code = {}
    exec(compile(source, '<fixed-synthetic-program>', 'exec'), code)
    values = []
    for test in tests:
        actual = code['run_experiment'](test['request'])
        values.append({'name': test['name'], 'passed': same_result(actual, test['expected']),
                       'actual': actual, 'expected': test['expected']})
    return {'state': 'tested', 'results': values}


@contextmanager
def no_model_generation_window(*args, **kwargs):
    yield


def json_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def actual_review_method(ref, source_text, plan_rows, scenario):
    """Exact review_implementation AST with explicit synthetic dependencies.

    The only execution substitution is d_selftest.run_tests, using the two fixed
    in-process toy programs above. File writes go to this audit directory. No
    source-generation service, source credential, official run, or Linux worker.
    """
    prefix = '_d02_gate_' + ref + '_' + scenario
    for package in (prefix, prefix+'.workflows', prefix+'.core', prefix+'.integrations'):
        module = types.ModuleType(package); module.__path__ = []
        sys.modules[package] = module
    def mod(name, env=None):
        module = types.ModuleType(prefix+'.'+name)
        if env: module.__dict__.update(env)
        sys.modules[module.__name__] = module
        return module
    session = types.SimpleNamespace(remaining=lambda *a, **k: 3000)
    mod('core.session', {'current_session': lambda: session})
    mod('integrations.llm', {'ModelError': type('SyntheticModelError', (RuntimeError,), {})})
    mod('workflows.d_generation_policy', {'generation_window': no_model_generation_window})
    sg = mod('workflows.source_generation')
    exec(git('show', f'{ref}:src/research_agent/workflows/source_generation.py').decode(), sg.__dict__)
    sg.read_generation_materials = lambda *a, **k: MATERIALS
    dr = mod('workflows.d_review')
    exec(git('show', f'{ref}:{REVIEW}').decode(), dr.__dict__)
    ds = mod('workflows.d_selftest')
    exec(git('show', f'{ref}:{SELFTEST}').decode(), ds.__dict__)
    ds.run_tests = lambda source, tests: evaluate(source, tests, ds.same_result)
    for name in ('d_refinement', 'd_holdout'):
        module = mod('workflows.'+name)
        exec(git('show', f'{ref}:src/research_agent/workflows/{name}.py').decode(), module.__dict__)
    work = OUT/'synthetic-review-method'/ref/scenario
    work.mkdir(parents=True, exist_ok=True)
    implementation = work/'implementation.py'
    receipt = work/'generation-response.json'
    implementation.write_text(BASE, encoding='utf-8')
    receipt.write_text(json.dumps({'origin':'synthetic toy original'}), encoding='utf-8')
    fake_calls = []
    class Model:
        budget = 10**9
        total_tokens = 0
        supports_source_reports = False
        def completion(self, stage, prompt, max_tokens):
            fake_calls.append(stage)
            if stage == 'd_tests':
                return response(plan_rows)
            assert stage == 'd_review'
            # Genuine toy-rule defect: the original returns x for rank, but the
            # declared rule requires x+10. This does not bless the wrong oracle.
            content = {'decision':'repair', 'defects':[{'material_id':'method:1',
                'source_quote':'return request["x"]',
                'explanation':'The rank phase incorrectly returns x rather than x+10.',
                'counterexample':'A legal rank request with x=1 must return 11, while this code returns 1.'}]}
            return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(content)}}]}
    runner = types.SimpleNamespace(work=work, llm=Model(),
        settings=types.SimpleNamespace(workspace=OUT, material_roots=[]))
    def toy_revision(*args, **kwargs):
        fake_calls.append('synthetic_revision')
        implementation.write_text(BAD, encoding='utf-8')
        receipt.write_text(json.dumps({'origin':'synthetic toy revision'}), encoding='utf-8')
        return implementation
    runner.prepare_generated_implementation = toy_revision
    env = {'__name__':prefix+'.workflows.layered_d_runner', '__package__':prefix+'.workflows',
           'current_session': lambda: session, 'confined': lambda root, rel: root/rel,
           'json':json, 'write_json':json_file, 'generation_window':no_model_generation_window,
           'read_generation_materials':sg.read_generation_materials,
           'ModelError':sys.modules[prefix+'.integrations.llm'].ModelError}
    load_functions(source_text, ['review_implementation'], env)
    env['review_implementation'](runner, types.SimpleNamespace(case_id='D-02'), implementation)
    audit = json.loads((work/'source-review.json').read_text())
    selected_source = implementation.read_text(encoding='utf-8')
    namespace = {}
    exec(selected_source, namespace)
    return {'origin':'mechanism simulation, not Linux or official experiment',
            'audit':audit, 'synthetic_calls':fake_calls, 'target_model_calls':0,
            'selected_source_sha256':sha(selected_source.encode()),
            'selected_run_value':namespace['run_experiment']({'phase':'run','x':1}),
            'selected_rank_value':namespace['run_experiment']({'phase':'rank','x':1})}


scenarios = {
    'valid_test_detects_regression': [row('valid', 1, 1)],
    'empty_test_plan': [],
    'conflicting_oracles_only': [row('valid', 1, 1), row('wrong', 1, 2)],
    'conflict_plus_independent_valid': [row('valid', 1, 1), row('wrong', 1, 2), row('independent', 2, 2)],
    'duplicate_same_oracle': [row('valid', 1, 1), row('duplicate', 1, 1)],
    'both_implementations_fail_all_tests': [row('wrong', 1, 2)],
}

sources = {}
variants = {}
for ref in REFS:
    commit = git('rev-parse', ref).decode().strip()
    source = git('show', f'{commit}:{SELFTEST}')
    review = git('show', f'{commit}:{REVIEW}')
    env = {'json': json, 'math': math, 're': re}
    load_functions(review.decode(), ['response_json', 'bound_quote', 'material_segments', 'material_reference'], env)
    load_functions(source.decode(), ['test_plan_result', 'same_result', 'preserves_tests'], env)
    per_scenario = {}
    for name, rows in scenarios.items():
        plan = env['test_plan_result'](response(rows), MATERIALS)
        before = evaluate(BASE, plan['tests'], env['same_result'])
        after = evaluate(BAD, plan['tests'], env['same_result'])
        per_scenario[name] = {'received': plan['received'], 'accepted_tests': len(plan['tests']),
                             'rejected': plan['rejected'], 'before': before, 'after': after,
                             'preserves_tests': env['preserves_tests'](before, after)}
    per_scenario['unavailable_before_and_after'] = {'preserves_tests': env['preserves_tests'](
        {'state': 'worker_failed', 'results': []}, {'state': 'worker_failed', 'results': []})}
    variants[ref] = {'commit': commit, 'cases': per_scenario}
    sources[ref] = {SELFTEST: sha(source), REVIEW: sha(review)}
    runner_path = 'src/research_agent/workflows/layered_d_runner.py'
    runner_source = git('show', f'{commit}:{runner_path}')
    sources[ref][runner_path] = sha(runner_source)
    variants[ref]['actual_review_method_simulations'] = {
        name: actual_review_method(ref, runner_source.decode(), scenarios[name], name)
        for name in ('valid_test_detects_regression', 'empty_test_plan', 'conflicting_oracles_only',
                     'conflict_plus_independent_valid', 'duplicate_same_oracle')}

assert variants['046324b']['cases']['conflicting_oracles_only']['preserves_tests'] is False
assert variants['dc9ef0f']['cases']['conflicting_oracles_only']['preserves_tests'] is True
assert variants['2e6f51a']['cases']['conflicting_oracles_only']['preserves_tests'] is True
for variant in variants.values():
    cases = variant['cases']
    assert cases['valid_test_detects_regression']['preserves_tests'] is False
    assert cases['conflict_plus_independent_valid']['preserves_tests'] is False
    assert cases['empty_test_plan']['preserves_tests'] is True
    assert cases['both_implementations_fail_all_tests']['preserves_tests'] is True
    assert cases['unavailable_before_and_after']['preserves_tests'] is True
    sim = variant['actual_review_method_simulations']
    assert sim['valid_test_detects_regression']['audit']['selected'] == 'original'
    assert sim['empty_test_plan']['audit']['selected'] == 'review'
    assert sim['conflict_plus_independent_valid']['audit']['selected'] == 'original'
assert variants['046324b']['actual_review_method_simulations']['conflicting_oracles_only']['audit']['selected'] == 'original'
for ref in ('dc9ef0f','2e6f51a'):
    assert variants[ref]['actual_review_method_simulations']['conflicting_oracles_only']['audit']['selected'] == 'review'

input_value = {'materials': MATERIALS, 'original': BASE, 'revision': BAD, 'test_plans': scenarios}
input_bytes = (json.dumps(input_value, ensure_ascii=False, indent=2) + '\n').encode()
(OUT/'selection-gate-synthetic-input.json').write_bytes(input_bytes)
result = {
    'origin': 'Codex offline', 'scope': 'exact Git function ASTs; synthetic decision gate only',
    'target_model_calls': 0, 'official_score': None, 'full_workflow_executed': False,
    'review_implementation_exact_ast_executed_with_explicit_synthetic_dependencies': True,
    'linux_isolation_executed': False, 'agent_or_test_source_modified': False,
    'identity_note': '046324b is teammate source; not established as the official 80.9 baseline.',
    'input_sha256': sha(input_bytes), 'script_sha256': sha(Path(__file__).read_bytes()),
    'source_sha256': sources, 'environment': {'python': sys.version, 'platform': platform.platform()},
    'variants': variants,
    'conclusion': {
        'new_combination_risk': 'conflicting oracle removal can empty the valid-test set; existing non-tested acceptance then permits an unvalidated regression',
        'inherited_gaps': ['empty plan permits revision', 'unavailable before permits revision', 'equal all-failed results permit revision'],
        'recommended_boundary': 'retain conflict rejection; retain original whenever no valid executable verification supports selecting its replacement',
        'not_proven': ['online cause', 'current frozen-input behavior', 'official 80.9 identity', 'score delta'],
    },
}
(OUT/'selection-gate-three-revisions.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({'result': str(OUT/'selection-gate-three-revisions.json'), 'input_sha256': result['input_sha256'],
                  'conflict_only_gate': {r: variants[r]['cases']['conflicting_oracles_only']['preserves_tests'] for r in REFS},
                  'origin': result['origin'], 'target_model_calls': 0}, ensure_ascii=False))
