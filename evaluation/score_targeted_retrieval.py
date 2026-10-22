"""对限页索引的第 4、5 轮 B 单题分别计分，不合并成整轮成绩。"""

from __future__ import annotations

import json

from .benchmark import ROOT
from .library_benchmark import LIBRARY_ROUNDS
from .scoring import score_retrieval


def main() -> None:
    for number in (4, 5):
        spec = next(row for row in LIBRARY_ROUNDS if row["number"] == number)
        out = ROOT / "output" / "library-evaluation" / f"round-{number}-followup3"
        path = out / "data" / f"lib_eval_r{number}_followup3" / f"B-{number:02d}.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("case_id") != f"B-{number:02d}":
            raise ValueError("B case_id mismatch")
        returned = [row["filename"] for row in result["papers"]]
        gold = spec["gold_b"]
        score = {"label": "B 单题限页索引诊断分，非完整轮次或官方榜分",
                 "case_id": result["case_id"], "returned": returned,
                 "gold": gold, "score": score_retrieval(returned, gold), "maximum": 15.0}
        (out / "local-retrieval-score.json").write_text(
            json.dumps(score, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(score, ensure_ascii=False))


if __name__ == "__main__":
    main()
