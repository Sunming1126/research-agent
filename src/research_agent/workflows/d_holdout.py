"""D-only independent validation of a revised candidate against its original."""
import json
import time
from ..core.session import current_session
from ..integrations.llm import ModelError
from .d_generation_policy import generation_window
from .d_selftest import test_prompt, test_plan_result, run_tests, preserves_tests
from .d_review import review_prompt, review_result


def validate_revision(runner, case, path, materials, previous_tests, original, receipt):
    session = current_session()
    if not getattr(runner.llm, 'supports_source_reports', False) or (session and session.remaining() < 900):
        return {'state': 'not_started', 'reverted': False}
    # Test generation sees the contract and prior inputs, never either implementation.
    prompt = test_prompt(materials, case_id=case.case_id) + '\n这是独立复验。避免重复以下输入，优先检验未覆盖的执行阶段与合法参数组合。不能猜测期望值。\n' + json.dumps([t['request'] for t in previous_tests], ensure_ascii=False)
    try:
        with generation_window(runner.llm, baseline=False, case_id=case.case_id):
            runner.llm.deadline = min(runner.llm.deadline, time.monotonic() + 180)
            runner.llm.REQUEST_TIMEOUT_CAP = 180
            proposed = runner.llm.completion('d_tests', prompt, max_tokens=8000)
        tests = test_plan_result(proposed, materials)['tests']
        seen = {json.dumps(t['request'], sort_keys=True) for t in previous_tests}
        fresh = []
        for t in tests:
            key = json.dumps(t['request'], sort_keys=True)
            if key not in seen:
                fresh.append(t); seen.add(key)
        if not fresh:
            return {'state': 'no_new_tests', 'reverted': False}
        before = run_tests(original.decode('utf-8'), fresh)
        after = run_tests(path.read_text(encoding='utf-8'), fresh)
        # Unavailable isolation is not evidence against either candidate.
        regressed = before['state'] == after['state'] == 'tested' and not preserves_tests(before, after)
        if regressed:
            # A generated oracle can be wrong. Require a material-bound defect
            # before replacing the current candidate, not merely a red test.
            if session and session.remaining() < 660:
                return {'state': 'unconfirmed_regression', 'reverted': False}
            source = path.read_text(encoding='utf-8')
            with generation_window(runner.llm, baseline=False, case_id=case.case_id):
                runner.llm.deadline = min(runner.llm.deadline, time.monotonic()+180)
                runner.llm.REQUEST_TIMEOUT_CAP = 180
                review = runner.llm.completion('d_review', review_prompt(materials, source,
                    {'tests': fresh, 'execution': after, 'original_execution': before}), max_tokens=8000)
            confirmed = review_result(review, materials, source)
            if not confirmed['defects']:
                return {'state': 'unconfirmed_regression', 'reverted': False}
        if regressed:
            path.write_bytes(original)
            (runner.work/'generation-response.json').write_bytes(receipt)
        return {'state': 'reverted_regression' if regressed else 'compared', 'reverted': regressed,
                'count': len(fresh), 'original_passed': sum(r['passed'] for r in before['results']),
                'revised_passed': sum(r['passed'] for r in after['results']),
                'execution_states': [before['state'], after['state']]}
    except (ModelError, ValueError, TypeError, KeyError, RuntimeError, TimeoutError, OSError) as exc:
        return {'state': 'unavailable', 'reverted': False, 'error_type': type(exc).__name__}
