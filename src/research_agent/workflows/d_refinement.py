"""D 专用第二轮修订：只处理当轮执行证据，保留当前最佳源码/响应对。"""
from ..core.session import current_session
from ..integrations.llm import ModelError
from .d_generation_policy import generation_window
from .d_review import review_prompt, review_result


def refine_remaining(runner, case, implementation, materials, tests, execution):
    from .d_selftest import run_tests, preserves_tests
    info = {'selected': False, 'state': 'not_needed'}
    # 自测只是开发诊断，不能替代正式验收；没有可执行反例时不盲目重写。
    if execution.get('state') != 'tested' or not any(not r['passed'] for r in execution.get('results', [])):
        return info
    session = current_session()
    if session and session.remaining() < 1100:
        return {**info, 'state': 'insufficient_time'}
    response_path = runner.work / 'generation-response.json'
    source_bytes = implementation.read_bytes()
    response_bytes = response_path.read_bytes()
    source = source_bytes.decode('utf-8')
    prompt = review_prompt(materials, source, {'tests': tests, 'execution': execution})
    prompt += ('\n这是修订后的实现，仍有实际测试失败。逐项判别是期望值错误还是实现错误；'
               '只报告当前源码仍然存在且能从材料定位的问题，不重复已修复问题，不为通过错误测试改代码。')
    available = getattr(runner.llm, 'budget', 40000) - getattr(runner.llm, 'total_tokens', 0)
    if available < len(prompt)//2 + 8000:
        return {**info, 'state': 'insufficient_budget'}
    try:
        with generation_window(runner.llm, baseline=False, case_id=case.case_id):
            result = review_result(runner.llm.completion('d_review', prompt, max_tokens=8000), materials, source)
        info.update(state=result['state'], grounded_defects=len(result['defects']))
        if not result['defects'] or (session and session.remaining() < 660):
            return info
        runner.prepare_generated_implementation(case, attempt='review-2', feedback={
            'defects': result['defects'], 'current_candidate': source,
            'instruction': '根据本轮材料修复仍存在的错误，保留其它已正确分支，返回完整实现。'})
        after = run_tests(implementation.read_text(encoding='utf-8'), tests)
        info['retest'] = {'state': after['state'], 'passed': sum(r['passed'] for r in after['results'])}
        # 必须保留所有已通过测试，并确实减少失败；没有改进就沿用第一轮候选。
        improved = (preserves_tests(execution, after) and after['state']=='tested'
                    and sum(r['passed'] for r in after['results']) > sum(r['passed'] for r in execution['results']))
        if improved:
            return {**info, 'selected': True, 'state': 'improved'}
        info['state'] = 'no_verified_improvement'
    except (ModelError, ValueError, TypeError, KeyError, SyntaxError, RuntimeError, TimeoutError) as exc:
        info.update(state='unavailable', error_type=type(exc).__name__)
    # 来源响应和源码必须一起恢复，不能把前一候选配上后一轮响应。
    implementation.write_bytes(source_bytes)
    response_path.write_bytes(response_bytes)
    return info
