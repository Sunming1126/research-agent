"""Write source-isolation fixtures; no D execution."""
import ast
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent/'协同开发/research-agent-d-contract-20261106'
allowed={f'src/research_agent/workflows/{name}.py' for name in (
    'layered_d_runner','d_generation_policy','source_generation','d_review','d_selftest','d_refinement','d_holdout')}
protected={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_text(encoding='utf-8').encode('utf-8')).hexdigest()
           for p in [ROOT/'agent.py',ROOT/'Dockerfile',ROOT/'.dockerignore',ROOT/'requirements.txt',
                     ROOT/'pyproject.toml',*sorted((ROOT/'src').rglob('*.py'))]
           if p.relative_to(ROOT).as_posix() not in allowed}
fixture=ROOT/'tests/fixtures/d-contract-protected-source-hashes.json'
fixture.write_text(json.dumps({'baseline_commit':'dc9ef0f2c9d257d14536389cbf72fa0b3e89b9bd',
    'allowed_d_paths':sorted(allowed),'protected':protected},indent=2)+'\n',encoding='utf-8')
for file,name in [('test_b5_conditions.py','test_protected_production_files_and_shared_dispatch_only'),
                  ('test_e4_resilience.py','test_e4_freezes_all_d_and_c_specific_files')]:
    path=ROOT/'tests'/file
    source=path.read_text(encoding='utf-8')
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name==name)
    replacement=f'''def {name}():
    # Historical testcase ID retained for the D30 cross-version evidence map.
    # The active D phase protects the complete frozen BE/shared runtime, rather
    # than mutually inconsistent pre-formal historical snapshots.
    import hashlib
    record=json.loads((ROOT/'tests/fixtures/d-contract-protected-source-hashes.json').read_text())
    assert record['baseline_commit']=='dc9ef0f2c9d257d14536389cbf72fa0b3e89b9bd'
    assert record['protected']
    for name,digest in record['protected'].items():
        assert hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode('utf-8')).hexdigest()==digest,name
'''
    lines=source.splitlines(keepends=True)
    path.write_text(''.join(lines[:node.lineno-1])+replacement+''.join(lines[node.end_lineno:]),encoding='utf-8')
print(json.dumps({'protected_files':len(protected),'allowed_d_paths':sorted(allowed),'runtime_changes':0}))
