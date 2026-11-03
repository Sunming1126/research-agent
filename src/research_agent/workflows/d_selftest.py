"""D 通用当轮测试；测试不是官方答案，不含按题号定制的校验规则。"""
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
from .d_review import response_json, material_segments, material_reference


def test_prompt(materials):
    return (
        '仅根据当前授权材料设计独立的小输入单元测试。你看不到候选实现，不能迎合它。'
        '返回JSON {tests:[{name,material_id,derivation,request,expected}]}，最多6个测试。'
        'request是可直接传给run_experiment的完整合法对象，expected是该函数完整JSON返回值。'
        'material_id选择下列material_segments中主要规则的编号，不要抄写或改写编号；derivation逐步手算期望值，不能复制格式占位符。'
        '覆盖不同阶段、非默认参数、并列/空输入/状态更新等材料实际允许的情况；禁止编造无效输入。'
        '使用极小数据使手算可行，正确填入当轮协议要求的字段，不猜测旧题规则。'
        '先做最简单的合法正常例，再做一个材料明确允许的边界例；每例完整手算，不追求复杂数据。'
        '先梳理材料中的不同执行阶段和分支，为每个阶段选择能区分正确实现与常见误实现的极小例子。'
        '预算内优先覆盖尚未覆盖的阶段、参数覆盖默认值、阈值恰好相等和多步状态更新，避免六个测试重复同一简单分支。'
        '浮点结果按公式实际计算，不提前四舍五入；如果手算精度不足，不提供猜测的精确期望值。'
        '可选字段未给出时应省略，不可擅自用null代替；期望中的字段和值必须由当前规则导出。'
        '若无法独立推导精确输出则省略该测试，可返回空数组。不能访问隐藏标签。\n'
        +json.dumps({'material_segments':material_segments(materials)},ensure_ascii=False)
    )


def test_plan_result(response,materials):
    value=response_json(response);tests=value.get('tests')
    if not isinstance(tests,list) or len(tests)>6:raise ValueError('invalid_test_plan')
    result=[];rejected=[]
    for row in tests:
        if not isinstance(row,dict) or not all(isinstance(row.get(k),str) and row[k].strip() for k in ('name','derivation')):
            rejected.append('missing_fields');continue
        if not isinstance(row.get('request'),dict) or 'expected' not in row:
            rejected.append('invalid_request_expected');continue
        quote=material_reference(row,materials)
        if quote is None:
            rejected.append('unbound_material_quote');continue
        try:encoded=json.dumps(row,ensure_ascii=False,allow_nan=False)
        except (ValueError,TypeError):
            rejected.append('invalid_json_value');continue
        if len(encoded)>16000:
            rejected.append('too_large');continue
        result.append({**{k:row[k] for k in ('name','derivation','request','expected')},'material_quote':quote})
    return {'tests':result,'received':len(tests),'rejected':rejected}


def parse_tests(response,materials):
    return test_plan_result(response,materials)['tests']


def same_result(actual,expected):
    if isinstance(expected,bool) or expected is None:return actual is expected
    if type(expected) is int:return type(actual) is int and actual==expected
    if isinstance(expected,float):
        try:return isinstance(actual,(float,int)) and not isinstance(actual,bool) and math.isfinite(actual) and math.isclose(actual,expected,rel_tol=1e-8,abs_tol=1e-10)
        except OverflowError:return False
    if type(actual) is not type(expected):return False
    if isinstance(expected,dict):return actual.keys()==expected.keys() and all(same_result(actual[k],v) for k,v in expected.items())
    if isinstance(expected,list):return len(actual)==len(expected) and all(same_result(a,b) for a,b in zip(actual,expected))
    return actual==expected


def run_tests(source,tests):
    if not tests:return {'state':'no_tests','results':[]}
    if sys.platform!='linux':return {'state':'isolation_unavailable','results':[]}
    # 独立解释器清空凭据环境；子进程先装内核系统调用白名单，再执行候选代码。
    payload=json.dumps({'source':source,'requests':[t['request'] for t in tests]},ensure_ascii=False,allow_nan=False)
    if len(payload.encode())>150000:return {'state':'test_input_too_large','results':[]}
    worker=Path(__file__).with_name('d_test_worker.py')
    try:
        with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as err:
            completed=subprocess.run([sys.executable,'-I',str(worker)],input=payload.encode(),stdout=out,stderr=err,
                timeout=12,env={'PYTHONIOENCODING':'utf-8'},close_fds=True,check=False)
            out.seek(0);raw=out.read(131072)
        if completed.returncode:return {'state':'worker_failed','returncode':completed.returncode,'results':[]}
        report=json.loads(raw)
        if not isinstance(report,dict) or report.get('state')!='isolated':return {'state':'isolation_unavailable','results':[]}
        rows=report.get('results',[])
        if not isinstance(rows,list) or len(rows)!=len(tests) or any(not isinstance(r,dict) for r in rows):
            return {'state':'incomplete_tests','results':[]}
        return {'state':'tested','results':[{'name':t['name'],'passed':r.get('ok') is True and same_result(r.get('value'),t['expected']),
                 'actual':r.get('value'),'error':r.get('error')} for t,r in zip(tests,rows)]}
    except subprocess.TimeoutExpired:return {'state':'test_timeout','results':[]}
    except (OSError,ValueError,TypeError):return {'state':'test_runner_error','results':[]}


def preserves_tests(before,after):
    if before.get('state')!='tested':return True
    if after.get('state')!='tested':return False
    left=before['results'];right=after['results']
    return len(left)==len(right) and all(not a['passed'] or b['passed'] for a,b in zip(left,right)) and sum(r['passed'] for r in right)>=sum(r['passed'] for r in left)
