"""Fixed-input, no-model C/E end-to-end shadow comparison using official SDK."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / '镜像测试'
CURRENT = ROOT / '协同开发/research-agent-stability-20261027'
CONTROL = HERE / '审核/p0-ce-contract-20261031-316c2d2/source'
PYTHON = HERE / 'runtime/windows-python312/python.exe'

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def child(config):
    import errno
    import importlib.metadata
    import platform
    import re
    from unittest.mock import patch
    import c_no_model_guard_20261030
    from fastapi.testclient import TestClient
    from agenthack import AgentRuntime
    from agenthack.telemetry import Telemetry
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from agent import ResearchAgent
    from fixtures import FakeLLM, case
    from research_agent.core.config import Settings
    from research_agent.core.engine import ResearchAgentEngine
    from research_agent.core import engine as engine_module
    from research_agent.integrations.http_runtime import install_research_protocol
    import research_agent
    assert Path(research_agent.__file__).resolve().is_relative_to(Path(config['source']).resolve())
    out = Path(config['out'])
    dependencies = {}
    for name in ['agenthack', 'fastapi', 'starlette', 'pydantic', 'httpx', 'anyio', 'PyMuPDF']:
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = 'metadata_unavailable'
    environment = {'python': sys.version, 'platform': platform.platform(), 'dependencies': dependencies,
                   'official_sdk_runtime_sha256': sha(Path(sys.modules[AgentRuntime.__module__].__file__)),
                   'telemetry': 'local_InMemorySpanExporter', 'assignment_verification': False,
                   'container_resource_limits_enforced': False, 'real_model_connection_used': False,
                   'case_timeout': 180, 'model_call_budget': 30, 'model_token_budget': 40000}
    environment_hash = hashlib.sha256(json.dumps(environment, sort_keys=True).encode()).hexdigest()
    save(out / 'environment-fingerprint.json', {'sha256': environment_hash, 'fields': environment})
    library = Path(config['library'])
    names = sorted(p.name for p in library.glob('*.pdf'))
    recorded_models = []
    class Model(FakeLLM):
        supports_source_reports = True
        def __init__(self, short):
            self.short = short
            self.stages = []
            self.reports = 0
            recorded_models.append(self)
        def json(self, stage, data, **kwargs):
            self.stages.append(stage)
            if stage == 'report':
                self.reports += 1
                value = super().json(stage, data, **kwargs)
                if self.short and self.reports == 1:
                    prefix = '# Synthetic fixture report\n\n'
                    suffix = '\n\n' + ' '.join(f'[{i}, p.1]' for i in range(1, 6))
                    value['markdown'] = prefix + '测' * (799 - len(re.sub(r'\s', '', prefix + suffix))) + suffix
                return value
            if stage == 'repair':
                return {'patches': [{'original': '# Synthetic fixture report', 'replacement': '# Synthetic fixture review'}]}
            return super().json(stage, data, **kwargs)
    settings = Settings(out, out / 'output', 'offline-ce-fixed', [library])
    engine = ResearchAgentEngine(settings, lambda: Model(False))
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime = AgentRuntime(ResearchAgent(engine), telemetry=Telemetry(provider))
    original_routes = [(route.path, id(route.endpoint)) for route in runtime.app.routes]
    install_research_protocol(runtime)
    assert original_routes == [(route.path, id(route.endpoint)) for route in runtime.app.routes]
    outcomes = []
    with TestClient(runtime.app) as client:
        assert client.get('/health').status_code == 200
        for case_id in ['C-01', 'C-03', 'E-01', 'E-02']:
            question = case(case_id, names)
            engine.llm_factory = lambda short=case_id in {'C-01', 'E-01'}: Model(short)
            original_write = engine_module.atomic_text
            attempted = []
            def transient(path, value, **kwargs):
                if case_id == 'C-03' and path.suffix == '.md':
                    attempted.append(str(path))
                    if len(attempted) == 1:
                        raise OSError(errno.EIO, 'injected transient end-to-end staging failure')
                return original_write(path, value, **kwargs)
            transport = str(uuid.uuid5(uuid.NAMESPACE_URL, 'offline-ce-fixed/' + str(config['round']) + '/' + case_id))
            start = time.monotonic()
            with patch.object(engine_module, 'atomic_text', transient):
                response = client.post('/solve', json=question,
                                       headers={'X-Research-Task-ID': transport, 'Idempotency-Key': transport})
            elapsed = time.monotonic() - start
            payload = response.json()
            target = settings.output / f'data/offline-ce-fixed/{case_id}.json'
            json_match = target.is_file() and json.loads(target.read_text(encoding='utf-8')) == payload
            markdown_match = (response.status_code == 200 and isinstance(payload.get('markdown'), str)
                              and isinstance(payload.get('path'), str)
                              and (settings.output / payload['path']).is_file()
                              and (settings.output / payload['path']).read_text(encoding='utf-8') == payload['markdown'])
            exact_fields = isinstance(payload, dict) and set(payload) == {'case_id', 'path', 'markdown'}
            trace = response.headers.get('x-agenthack-trace-id')
            actual_trace_ids = {format(span.context.trace_id, '032x') for span in exporter.get_finished_spans()}
            item = {'case_id': case_id, 'input': question, 'input_sha256': hashlib.sha256(
                json.dumps(question, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                'transport_task_id': transport, 'http_status': response.status_code,
                'seconds': round(elapsed, 6), 'wire_fields_valid': exact_fields,
                'json_matches_response': json_match, 'markdown_matches_response': bool(markdown_match),
                'delivered': bool(response.status_code == 200 and exact_fields and json_match and markdown_match),
                'sdk_trace_id': trace, 'sdk_trace_present_in_local_exporter': trace in actual_trace_ids,
                'native_harness_proof': False, 'target_model_calls': 0,
                'logical_fixture_model_stages': recorded_models[-1].stages,
                'logical_fixture_report_calls': recorded_models[-1].reports,
                'staging_write_attempts_for_injected_file': len(attempted),
                'fault': {'C-01': 'first_report_799_characters', 'C-03': 'one_transient_staging_EIO',
                          'E-01': 'first_report_799_characters', 'E-02': 'none'}[case_id]}
            save(out / f'{case_id}-wire-response.json', payload)
            save(out / f'{case_id}-case-log.json', item)
            outcomes.append(item)
    provider.shutdown()
    summary = {'kind': 'offline_official_SDK_end_to_end_with_synthetic_model', 'round': config['round'],
               'variant': config['variant'], 'cases': outcomes, 'delivered': sum(r['delivered'] for r in outcomes),
               'environment_sha256': environment_hash,
               'total': len(outcomes), 'target_model_calls': 0, 'native_harness_execution': False,
               'official_score': None, 'official_runtime_routes_preserved': True}
    save(out / 'summary.json', summary)

def parent():
    auth = json.loads((ROOT / '.session-audit/p0-ce-offline-authorization-20261031.json').read_text(encoding='utf-8'))
    assert auth['user_reply'] == '允许本轮C/E离线测试'
    root = HERE / 'runs' / ('p0-ce-e2e-20261031-' + time.strftime('%H%M%S') + '-' + uuid.uuid4().hex[:6])
    root.mkdir(parents=True, exist_ok=False)
    save(ROOT / '.session-audit/p0-ce-e2e-latest-20261031.json', {'run': str(root)})
    # Generate synthetic PDFs once, without importing or executing the Agent.
    library = root / 'fixed-papers'
    boot = ('import sys;sys.path[:0]=' + repr([str(HERE / 'runtime/py312-windows')]) + ';import fitz;from pathlib import Path;'
            'root=Path(' + repr(str(library)) + ');root.mkdir();'
            '\nfor i in range(1,6):\n doc=fitz.open();p=doc.new_page();'
            'p.insert_text((40,40),"Synthetic software fixture: urban vegetation and remote sensing. "*3);'
            'doc.save(root/f"Study_{i}.pdf");doc.close()')
    subprocess.run([str(PYTHON), '-c', boot], check=True, timeout=30)
    inputs = {'papers': {p.name: sha(p) for p in library.glob('*.pdf')},
              'source_current': {p.relative_to(CURRENT).as_posix(): sha(p) for p in [CURRENT / 'agent.py', *sorted((CURRENT / 'src').rglob('*.py'))]},
              'source_control': {p.relative_to(CONTROL).as_posix(): sha(p) for p in [CONTROL / 'agent.py', *sorted((CONTROL / 'src').rglob('*.py'))]},
              'control_revision': '316c2d2c1b5c96c59b09fe906356f18d8d0364d8',
              'assignment_verification': False, 'constraint': 'local SDK only; no platform assigned files/collector/hidden grader'}
    save(root / 'fingerprints.json', inputs)
    runs = []
    for index in range(3):
        for variant in (['control', 'candidate'] if index % 2 == 0 else ['candidate', 'control']):
            source = CONTROL if variant == 'control' else CURRENT
            out = root / f'round-{index+1}-{variant}'
            out.mkdir()
            config = {'source': str(source), 'out': str(out), 'library': str(library), 'round': index + 1, 'variant': variant}
            save(out / 'config.json', config)
            paths = [str(source), str(source / 'src'), str(source / 'tests'), str(HERE / 'runtime/py312-windows'), str(HERE)]
            code = 'import sys,runpy;sys.path[:0]=' + repr(paths) + ';runpy.run_path(' + repr(str(Path(__file__).resolve())) + ',run_name="__main__")'
            env = os.environ.copy()
            env.update(PYTHONUTF8='1', RESEARCH_RUNTIME='local', AGENTHACK_TELEMETRY_REQUIRED='false',
                       ANIC_C_TEST_GUARD_LOG=str(out / 'network-guard.json'), P0_E2E_CHILD_CONFIG=str(out / 'config.json'))
            with (out / 'stdout.txt').open('wb') as stdout, (out / 'stderr.txt').open('wb') as stderr:
                process = subprocess.Popen([str(PYTHON), '-X', 'utf8', '-c', code], cwd=source, env=env, stdout=stdout, stderr=stderr)
                try:
                    status = process.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait();status=124
            entry = {'variant': variant, 'round': index + 1, 'returncode': status, 'path': out.name}
            if (out / 'summary.json').is_file():
                entry.update(json.loads((out / 'summary.json').read_text(encoding='utf-8')))
            runs.append(entry)
            save(root / 'progress.json', runs)
            if status:
                raise RuntimeError('Offline e2e child failed; evidence retained at ' + str(out))
    for relative, value in inputs['source_current'].items():
        assert sha(CURRENT / relative) == value, 'Source changed during fixed comparison'
    for name, value in inputs['papers'].items():
        assert sha(library / name) == value
    assert len({r['environment_sha256'] for r in runs}) == 1, 'Environment changed across paired trials'
    assert all(len({next(c['input_sha256'] for c in r['cases'] if c['case_id'] == case_id) for r in runs}) == 1
               for case_id in ['C-01', 'C-03', 'E-01', 'E-02']), 'Inputs changed across paired trials'
    summary = {'kind': 'three_paired_rounds_offline_SDK_not_real_models_or_platform_scores',
               'rounds': runs, 'target_model_calls': 0, 'native_harness_execution': False,
               'control_delivered': sum(r['delivered'] for r in runs if r['variant'] == 'control'),
               'candidate_delivered': sum(r['delivered'] for r in runs if r['variant'] == 'candidate'),
               'cases_per_variant': 12, 'source_and_papers_unchanged': True,
               'environment_sha256': runs[0]['environment_sha256'], 'input_fingerprints_match': True,
               'official_score': None}
    save(root / 'summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k != 'rounds'}, ensure_ascii=True), flush=True)

if __name__ == '__main__':
    if os.environ.get('P0_E2E_CHILD_CONFIG'):
        child(json.loads(Path(os.environ['P0_E2E_CHILD_CONFIG']).read_text(encoding='utf-8')))
    else:
        parent()
