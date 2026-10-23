"""Run regression checks against the installed target code inside its image.

Fixtures in this file are deterministic protocol/fault fixtures, never real LLM
responses or evidence of official platform admission.
"""
import ast
import copy
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

sys.path.insert(0, '/app/src')
from research_agent.core.config import Settings
from research_agent.core.models import CaseContext
from research_agent.core.utils import confined
from research_agent.contracts.validation import validate_result
from research_agent.workflows.source_generation import extract_source, save_source
from research_agent.workflows.layered_d_runner import LayeredDRunner, normalize_layered_submission, build_layered_command
from research_agent.workflows.experiments import failed_result, parse_metric_specs
from research_agent.integrations.llm import GatewayLLM
from research_agent.rag.materials import MaterialResolver, MaterialError

# These are function-level contract checks. SDK tool decorators require a live
# AgentRuntime session; HTTP tests in test_agent.py exercise that session layer.
# Unwrap only this local test reference, leaving the installed target unchanged.
validate_result = inspect.unwrap(validate_result)

MATERIAL = Path('/data/experiments')
RESULTS = []


def record(name, group, fn, level='required'):
    started = time.monotonic()
    try:
        evidence = fn()
        row = dict(id=name, group=group, level=level, status='PASS', evidence=evidence)
    except Exception as exc:
        row = dict(id=name, group=group, level=level, status='FAIL',
                   error=type(exc).__name__ + ': ' + str(exc), traceback=traceback.format_exc())
    row['seconds'] = round(time.monotonic()-started, 4)
    row['execution'] = 'real-target-code-in-image; deterministic fixtures where specified'
    RESULTS.append(row)
    print(json.dumps({k:v for k,v in row.items() if k not in ('traceback', 'evidence')}, ensure_ascii=False), flush=True)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def must_reject(fn, types=(ValueError, SyntaxError, TypeError)):
    try:
        fn()
    except types:
        return {'rejected': True}
    raise AssertionError('Invalid input was accepted')


def response(content, finish='stop'):
    return {'choices':[{'finish_reason':finish,'message':{'role':'assistant','content':content}}]}


SOURCE = 'def run_experiment(request):\n    return {"baseline": [], "candidate": None, "details": {}}\n'


def case(case_id='D-01'):
    kind={'A':'Chat','B':'Literature_Retrieval','C':'Survey','D':'Reproduction','E':'Idea'}[case_id[0]]
    return CaseContext.from_dict(dict(case_id=case_id,function=kind,title='自动化测试',
        input='name=Macro-F1, unit=分数; name=Accuracy, unit=%; name=运行时间, unit=秒。',
        materials=[],constraints=[],expected_output='strict output',output_schema_ref=kind))


def source_parity(body):
    spec=importlib.util.spec_from_file_location('official_source', MATERIAL/'model_source.py')
    official=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(official)
    def result(fn):
        try:
            return ('accepted',fn(body))
        except (ValueError,SyntaxError,TypeError,AttributeError):
            return ('rejected',None)
    a,b=result(extract_source),result(official.extract_source)
    check(a==b,'Target source extractor disagrees with delivered official extractor')
    return {'official_and_target':a[0]}


def run():
    record('image.non_root','runtime',lambda: check(os.geteuid()!=0,'Target user is root'))
    record('image.python_runtime','runtime',lambda: check(sys.version_info[:2]>=(3,9),'Unsupported Agent Python '+sys.version))
    for name in ('agenthack','openai','PIL','fitz'):
        record('dependency.'+name,'runtime',lambda name=name: check(importlib.util.find_spec(name) is not None,'Missing '+name))
    for rel in ['source_generation.py','layered_d_runner.py','experiments.py']:
        path=Path('/app/src/research_agent/workflows')/rel
        record('agent.syntax.'+rel,'compatibility',lambda path=path: ast.parse(path.read_text()) and {'parsed':True,'agent_python':sys.version.split()[0],'not_algorithm_python39_test':True})

    fixtures={
        'plain':response(SOURCE),
        'fence':response('```python\n'+SOURCE+'```'),
        'crlf':response(SOURCE.replace('\n','\r\n')),
        'trailing_newlines':response(SOURCE+'\n\n'),
        'explanation_and_fence':response('实现如下：\n```python\n'+SOURCE+'```\n'),
        'truncated':response(SOURCE,'length'),
        'no_entrypoint':response('def another(request):\n    return {}\n'),
        'invalid_syntax':response('def run_experiment(request)\n    return {}\n'),
        'two_versions':response('```python\n'+SOURCE+'```\n```python\ndef run_experiment(request):\n    return {}\n```'),
        'unrelated':response('This is not Python source.'),
        'oversize':response(SOURCE+'#'+('x'*66000)),
        'wrong_role':{'choices':[{'finish_reason':'stop','message':{'role':'user','content':SOURCE}}]},
        'empty_choices':{'choices':[]},
        'python310_syntax':response('def run_experiment(request):\n    match request:\n        case _:\n            return {}\n'),
    }
    for name,body in fixtures.items():
        record('source.official_parity.'+name,'source-provenance-format',lambda body=body:source_parity(body))
    for name in ['truncated','no_entrypoint','invalid_syntax','two_versions','unrelated','oversize','wrong_role','empty_choices','python310_syntax']:
        record('source.reject.'+name,'source-provenance-format',lambda name=name:must_reject(lambda:extract_source(fixtures[name])))
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        def preservation():
            original='# 原始注释保留\n'+SOURCE.replace('return {','return  {')
            dest=root/'model.py'
            digest=save_source(response('```python\n'+original+'```'),dest)
            check(dest.read_bytes()==original.encode(),'Model code was rewritten')
            check(digest==hashlib.sha256(original.encode()).hexdigest(),'Digest does not match source')
            return {'byte_identical':True,'sha256':digest}
        record('source.byte_preservation','source-provenance-format',preservation)
        for text in ['../escape','/etc/passwd','a/../../escape','C:\\escape']:
            record('paths.reject.'+text,'material-isolation',lambda text=text:must_reject(lambda:confined(root,text)))

        output=root/'out'; output.mkdir()
        specs=[('Macro-F1','分数'),('Accuracy','%'),('运行时间','秒')]
        d=case()
        failure=failed_result('D-01',specs,'failed')
        record('D.failure.three_null_metrics','output-contract',lambda:validate_result(d,failure,output,specs=specs))
        for label,change in {
            'missing_metric':lambda x:x['metrics'].pop(),
            'nan_observed':lambda x:x['metrics'][0].update(observed=float('nan')),
            'failed_has_observed':lambda x:x['metrics'][0].update(observed=1.0),
            'extra_top_level':lambda x:x.update(details={}),
            'completed_without_evidence':lambda x:x.update(status='completed'),
            'wrong_case':lambda x:x.update(case_id='D-02'),
            'duplicate_metric':lambda x:x['metrics'][1].update(name=x['metrics'][0]['name']),
        }.items():
            value=copy.deepcopy(failure); change(value)
            record('D.reject.'+label,'output-contract',lambda value=value:must_reject(lambda:validate_result(d,value,output,specs=specs)))
        record('D.rpc_complete_preserves_failed','output-contract',lambda: check(
            normalize_layered_submission({'case_id':'D-01','status':'complete','submission':failure},'D-01')['status']=='failed',
            'RPC complete was incorrectly interpreted as experiment completed'))
        record('D.command.requires_implementation','source-provenance-format',lambda:check(
            '--implementation' in build_layered_command('python',Path('/data/experiments/run_experiment.py'),'D-01',root/'model.py'),
            'Missing implementation argument'))
        record('D.metrics_no_implicit_defaults','output-contract',lambda:must_reject(lambda:parse_metric_specs('No metric specifications','D-01')))

        a=case('A-01')
        record('A.valid_contract','output-contract',lambda:validate_result(a,{'case_id':'A-01','answer':'有依据的回答','clarifying_question':'研究尺度是什么？'},output))
        record('A.empty_question_rejected','output-contract',lambda:must_reject(lambda:validate_result(a,{'case_id':'A-01','answer':'回答','clarifying_question':''},output)))
        b=case('B-01')
        record('B.empty_results_legal','output-contract',lambda:validate_result(b,{'case_id':'B-01','papers':[]},output,filenames=[]))
        record('B.hallucinated_filename_rejected','output-contract',lambda:must_reject(lambda:validate_result(b,{'case_id':'B-01','papers':[{'filename':'not-present.pdf','reason':'相关'}]},output,filenames=[])))
        duplicate={'case_id':'B-01','papers':[{'filename':'a.pdf','reason':'相关'},{'filename':'a.pdf','reason':'重复'}]}
        record('B.duplicate_rejected','output-contract',lambda:must_reject(lambda:validate_result(b,duplicate,output,filenames=['a.pdf'])))
        for cid in ['C-01','E-01']:
            item=case(cid)
            record(cid+'.short_report_rejected','output-contract',lambda item=item,cid=cid:must_reject(lambda:validate_result(item,{'case_id':cid,'path':'wrong.md','markdown':'短文'},output,filenames=[])))
        for label,mutator in {
            'prefix_function_mismatch':lambda v:v.update(function='Survey'),
            'schema_mismatch':lambda v:v.update(output_schema_ref='Chat'),
            'materials_not_array':lambda v:v.update(materials='D-01'),
            'invalid_case_id':lambda v:v.update(case_id='D-../escape'),
        }.items():
            value=d.to_dict();mutator(value)
            record('input.reject.'+label,'input-contract',lambda value=value:must_reject(lambda:CaseContext.from_dict(value)))

        settings=Settings(workspace=Path('/data'),output=output,material_roots=[MATERIAL],case_timeout=180)
        def generate_fixture():
            class FixtureLLM:
                def completion(self,*args,**kwargs):
                    return response(SOURCE)
            target=LayeredDRunner(settings,root/'generated',llm=FixtureLLM())
            path=target.prepare_generated_implementation(d)
            check(path.read_bytes()==SOURCE.encode(),'Generated source was changed')
            return {'fixture_not_real_llm':True,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        record('D.generated_source_saved_unmodified','source-provenance-format',generate_fixture)

        # A quality probe: optional recovery capability, not an extra official
        # admission condition. Failure must not be described as automatic DQ.
        def repair_probe():
            class FixtureLLM:
                calls=0
                def completion(self,*args,**kwargs):
                    self.calls+=1
                    return response('def broken(') if self.calls==1 else response(SOURCE)
            llm=FixtureLLM(); runner=LayeredDRunner(settings,root/'repair',llm=llm)
            try:
                runner.prepare_checked_implementation(d, tester=lambda *a: {'status':'passed','fixture_not_real_algorithm_test':True})
            except (ValueError,SyntaxError):
                raise AssertionError('No model repair: invalid first source ends generation after %d call(s)'%llm.calls)
            check(llm.calls>=2,'Expected a model-generated complete repair')
        record('quality.model_source_repair','recovery',repair_probe,level='quality')

        # Exercise the real subprocess path with a harmless sleeping child. Only
        # source preparation and the command are fixtures; installed timeout and
        # cancellation code are not mocked or replaced.
        def lifecycle_probe(cancel=False):
            import threading
            from dataclasses import replace
            from research_agent.core.session import CaseSession
            work=root/('cancel' if cancel else 'budget');work.mkdir()
            runner=LayeredDRunner(replace(settings,case_timeout=10 if cancel else 1),work,llm=None)
            path=work/'implementation.py';path.write_text(SOURCE)
            def generate(*args,**kwargs):
                time.sleep(.15)
                return path
            session=CaseSession('D-01',10 if cancel else 1)
            timer=threading.Timer(.35,session.cancelled.set) if cancel else None
            started=time.monotonic()
            if timer:timer.start()
            prepared='prepare_checked_implementation' if hasattr(runner,'prepare_checked_implementation') else 'prepare_generated_implementation'
            try:
                with patch.dict(os.environ,{'RESEARCH_EXPERIMENT_CASE':'D-01'}), \
                     patch.object(runner,prepared,side_effect=generate), \
                     patch('research_agent.workflows.layered_d_runner.build_layered_command',return_value=[sys.executable,'-c','import time;time.sleep(3)']),session.activate():
                    try:result=runner.run(d,specs)
                    except (TimeoutError, __import__('subprocess').TimeoutExpired):result={'status':'failed'}
            finally:
                if timer:timer.cancel()
            elapsed=time.monotonic()-started
            check(elapsed < (1.4 if cancel else 1.5),'Child not stopped within case/cancel budget: %.3fs'%elapsed)
            check(result['status']!='completed','Sleeping child produced a completed answer')
            return {'fixture_not_real_llm':True,'actual_sleeping_subprocess':True,'seconds':elapsed,'case_timeout':session.timeout,'cancelled':cancel}
        record('D.cumulative_case_budget','time-budget',lifecycle_probe)
        record('D.cancel_stops_child','time-budget',lambda:lifecycle_probe(True),level='quality')

        def environment_probe():
            import research_agent.workflows.layered_d_runner as module
            import subprocess
            work=root/'upstream-error';work.mkdir()
            runner=LayeredDRunner(settings,work,llm=None)
            path=work/'implementation.py';path.write_text(SOURCE)
            completed=subprocess.CompletedProcess([],1,'','RuntimeError: D_SOURCE_AUDIT_UNAVAILABLE\n')
            prepared='prepare_checked_implementation' if hasattr(runner,'prepare_checked_implementation') else 'prepare_generated_implementation'
            process_path='research_agent.workflows.layered_d_runner.'+('run_case_process' if hasattr(module,'run_case_process') else 'subprocess.run')
            with patch.dict(os.environ,{'RESEARCH_EXPERIMENT_CASE':'D-01'}), \
                 patch.object(runner,prepared,return_value=path), \
                 patch(process_path,return_value=completed):
                result=runner.run(d,specs)
            error=json.loads((work/'experiment-error.json').read_text())
            check('D_SOURCE_AUDIT_UNAVAILABLE' in error.get('message',''),
                  'Structured error loses upstream code and returns '+result['status']+'; raw stderr still retained')
            return {'fixture_not_real_llm':True,'diagnostic':error}
        record('quality.platform_error_classification','recovery',environment_probe,level='quality')

    # Credentials are fixtures; none are sent over the network.
    with patch.dict(os.environ,{'PLATFORM_LLM_URL':'https://example.invalid/session/one/v1',
                               'PLATFORM_API_KEY':'test-fixture-not-a-secret','PLATFORM_LLM_MODEL':'fixture-model'},clear=True):
        record('gateway.full_session_url_preserved','gateway-compatibility',lambda:check(
            GatewayLLM().base_url=='https://example.invalid/session/one/v1','Session-scoped URL was modified'))
        record('gateway.platform_model_alias','gateway-compatibility',lambda:check(GatewayLLM().model=='fixture-model','PLATFORM_LLM_MODEL ignored'))
    print(json.dumps({'summary':{s:sum(x['status']==s for x in RESULTS) for s in ['PASS','FAIL']}}),flush=True)
    Path('/results/offline-checks.json').write_text(json.dumps(RESULTS,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    run()
