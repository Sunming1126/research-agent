"""复用哈希一致的授权证据，定向验证 C/E 写作阶段；不计为完整仿真。"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import time
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from scripts.run_v3_official_simulation import load_local_credentials
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.models import CaseContext, Chunk, Evidence
from research_agent.core.session import CaseSession
from research_agent.core.utils import read_json, write_json
from research_agent.rag.materials import MaterialResolver
from research_agent.integrations.llm import GatewayLLM
from research_agent.workflows.tasks import TaskHandlers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="composition-v5")
    parser.add_argument("--reuse-drafts-from")
    parser.add_argument("--cases", nargs="+")
    args = parser.parse_args()
    load_local_credentials()
    output = ROOT / "output/architecture-upgrade-20261022" / args.label
    if (output / "summary.json").exists():
        raise ValueError("composition run already exists; preserve previous evidence")
    output.mkdir(parents=True, exist_ok=True)
    manifest = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (ROOT / "src").rglob("*.py")}
    write_json(output / "source-manifest.json", manifest)
    for relative in manifest:
        dest = output / "source-snapshot" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, dest)
    team, rows = "upgrade_v5", []
    for cid, original in (("C-81", "live-v4"), ("E-81", "live-v3"), ("E-82", "live-v3")):
        if args.cases and cid not in args.cases:
            continue
        started = time.monotonic()
        old = output.parent / original
        payload = next(c for c in json.loads((old / "cases.json").read_text(encoding="utf-8")) if c["case_id"] == cid)
        pointer = read_json(old / f"data/upgrade_v4/work/receipts/{cid}.json")
        receipt_path = old / pointer["receipt"]
        receipt = read_json(receipt_path)
        evidence = read_json(receipt_path.parent / "evidence.json")
        if receipt["source_case"] != payload:
            raise ValueError("case changed since evidence collection")
        settings = Settings(ROOT, output, team, [ROOT / "paper_library/pdfs"])
        materials = MaterialResolver(settings).resolve(payload["materials"])
        source_hashes = {c["filename"]: c["document_sha256"] for c in evidence["chunks"]}
        if any(source_hashes.get(m.filename) != m.sha256 for m in materials):
            raise ValueError("materials changed since evidence collection")
        class DraftReplayGateway(GatewayLLM):
            def json(self, stage, data, **kwargs):
                if stage == "report" and args.reuse_drafts_from:
                    old_output = output.parent / args.reuse_drafts_from
                    old_pointer = read_json(old_output / f"data/upgrade_v5/work/receipts/{cid}.json")
                    old_receipt = old_output / old_pointer["receipt"]
                    old_proof = read_json(old_receipt)
                    if old_proof["source_case"] != payload:
                        raise ValueError("draft belongs to another case")
                    old_evidence = read_json(old_receipt.parent / "evidence.json")
                    if old_evidence["claims"] != evidence["claims"]:
                        raise ValueError("draft evidence changed")
                    draft = old_evidence["report_trace"][0]
                    self.usage.append({"stage": "report_replay", "tokens": 0, "usage_source": "reused_authorized_draft",
                                       "receipt": old_receipt.relative_to(ROOT).as_posix()})
                    return {"markdown": draft["raw_markdown"], "used_evidence_ids": draft["used_evidence_ids"]}
                return super().json(stage, data, **kwargs)

        llm = DraftReplayGateway(token_budget=20000, timeout=180)
        handler = TaskHandlers(llm, team)
        handler.ledger = [Evidence(**row) for row in evidence["claims"]]
        handler.selected_chunks = [Chunk(**row) for row in evidence["chunks"]]
        handler.coverage, handler.evidence_audit = evidence["coverage"], evidence["evidence_audit"]
        case = CaseContext.from_dict(payload)
        try:
            with CaseSession(cid, 180).activate():
                result = handler.compose_report(case)
                engine = ResearchAgentEngine(settings)
                engine._persist(case, result, output / f"data/{team}/work/{cid}/composition",
                                [m.filename for m in materials], None, None, handler, llm)
            text = result["markdown"]
            row = {"case_id": cid, "complete": True, "audit": handler.audit,
                   "unrequested_idea_section": bool(re.search(r"^#{1,3}\s+.*(?:研究构想|Idea|研究方案)", text, re.M))
                       if case.function == "Survey" else None,
                   "placeholder_budget": bool(re.search(r"\b[NM]\s*次|具体预算待定", text)),
                   "source_evidence_receipt": receipt_path.relative_to(ROOT).as_posix()}
        except Exception as exc:
            row = {"case_id": cid, "complete": False, "message": str(exc)[:1200], "error_type": type(exc).__name__}
            write_json(output / f"errors/{cid}.json", {"audit": handler.audit, "trace": handler.report_trace,
                                                       "error": row})
        row.update(seconds=round(time.monotonic() - started, 2), tokens=llm.total_tokens, usage=llm.usage)
        rows.append(row)
        write_json(output / "summary.json", {"label": "已核验证据的写作定向复测，非完整仿真或官方成绩",
                                            "reused_drafts_from": args.reuse_drafts_from, "cases": rows})
        print(json.dumps({key: row[key] for key in ("case_id", "complete", "seconds", "tokens")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
