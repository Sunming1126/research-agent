"""B答卷与故障证据分离；失败不伪造论文、不改写SDK失败追踪。"""
from contextvars import ContextVar
import uuid

from .utils import confined, write_json, read_json, sha256_file
from .dispatch_plan import verify_current_assignment


B_REQUEST = ContextVar('research_b_request', default=None)


def publish_b(outcome):
    slot = B_REQUEST.get()
    if slot is not None:
        slot['outcome'] = outcome
    return outcome


def failure_submission(settings, case, error_type):
    result = {'case_id': case.case_id, 'papers': []}
    outcome = {'case_id': case.case_id, 'result': result, 'outcome': 'failed',
               'error_type': error_type, 'result_saved': False}
    try:
        # 失败答卷也不能绕过本轮派题身份/摘要核验。
        if getattr(settings, 'verify_assigned', False):
            verify_current_assignment(settings, case)
        relative = f'data/{settings.team}/{case.case_id}.json'
        target = confined(settings.output, relative)
        write_json(target, result)
        if read_json(target) != result:
            raise ValueError('B failure JSON readback mismatch')
        outcome.update(result_saved=True, path=relative, sha256=sha256_file(target))
        diagnostic = f'data/{settings.team}/work/{case.case_id}/failure-{uuid.uuid4().hex}/outcome.json'
        outcome['diagnostic_path'] = diagnostic
        write_json(confined(settings.output, diagnostic), outcome)
    except (OSError, ValueError, AttributeError) as exc:
        # 文件系统拒绝写入时不能谎称已经保存；HTTP仍只包含B字段。
        outcome['storage_error_type'] = type(exc).__name__
    return publish_b(outcome)
