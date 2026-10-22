"""给第 5 轮第三次 E 单题定向试验计算独立诊断分。"""

from __future__ import annotations

import json

from .benchmark import ROOT
from .scoring import score_idea


def main() -> None:
    out = ROOT / "output" / "library-evaluation" / "round-5-followup3"
    result = json.loads((out / "data" / "lib_eval_r5_followup3" / "E-05.json").read_text(encoding="utf-8"))
    judgment = json.loads((ROOT / "evaluation" / "judgments" /
                           "library-round-5-followup3-E.json").read_text(encoding="utf-8"))["E-05"]
    if result.get("case_id") != "E-05" or "## 参考文献" not in result.get("markdown", ""):
        raise ValueError("E-05 artifact is missing or malformed")
    if set(judgment["covered"]) != set(judgment["evidence"]):
        raise ValueError("judgment requires a text location for each scored criterion")
    score = {"label": "E 单题定向诊断分，非完整轮次和非官方榜分",
             "case_id": "E-05", "score": score_idea(set(judgment["covered"])),
             "maximum": 15.0, "covered": judgment["covered"],
             "quality_caveat": judgment["review_notes"]["quality_caveat"]}
    (out / "local-idea-score.json").write_text(json.dumps(score, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(score, ensure_ascii=False))


if __name__ == "__main__":
    main()
