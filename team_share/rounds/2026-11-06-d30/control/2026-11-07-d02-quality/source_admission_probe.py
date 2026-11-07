"""Read-only admission probes, using authored syntax fixtures, not secret D inputs."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / '协同开发/research-agent-d-contract-20261106'
HERE = Path(__file__).resolve().parent
REFS = ('046324b', 'dc9ef0f', '2e6f51a')
FIXTURES = {
    'valid_one_argument': 'def run_experiment(request):\n    return {}\n',
    'missing_request_argument': 'def run_experiment():\n    return {}\n',
    'extra_required_argument': 'def run_experiment(request, config):\n    return {}\n',
    'keyword_only_request': 'def run_experiment(*, request):\n    return {}\n',
}
observations = []
for ref in REFS:
    module = subprocess.check_output(['git', '-C', str(REPO), 'show',
                                     ref + ':src/research_agent/workflows/source_generation.py'])
    tree = ast.parse(module.decode('utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_normalize_source')
    scope = {'ast': ast}
    # Only the known repository normalization function executes. Fixture bodies do not.
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                 '<pinned-normalize-source>', 'exec'), scope)
    cases = []
    for name, source in FIXTURES.items():
        accepted = True
        try:
            scope['_normalize_source'](source)
        except (ValueError, SyntaxError):
            accepted = False
        # Exact call shape of this authored fixture; no algorithm/source execution.
        call_compatible = name == 'valid_one_argument'
        cases.append({'fixture': name, 'accepted_by_source_gate': accepted,
                      'callable_as_run_experiment_request': call_compatible,
                      'fixture_sha256': hashlib.sha256(source.encode()).hexdigest()})
    observations.append({'ref': subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', ref], text=True).strip(),
                         'module_sha256': hashlib.sha256(module).hexdigest(), 'fixtures': cases})
    assert all(c['accepted_by_source_gate'] for c in cases)
result = {
    'origin': 'Codex offline read-only source-admission counterexamples',
    'source_of_inputs': 'Four authored Python signature fixtures; not competition questions or hidden inputs',
    'target_model_calls': 0, 'production_files_changed': 0,
    'official_80p9_ref_verified': False,
    'finding': 'All three references accept syntax-valid entrypoints that cannot receive the actual positional request argument.',
    'introduced_by_latest_changes': False,
    'proves_latest_official_failure_cause': False,
    'observations': observations,
}
(HERE / 'source-admission-findings.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'versions': len(observations), 'unusable_entry_shapes_accepted_per_version': 3,
                  'online_root_cause_proven': False, 'production_files_changed': 0}))
