from __future__ import annotations

import asyncio
import errno
import os
import json
import logging
from dataclasses import asdict, replace
import threading
import time
import uuid
from pathlib import Path

from .config import Settings
from .dispatch import load_dispatch
from .dispatch_plan import verify_current_assignment
from .models import CaseContext, Artifact
from .session import CaseSession, checkpoint, current_session
from .b_submission import B_REQUEST, publish_b, failure_submission
from .utils import confined, write_json, read_json, atomic_text, sha256_file, sha256_bytes
from ..contracts.validation import validate_result
from ..integrations.instrumentation import tool
from ..integrations.llm import GatewayLLM
from ..rag.materials import DocumentIndex, MaterialError
from ..rag.retrieval import HybridRetriever
from ..workflows.experiments import ExperimentRunner, parse_metric_specs, resolve_metric_specs, failed_result
from ..workflows.tasks import TaskHandlers


class ResearchAgentEngine:
    """串联单题处理、结果落盘和整轮预检查。"""
    def __init__(self, settings=None, llm_factory=None):
        self.settings = settings or Settings.from_env()
        self.llm_factory = llm_factory or (lambda: GatewayLLM(self.settings.llm_calls, self.settings.llm_tokens, self.settings.case_timeout))
        self.lock = threading.Lock()

    async def solve(self, payload):
        cancelled = threading.Event()
        request = None
        if not (isinstance(payload, dict) and payload.get("dispatch") is True):
            case = CaseContext.from_dict(payload)
            # 入口时计时，线程池排队时间也属于本题；线程与外层取消共享同一生命周期。
            request = CaseSession(case.case_id, self._case_timeout(payload), cancelled=cancelled)
            slot = B_REQUEST.get()
            if slot is not None:
                slot["lifecycle"] = request
        try:
            return await asyncio.to_thread(self.solve_sync, payload, cancelled, request)
        except asyncio.CancelledError:
            if request:
                request.cancel()
            else:
                cancelled.set()
            # 不能在执行锁之外发布失败答卷：排队中的取消没有当前题产物所有权。
            raise

    def solve_sync(self, payload, cancelled=None, request=None):
        # 实验共享单 CPU 资源；串行执行可避免并发请求覆盖产物。
        cancelled = cancelled or threading.Event()
        received_at = request.started_at if request else time.monotonic()
        dispatch = isinstance(payload, dict) and payload.get("dispatch") is True
        timeout = self._case_timeout(payload) if not dispatch else None
        if not dispatch and request is None:
            request = CaseSession(CaseContext.from_dict(payload).case_id, timeout,
                                  cancelled=cancelled, started_at=received_at)
            slot = B_REQUEST.get()
            if slot is not None:
                slot["lifecycle"] = request
        while not self.lock.acquire(timeout=.1):
            if cancelled.is_set() or (timeout and time.monotonic() - received_at >= timeout):
                raise TimeoutError("case cancelled or expired while queued")
        try:
            if isinstance(payload, dict) and payload.get("dispatch") is True:
                return self.run_dispatch(cancelled)
            try:
                return self._solve_one(payload, cancelled, received_at, request)
            except Exception as exc:
                # 派题核验、实验目录等前置失败也必须留下本队诊断，不能只剩SDK错误类。
                case = CaseContext.from_dict(payload)
                diagnostic = {"event": "research_request_failed", "case_id": case.case_id,
                              "error_type": type(exc).__name__, "message": str(exc)[:500]}
                logging.getLogger(__name__).error("%s", json.dumps(diagnostic, ensure_ascii=False))
                try:
                    write_json(confined(self.settings.output,
                        f"data/{self.settings.team}/work/{case.case_id}/preflight-{uuid.uuid4().hex}/request-error.json"), diagnostic)
                except (OSError, ValueError):
                    logging.getLogger(__name__).error("could not save request failure diagnostic")
                if case.function == 'Literature_Retrieval':
                    self._save_b_failure(case, type(exc).__name__, request)
                raise
        finally:
            self.lock.release()

    @tool(name='save_b_failure_and_readback')
    def _save_b_failure(self, case, error_type, request=None):
        return failure_submission(self.settings, case, error_type, session=request)

    def _case_timeout(self, payload):
        limit = self.settings.case_timeout
        if isinstance(payload, dict) and payload.get("function") == "Reproduction" and self.settings.experiment_mode == "platform":
            limit = min(limit, 180)
        # 最后保留10秒用于产物落盘与原生SDK导出，避免答卷卡在外层180秒边界。
        wire = min(limit, 180)
        return wire - min(10, wire * .1)

    @tool(name="run_assigned_case")
    def _solve_one(self, payload, cancelled=None, received_at=None, request=None):
        case = CaseContext.from_dict(payload)
        if self.settings.verify_assigned:
            verify_current_assignment(self.settings, case)
        session = request or CaseSession(case.case_id, self._case_timeout(payload),
                              cancelled=cancelled or threading.Event(),
                              started_at=received_at if received_at is not None else time.monotonic())
        with session.activate():
            return self._execute_one(case)

    def _execute_one(self, case):
        s = self.settings
        if case.function == "Reproduction" and s.experiment_mode == "platform":
            # 仅添加本题公开实验目录；其他 D 题和整个 experiments 均不授权。
            prefix = "experiments" if s.workspace.name == "data" else "data/experiments"
            case_root = confined(s.workspace, f"{prefix}/{case.case_id}")
            s = replace(s, material_roots=list(dict.fromkeys([*s.material_roots, case_root])))
        session = current_session()
        work_relative = f"data/{s.team}/work/{case.case_id}/{uuid.uuid4().hex}"
        work = confined(s.output, work_relative)
        # 重试前归档旧结果，避免本次失败后仍留下上次的成功提交。
        previous = confined(s.output, f"data/{s.team}/{case.case_id}.json")
        previous_files = [(previous, "previous-result.json"),
                          (confined(s.output, f"data/{s.team}/work/receipts/{case.case_id}.json"), "previous-receipt-pointer.json")]
        if case.function in {"Survey", "Idea"}:
            kind = "综述" if case.function == "Survey" else "灵感Idea报告"
            previous_files.append((confined(s.output, f"data/expert/{case.case_id}-{kind}-{s.team}.md"), "previous-report.md"))
        for previous, archived_name in previous_files:
            if previous.is_file():
                checkpoint("archive_previous_submission")
                work.mkdir(parents=True, exist_ok=True)
                os.replace(previous, confined(work, archived_name))
        cache = confined(s.output, f"data/{s.team}/work/document-cache")
        llm = self.llm_factory()
        if hasattr(llm, "deadline"):
            llm.deadline = min(llm.deadline, session.deadline)
        handler = TaskHandlers(llm, s.team)
        index = DocumentIndex(s, cache)
        proof, specs = None, None
        try:
            if case.function == "Reproduction":
                specs = resolve_metric_specs(case, s)
                runner = ExperimentRunner(s, work, llm=llm)
                try:
                    # 先核验题目授权材料；公开实验模式只能由配置显式启用。
                    # D读取自身方法/协议/样例，不把实验目录当论文全文库解析。
                    index.materials = index.resolver.resolve(case.materials)
                    result = runner.run(case, specs)
                    proof = runner.proof
                except (MaterialError, FileNotFoundError) as exc:
                    result = failed_result(case.case_id, specs, "materials_missing")
                    write_json(confined(work, "material-error.json"), {"message": str(exc)})
            else:
                # B 类按库规模读取前页候选，再深入匹配文献，给模型和落盘保留预算。
                # C/E 保留全文切分，供具体事实与页码引用。
                # 大库冷启动必须落在逐题预算内；250篇以上先读首页与摘要附近。
                # 深层正文召回风险由检索测试持续监测，C/E 仍读授权全文。
                b_case = case.function == 'Literature_Retrieval'
                # B 按正文证据检索，无需重建表格网格；100篇冷启动实测表格检测耗尽预算。
                # B/C/E保留表格文本块及页码，不重建网格；C/E仍读完整正文。
                index.parser.extract_tables = case.function not in {'Literature_Retrieval', 'Survey', 'Idea'}
                materials = index.resolver.resolve(case.materials, pdf_only=b_case, diagnostics=index.errors)
                pages = (2 if len(materials) >= 250 else 4 if len(materials) >= 50 else 8) if b_case else None
                chunks = index.build(case.materials, page_limit=pages, resolved=materials, tolerate_pdf_errors=b_case)
                if b_case and not chunks and any(row.get('error_type') for row in index.errors):
                    raise MaterialError('no readable unambiguous PDF remains in the authorized scope')
                retriever = HybridRetriever(chunks)
                handler.document_expander = index.expand_documents if pages else None
                if b_case and pages:
                    handler.document_probe = lambda queries: index.probe_deep_pages(queries, pages)
                result = handler.handle(case, retriever)
            for material in index.materials:
                if sha256_file(material.path) != material.sha256:
                    raise ValueError("source material changed during case execution")
            filenames = [m.filename for m in index.materials]
            checkpoint("submission_validation")
            validate_result(case, result, s.output, team=s.team, filenames=filenames, specs=specs, proof=proof)
            self._persist(case, result, work, filenames, specs, proof, handler, llm)
            if case.function == 'Literature_Retrieval':
                outcome = {'case_id': case.case_id, 'result': result, 'result_saved': True,
                           'outcome': 'partial' if index.errors or handler.retrieval_trace.get('partial_error') or handler.probe_trace.get('coverage_limited') else ('matched' if result['papers'] else 'no_matches'),
                           'material_diagnostics': index.errors, 'authorized_pdf_count': len(index.materials),
                           'retrieval_partial_error': handler.retrieval_trace.get('partial_error'),
                           'page_probe_coverage': handler.probe_trace,
                           'path': f'data/{s.team}/{case.case_id}.json', 'diagnostic_path': work_relative + '/b-outcome.json'}
                publish_b(outcome)
                try:
                    write_json(confined(work, 'b-outcome.json'), outcome)
                except OSError:
                    # 提交已提交且回读成功，附属诊断失败不能将真实论文覆盖为空数组。
                    logging.getLogger(__name__).error("B auxiliary outcome diagnostic could not be saved")
            return result
        except Exception as exc:
            # SDK 保留错误类型与 trace_id；运行日志补充不含凭据的材料定位信息。
            diagnostic = {"event": "research_case_failed", "case_id": case.case_id,
                          "error_type": type(exc).__name__, "team": s.team,
                          "material_roots": [str(root) for root in s.material_roots],
                          "diagnostic_path": work_relative + "/error.json"}
            if isinstance(exc, (MaterialError, FileNotFoundError, PermissionError)):
                diagnostic["material_error"] = str(exc)[:500]
            logging.getLogger(__name__).error("%s", json.dumps(diagnostic, ensure_ascii=False))
            write_json(confined(work, "diagnostic-evidence.json"), {"claims": [asdict(e) for e in handler.ledger],
                                                                  "material_diagnostics": index.errors,
                                                                  "rejected_claims": handler.rejected_claims,
                                                                  "retrieval_trace": handler.retrieval_trace,
                                                                  "query_plan_status": handler.query_plan_status,
                                                                  "audit": handler.audit,
                                                                   "coverage": handler.coverage,
                                                                   "evidence_audit": handler.evidence_audit,
                                                                   "report_trace": handler.report_trace,
                                                                  "draft": handler.last_draft})
            error = {"case_id": case.case_id, "error_type": type(exc).__name__,
                     "message": str(exc)[:1000], "usage": getattr(llm, "usage", []),
                     "model_response_failures": getattr(llm, 'response_failures', [])}
            if getattr(exc, 'issues', None):
                error['schema_diff'] = exc.issues
            write_json(confined(work, "error.json"), error)
            write_json(confined(work, "session.json"), session.snapshot())
            raise

    @tool(name="write_and_readback_submission")
    def _persist(self, case, result, work, filenames, specs, proof, handler, llm):
        # 所有可失败的序列化/证据/收据准备先在本次 work 内完成；最后短临界区发布。
        s = self.settings
        checkpoint("persist_and_readback")
        durable = case.function in {'Survey', 'Idea'}
        stage_root = confined(work, "submission-stage")
        storage_retries = []
        def stage_write(writer, path, value):
            # Only pre-publication, request-owned files are retryable. The value
            # is fixed; neither generation nor the publication transaction repeats.
            for attempt in range(2 if durable else 1):
                checkpoint()
                try:
                    return writer(path, value, durable=durable)
                except OSError as exc:
                    session = current_session()
                    if (not durable or attempt or isinstance(exc, (TimeoutError, PermissionError))
                            or exc.errno not in {errno.EIO, errno.EINTR, errno.EAGAIN, errno.ENOSPC, errno.EBUSY}
                            or session is None or session.publication_state != 'active'):
                        raise
                    content = value if isinstance(value, str) else json.dumps(
                        value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
                    storage_retries.append({'action': 'R3_retry_staging', 'attempt': attempt + 1,
                                            'path': path.relative_to(work).as_posix(),
                                            'value_sha256': sha256_bytes(content.encode('utf-8')),
                                            'error_type': type(exc).__name__, 'errno': exc.errno})
                    try:
                        write_json(confined(work, 'storage-recovery.json'),
                                   {'case_id': case.case_id, 'scope': 'current_request_staging_only',
                                    'attempts': storage_retries})
                    except (OSError, ValueError):
                        logging.getLogger(__name__).error('staging retry evidence could not be saved')
        updates = []
        if "markdown" in result:
            staged_markdown = confined(stage_root, result["path"])
            stage_write(atomic_text, staged_markdown, result["markdown"])
            updates.append((staged_markdown, confined(s.output, result["path"])))
            checkpoint()
        target = confined(s.output, f"data/{s.team}/{case.case_id}.json")
        staged_target = confined(stage_root, f"data/{s.team}/{case.case_id}.json")
        stage_write(write_json, staged_target, result)
        checkpoint()
        readback = read_json(staged_target)
        if readback != result:
            raise ValueError("JSON readback mismatch")
        validate_result(case, readback, stage_root if "markdown" in result else s.output,
                        team=s.team, filenames=filenames, specs=specs, proof=proof, readback=True)
        updates.append((staged_target, target))
        artifacts = [(target, staged_target)]
        if "path" in result:
            artifacts.append((confined(s.output, result["path"]), staged_markdown))
        artifacts += [(confined(s.output, f["path"]), confined(s.output, f["path"])) for f in result.get("figures", [])]
        rows = [asdict(Artifact(public.relative_to(s.output).as_posix(), public.suffix[1:], stored.stat().st_size, sha256_file(stored)))
                for public, stored in artifacts]
        stage_write(write_json, confined(work, "evidence.json"), {"claims": [asdict(e) for e in handler.ledger],
                                                   "rejected_claims": handler.rejected_claims,
                                                   "retrieval_trace": handler.retrieval_trace,
                                                   "query_plan_status": handler.query_plan_status,
                                                   "chunks": [asdict(c) for c in handler.selected_chunks],
                                                   "audit": handler.audit,
                                                   "coverage": handler.coverage,
                                                   "evidence_audit": handler.evidence_audit,
                                                   "report_trace": handler.report_trace,
                                                    "last_model_draft": handler.last_draft})
        stage_write(write_json, confined(work, "receipt.json"), {"case_id": case.case_id, "artifacts": rows, "filenames": filenames,
                                                  "specs": specs, "proof": proof,
                                                  "usage": getattr(llm, "usage", []),
                                                  "session": current_session().snapshot() if current_session() else None,
                                                  "source_case": case.to_dict()})
        staged_pointer = confined(stage_root, "receipt-pointer.json")
        stage_write(write_json, staged_pointer,
                    {"receipt": (work / "receipt.json").relative_to(s.output).as_posix()})
        updates.append((staged_pointer, confined(s.output, f"data/{s.team}/work/receipts/{case.case_id}.json")))
        session = current_session()
        if session is None:
            raise RuntimeError("submission publication requires an active case session")
        installed = []
        try:
            with session.publication():
                for index, (staged, public) in enumerate(updates):
                    checkpoint()
                    public.parent.mkdir(parents=True, exist_ok=True)
                    entry = {"staged": staged, "public": public, "backup": None, "installed": False}
                    installed.append(entry)
                    if public.exists():
                        backup = confined(stage_root, f"backup-{index}")
                        os.replace(public, backup)
                        entry["backup"] = backup
                    os.replace(staged, public)
                    entry["installed"] = True
                    checkpoint()
                if read_json(target) != result:
                    raise ValueError("published JSON readback mismatch")
                validate_result(case, result, s.output, team=s.team, filenames=filenames, specs=specs, proof=proof, readback=True)
        except BaseException:
            # 只撤回本次已发布的文件，原样保留到 work；单文件 rename 不等于多文件事务。
            for entry in reversed(installed):
                if entry["installed"] and entry["public"].exists():
                    os.replace(entry["public"], entry["staged"])
                if entry["backup"] is not None and entry["backup"].exists():
                    os.replace(entry["backup"], entry["public"])
            raise

    def run_dispatch(self, cancelled=None):
        cases = load_dispatch(self.settings)
        expected_count = len(cases)
        completed, errors = [], []
        for case in cases:
            if cancelled is not None and cancelled.is_set():
                errors.append({"case_id": case["case_id"], "type": "Cancelled", "message": "batch cancelled"})
                break
            try:
                result = self._solve_one(case, cancelled)
                completed.append({"case_id": result["case_id"], "status": result.get("status", "written")})
            except Exception as exc:
                errors.append({"case_id": case["case_id"], "type": type(exc).__name__, "message": str(exc)[:400]})
        summary = {"completed": completed, "errors": errors,
                   "expected_cases": expected_count,
                   "complete": len(completed) == expected_count and not errors}
        write_json(confined(self.settings.output, f"data/{self.settings.team}/work/batch-summary.json"), summary)
        return summary

    @tool(name="precheck_all_submissions")
    def precheck(self):
        cases = load_dispatch(self.settings)
        expected = {case["case_id"] + ".json" for case in cases}
        team_dir = confined(self.settings.output, f"data/{self.settings.team}")
        present = {path.name for path in team_dir.glob("*.json") if path.name != "dispatch.json"}
        if present != expected:
            raise ValueError(f"missing or extra results: {sorted(present ^ expected)}")
        for payload in cases:
            case = CaseContext.from_dict(payload)
            pointer = read_json(confined(self.settings.output, f"data/{self.settings.team}/work/receipts/{case.case_id}.json"))
            if not pointer["receipt"].startswith(f"data/{self.settings.team}/work/{case.case_id}/"):
                raise ValueError("invalid receipt pointer")
            receipt = read_json(confined(self.settings.output, pointer["receipt"]))
            if receipt["source_case"] != case.to_dict():
                raise ValueError("receipt belongs to another question")
            for item in receipt["artifacts"]:
                path = confined(self.settings.output, item["path"])
                if sha256_file(path) != item["sha256"]:
                    raise ValueError("artifact modified after completion")
            result = read_json(confined(self.settings.output, f"data/{self.settings.team}/{case.case_id}.json"))
            specs = [tuple(row) for row in receipt["specs"]] if receipt["specs"] else None
            validate_result(case, result, self.settings.output, team=self.settings.team,
                            filenames=receipt["filenames"], specs=specs, proof=receipt["proof"], readback=True)
        return {"cases": len(cases), "valid": True}

