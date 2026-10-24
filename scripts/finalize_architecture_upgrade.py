"""按最终代码回读诊断产物，复查真实源码公开样例并汇总验证边界。"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from research_agent import __version__
from research_agent.core.models import CaseContext, Evidence
from research_agent.core.utils import read_json, sha256_file, write_json, source_quote_matches
from research_agent.contracts.validation import validate_result
from research_agent.evidence.reporting import validate_citations
from research_agent.workflows.sample_validation import run_public_sample


def main():
    base = ROOT / "output/architecture-upgrade-20261022"
    selected = [("A-81", "live-final-a", "upgrade_v4"), ("B-81", "live-v1", "upgrade_v4"),
                ("B-82", "live-v3", "upgrade_v4"), ("C-81", "composition-v5-review", "upgrade_v5"),
                ("E-81", "composition-v5-review", "upgrade_v5"), ("E-82", "composition-v5-bio-final", "upgrade_v5")]
    rows = []
    for cid, folder, team in selected:
        out = base / folder
        pointer = read_json(out / f"data/{team}/work/receipts/{cid}.json")
        receipt_path = out / pointer["receipt"]
        receipt = read_json(receipt_path)
        case = CaseContext.from_dict(receipt["source_case"])
        result = read_json(out / f"data/{team}/{cid}.json")
        for artifact in receipt["artifacts"]:
            assert sha256_file(out / artifact["path"]) == artifact["sha256"], (cid, artifact["path"])
        validate_result(case, result, out, team=team, filenames=receipt["filenames"], readback=True)
        evidence = read_json(receipt_path.parent / "evidence.json")
        if case.function in {"Survey", "Idea"}:
            ledger = [Evidence(**e) for e in evidence["claims"]]
            refs = {name: i+1 for i, name in enumerate(sorted({e.filename for e in ledger}))}
            chunks = {c["chunk_id"]: c for c in evidence["chunks"]}
            for e in ledger:
                assert source_quote_matches(e.quote, chunks[e.chunk_id]["text"]), (cid, e.evidence_id)
            validate_citations(result["markdown"].split("## 参考文献")[0],
                [{**asdict(e), "ref": refs[e.filename]} for e in ledger], [e.evidence_id for e in ledger],
                minimum=5 if case.function == "Survey" else 1)
        row = {"case_id": cid, "run": folder, "artifact_readback": True, "hashes_valid": True,
               "audit": evidence.get("audit"), "receipt": receipt_path.relative_to(ROOT).as_posix()}
        rows.append(row)
    material_root = ROOT / "科研助手赛题材料/x新/research-experiments-20261020-source-v1"
    for cid, folder in (("D-01", "live-v1"), ("D-02", "live-v2"), ("D-05", "live-v1")):
        source = base / folder / "public-source-selftest" / cid / "implementation.py"
        materials = {key: (material_root / cid / filename).read_text(encoding="utf-8")
                     for key, filename in (("protocol", "protocol.json"), ("sample", "sample.json"))}
        report = run_public_sample(source, materials, cid)
        rows.append({"case_id": cid, "scope": "current_checker_replay_of_real_gateway_source",
                     "source_sha256": sha256_file(source), "selftest": report, "official_credentials_verified": False})
    manifest = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (ROOT / "src").rglob("*.py")}
    write_json(base / "final-source-manifest.json", manifest)
    packages = {}
    for name in ("openai", "PyMuPDF", "pypdf", "numpy", "matplotlib", "Pillow", "pytest"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    write_json(base / "final-validation.json", {"version": __version__,
        "label": "历史独立诊断产物按当前代码回读；非一轮全新竞赛或官方分数",
        "cases": rows, "python": sys.version, "platform": platform.platform(), "packages": packages,
        "new_python_dependencies": [], "optional_system_dependency": "libseccomp on Linux self-test worker",
        "docker_engine_available": False, "official_source_credentials_verified": False})
    print(json.dumps({"version": __version__, "artifact_cases": len(selected), "public_source_cases": 3,
                      "valid": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
