"""Current-material D cancellation fault injection, without a practice oracle."""
import json
import threading

import pytest

from research_agent.core.session import CaseSession
from test_eval_20261026_regressions import setup_d, response


@pytest.mark.parametrize('attempt', [1, 2, 3])
def test_cancelled_generation_never_becomes_exhausted_source_retry(tmp_path, attempt):
    runner, case = setup_d(tmp_path, [])
    cancelled = threading.Event()
    calls = []
    initial = vars(runner.llm).copy()
    def complete(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == attempt:
            cancelled.set()
            raise TimeoutError('transport interrupted after user cancellation')
        return response('unfinished source', 'length')
    runner.llm.completion = complete
    with CaseSession(case.case_id,1780,cancelled=cancelled).activate():
        with pytest.raises(TimeoutError,match='cancelled'):
            runner.prepare_checked_implementation(case)
    assert calls == ['d_source'] * attempt
    assert not (runner.work/'implementation.py').exists()
    assert not (runner.work/f'source-attempt-{attempt+1}.json').exists()
    for name in ('deadline','REQUEST_TIMEOUT_CAP','RETRY_ATTEMPTS','thinking_override'):
        assert (name in vars(runner.llm)) == (name in initial)


def test_response_arriving_after_cancellation_is_evidence_not_selected_source(tmp_path):
    runner, case = setup_d(tmp_path, [])
    cancelled = threading.Event()
    reply = response()
    calls = []
    def complete(*args, **kwargs):
        calls.append(args[0])
        cancelled.set()
        return reply
    runner.llm.completion = complete
    with CaseSession(case.case_id,1780,cancelled=cancelled).activate():
        with pytest.raises(TimeoutError,match='cancelled'):
            runner.prepare_checked_implementation(case)
    assert calls == ['d_source']
    assert json.loads((runner.work/'source-attempt-1.json').read_text()) == reply
    assert not (runner.work/'implementation.py').exists()
    assert not (runner.work/'source-attempt-2.json').exists()


def test_uncancelled_request_timeouts_remain_bounded_and_honest(tmp_path):
    runner, case = setup_d(tmp_path,[TimeoutError('request timeout') for _ in range(3)])
    with CaseSession(case.case_id,1780).activate():
        with pytest.raises(RuntimeError,match='no complete'):
            runner.prepare_checked_implementation(case)
    assert not (runner.work/'implementation.py').exists()
    audit=json.loads((runner.work/'public-selftest.json').read_text())
    assert len(audit['attempts']) == 3
