"""D-only generation budgets. Never alter GatewayLLM class defaults."""
from contextlib import contextmanager
import time
from ..core.session import current_session


@contextmanager
def generation_window(llm, *, baseline, case_id=None):
    cap = 60 if baseline else 95
    session = current_session()
    if session:
        cap = min(cap, session.remaining(reserve=45))
    names = ('deadline', 'REQUEST_TIMEOUT_CAP', 'RETRY_ATTEMPTS', 'thinking_override')
    # Preserve attribute presence as well as value (class defaults remain inherited).
    previous = {name: (name in vars(llm), getattr(llm, name, None)) for name in names}
    try:
        deadline = time.monotonic() + cap
        old = previous['deadline'][1]
        llm.deadline = min(old, deadline) if old is not None else deadline
        llm.REQUEST_TIMEOUT_CAP = cap
        llm.RETRY_ATTEMPTS = 1
        llm.thinking_override = 'enabled' if case_id == 'D-02' and not baseline else 'disabled'
        yield
    finally:
        for name, (present, value) in previous.items():
            if present:
                setattr(llm, name, value)
            else:
                vars(llm).pop(name, None)


def generation_tokens(case_id, baseline):
    return 2000 if baseline else (4200 if case_id == 'D-02' else 5200)


class PlatformExperimentUnavailable(RuntimeError):
    """Official service/source auditing unavailable; not an algorithm verdict."""


def platform_service_error(stderr):
    # Only inspect the terminal exception, not arbitrary generated source/trace text.
    last = next((line for line in reversed(stderr.splitlines()) if line.strip()), '')
    if 'D_SOURCE_AUDIT_UNAVAILABLE' in last:
        return 'D_SOURCE_AUDIT_UNAVAILABLE'
    if any(marker in last for marker in ('HTTP Error 503:', 'HTTP 503', 'status=503', 'status_code=503')):
        return 'HTTP_503'
    return None
