from __future__ import annotations

import asyncio
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
from .models import CaseContext, Artifact
from .session import CaseSession, checkpoint, current_session
from .utils import confined, write_json, read_json, atomic_text, sha256_file
from ..contracts.validation import validate_result
from ..integrations.instrumentation import tool
from ..integrations.llm import GatewayLLM
from ..rag.materials import DocumentIndex, MaterialError
from ..rag.retrieval import HybridRetriever
from ..workflows.experiments import ExperimentRunner, parse_metric_specs, failed_result
from ..workflows.tasks import TaskHandlers


class ResearchAgentEngine:
    """串联单题处理、结果落盘和整轮预检查。"""
    def __init__(self, settings=None, llm_factory=None):
        self.settings = settings or Settings.from_env()
        self.llm_factory = llm_factory or (lambda: GatewayLLM(self.settings.llm_calls, self.settings.llm_tokens, self.settings.case_timeout))
        self.lock = threading.Lock()

    async def solve(self, payload):
        cancelled = threading.Event()
        try:
            return await asyncio.to_thread(self.solve_sync, payload, cancelled)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    def solve_sync(self, payload, cancelled=None):
        # 实验共享单 CPU 资源；串行执行可避免并发请求覆盖产物。
        cancelled = cancelled or threading.Event()
        received_at = time.monotonic()
        dispatch = isinstance(payload, dict) and payload.get("dispatch") is True
        timeout = self._case_timeout(payload) if not dispatch else None
        while not self.lock.acquire(timeout=.1):
            if cancelled.is_set() or (timeout and time.monotonic() - received_at >= timeout):
                raise TimeoutError("case cancelled or expired while queued")
        try:
            if isinstance(payload, dict) and payload.get("dispatch") is True:
                return self.run_dispatch(cancelled)
            return self._solve_one(payload, cancelled, received_at)
        finally:
            self.lock.release()

    def _case_timeout(self, payload):
        limit = self.settings.case_timeout
        if isinstance(payload, dict) and payload.get("function") == "Reproduction" and self.settings.experiment_mode == "platform":
            limit = min(limit, 180)
        return limit

    @tool(name="run_assigned_case")
    def _solve_one(self, payload, cancelled=None, received_at=None):
        case = CaseContext.from_dict(payload)
        session = CaseSession(case.case_id, self._case_timeout(payload),
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
        if previous.is_file():
            work.mkdir(parents=True, exist_ok=True)
            os.replace(previous, confined(work, "previous-result.json"))
        cache = confined(s.output, f"data/{s.team}/work/document-cache")
        llm = self.llm_factory()
        if hasattr(llm, "deadline"):
            llm.deadline = min(llm.deadline, session.deadline)
        handler = TaskHandlers(llm, s.team)
        index = DocumentIndex(s, cache)
        proof, specs = None, None
        try:
            if case.function == "Reproduction":
                specs = parse_metric_specs(case.input, case.case_id)
                runner = ExperimentRunner(s, work, llm=llm)
                try:
                    # 先核验题目授权材料；公开实验模式只能由配置显式启用。
                    index.build(case.materials)
                    result = runner.run(case, specs)
                    proof = runner.proof
                except (MaterialError, FileNotFoundError) as exc:
                    result = failed_result(case.case_id, specs, "materials_missing")
                    write_json(confined(work, "material-error.json"), {"message": str(exc)})
            else:
                # 大型 B 类候选库先看每篇前 8 页，避免单题冷启动耗尽预算。
                # C/E 保留全文切分，供具体事实与页码引用。
                # 大库冷启动必须落在逐题预算内；三百篇以上先读首页与摘要附近。
                # 深层正文召回风险由检索测试持续监测，C/E 仍读授权全文。
                materials = index.resolver.resolve(case.materials)
                pages = (4 if len(materials) >= 250 else 8) if case.function == "Literature_Retrieval" else None
                chunks = index.build(case.materials, page_limit=pages, resolved=materials)
                retriever = HybridRetriever(chunks)
                handler.document_expander = index.expand_documents if pages else None
                result = handler.handle(case, retriever)
            for material in index.materials:
                if sha256_file(material.path) != material.sha256:
                    raise ValueError("source material changed during case execution")
            filenames = [m.filename for m in index.materials]
            checkpoint("submission_validation")
            validate_result(case, result, s.output, team=s.team, filenames=filenames, specs=specs, proof=proof)
            self._persist(case, result, work, filenames, specs, proof, handler, llm)
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
                                                                  "rejected_claims": handler.rejected_claims,
                                                                  "retrieval_trace": handler.retrieval_trace,
                                                                  "query_plan_status": handler.query_plan_status,
                                                                  "audit": handler.audit,
                                                                   "coverage": handler.coverage,
                                                                   "evidence_audit": handler.evidence_audit,
                                                                   "report_trace": handler.report_trace,
                                                                  "draft": handler.last_draft})
            write_json(confined(work, "error.json"), {"case_id": case.case_id, "error_type": type(exc).__name__,
                                                    "message": str(exc)[:1000], "usage": getattr(llm, "usage", [])})
            write_json(confined(work, "session.json"), session.snapshot())
            raise

    @tool(name="write_and_readback_submission")
    def _persist(self, case, result, work, filenames, specs, proof, handler, llm):
        # 先写附属文件，再写 JSON；最后从磁盘回读，确认提交内容与产物一致。
        s = self.settings
        checkpoint("persist_and_readback")
        if "markdown" in result:
            atomic_text(confined(s.output, result["path"]), result["markdown"])
        target = confined(s.output, f"data/{s.team}/{case.case_id}.json")
        write_json(target, result)
        readback = read_json(target)
        if readback != result:
            raise ValueError("JSON readback mismatch")
        validate_result(case, readback, s.output, team=s.team, filenames=filenames, specs=specs, proof=proof, readback=True)
        artifacts = [target]
        if "path" in result:
            artifacts.append(confined(s.output, result["path"]))
        artifacts += [confined(s.output, f["path"]) for f in result.get("figures", [])]
        rows = [asdict(Artifact(p.relative_to(s.output).as_posix(), p.suffix[1:], p.stat().st_size, sha256_file(p))) for p in artifacts]
        write_json(confined(work, "evidence.json"), {"claims": [asdict(e) for e in handler.ledger],
                                                   "rejected_claims": handler.rejected_claims,
                                                   "retrieval_trace": handler.retrieval_trace,
                                                   "query_plan_status": handler.query_plan_status,
                                                   "chunks": [asdict(c) for c in handler.selected_chunks],
                                                   "audit": handler.audit,
                                                   "coverage": handler.coverage,
                                                   "evidence_audit": handler.evidence_audit,
                                                   "report_trace": handler.report_trace,
                                                   "last_model_draft": handler.last_draft})
        write_json(confined(work, "receipt.json"), {"case_id": case.case_id, "artifacts": rows, "filenames": filenames,
                                                  "specs": specs, "proof": proof,
                                                  "usage": getattr(llm, "usage", []),
                                                  "session": current_session().snapshot() if current_session() else None,
                                                  "source_case": case.to_dict()})
        write_json(confined(s.output, f"data/{s.team}/work/receipts/{case.case_id}.json"),
                   {"receipt": (work / "receipt.json").relative_to(s.output).as_posix()})

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

