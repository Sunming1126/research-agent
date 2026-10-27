"""B答卷与故障证据分离；失败不伪造论文、不改写SDK失败追踪。"""
from contextvars import ContextVar
from contextlib import nullcontext
import os
import uuid

from .utils import confined, write_json, read_json, sha256_file
from .dispatch_plan import verify_current_assignment
from .session import current_session


B_REQUEST = ContextVar('research_b_request', default=None)


def publish_b(outcome):
    slot = B_REQUEST.get()
    if slot is not None:
        slot['outcome'] = outcome
    return outcome


def failure_submission(settings, case, error_type, session=None):
    slot = B_REQUEST.get()
    previous = slot.get('outcome') if slot is not None else None
    if previous and previous.get('case_id') == case.case_id and previous.get('result_saved'):
        # 本请求已成功提交的真实检索不可被后续诊断/取消覆盖。
        try:
            if read_json(confined(settings.output, previous['path'])) == previous['result']:
                return previous
        except (OSError, ValueError, KeyError):
            failed = dict(previous, result_saved=False, storage_error_type='CommittedResultUnavailable')
            return publish_b(failed)
        return publish_b(dict(previous, result_saved=False, storage_error_type='CommittedResultChanged'))
    result = {'case_id': case.case_id, 'papers': []}
    outcome = {'case_id': case.case_id, 'result': result, 'outcome': 'failed',
               'error_type': error_type, 'result_saved': False}
    try:
        # 失败答卷也不能绕过本轮派题身份/摘要核验。
        if getattr(settings, 'verify_assigned', False):
            verify_current_assignment(settings, case)
        lifecycle = session or current_session() or (slot.get('lifecycle') if slot is not None else None)
        if lifecycle:
            lifecycle.checkpoint('prepare_b_failure_submission')
        relative = f'data/{settings.team}/{case.case_id}.json'
        target = confined(settings.output, relative)
        work = confined(settings.output, f'data/{settings.team}/work/{case.case_id}/failure-{uuid.uuid4().hex}')
        staged = confined(work, 'submission.json')
        write_json(staged, result)
        if read_json(staged) != result:
            raise ValueError('B failure JSON readback mismatch')
        backup = confined(work, 'previous-result.json')
        installed = False
        try:
            with lifecycle.publication() if lifecycle else nullcontext():
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    os.replace(target, backup)
                os.replace(staged, target)
                installed = True
                if lifecycle:
                    lifecycle.checkpoint()
                if read_json(target) != result:
                    raise ValueError('B published failure JSON readback mismatch')
        except BaseException:
            if installed and target.exists():
                os.replace(target, staged)
            if backup.exists():
                os.replace(backup, target)
            raise
        outcome.update(result_saved=True, path=relative, sha256=sha256_file(target),
                       diagnostic_path=(work / 'outcome.json').relative_to(settings.output).as_posix())
        try:
            write_json(confined(work, 'outcome.json'), outcome)
        except OSError:
            outcome['diagnostic_saved'] = False
    except (OSError, ValueError, AttributeError) as exc:
        # 文件系统拒绝写入时不能谎称已经保存；HTTP仍只包含B字段。
        outcome['storage_error_type'] = type(exc).__name__
    return publish_b(outcome)
