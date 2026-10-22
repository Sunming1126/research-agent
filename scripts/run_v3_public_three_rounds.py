"""使用新版官方公开输入运行三轮 layered-d-v3 算法烟测。"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

from research_agent.workflows.d_algorithms import run_d01, run_d02, run_d05


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "科研助手赛题材料" / "x新" / "research-experiments"
OUTPUT = ROOT / "output" / "v3-public-three-rounds"
RUNNERS = {"D-01": run_d01, "D-02": run_d02, "D-05": run_d05}


def read_request(relative: str) -> dict:
    value = json.loads((PACKAGE / "公开样例请求" / relative).read_text(encoding="utf-8"))
    # 新版公开边界样例外层 stage/payload 与平台夹具一致，算法只接收 payload。
    return value.get("payload", value)


def check_result(case_id: str, request: dict, result: dict) -> None:
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    if case_id == "D-01":
        assert len(result["baseline"]) == len(request["data"]["texts"])
        if result.get("candidate") is not None:
            assert len(result["candidate"]) == len(request["data"]["texts"])
    elif case_id == "D-02":
        rankings = result["candidate"]
        assert set(rankings) == {row["id"] for row in request["data"]["test_queries"]}
        for rows in rankings.values():
            assert len(rows) == len(request["data"]["documents"])
    else:
        ids = {row["id"] for row in request["data"]["jobs"]}
        for run in (result["baseline"], result["candidate"]):
            seen = [row["id"] for row in run["trace"]] + [row["id"] for row in run["dropped"]]
            assert set(seen) == ids and len(seen) == len(ids)


def main() -> None:
    cases = [
        {"D-01": "D-01/不均衡-重复词-标点.json", "D-02": "D-02/空字段-重复未知词-不等组.json", "D-05": "D-05/并发预留.json"},
        {"D-01": "D-01/均衡-空文本-未知词.json", "D-02": "D-02/rank-default.json", "D-05": "D-05/过期与同时到达.json"},
        {"D-01": "D-01.json", "D-02": "D-02.json", "D-05": "D-05.json"},
    ]
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    summaries = []
    for round_no, mapping in enumerate(cases, start=1):
        round_dir = OUTPUT / f"round-{round_no:02d}"
        round_dir.mkdir(parents=True)
        rows = []
        for case_id, relative in mapping.items():
            request = read_request(relative)
            result = RUNNERS[case_id](request)
            check_result(case_id, request, result)
            target = round_dir / f"{case_id}.json"
            target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            rows.append({"case_id": case_id, "source": relative, "status": "passed"})
        summary = {"round": round_no, "cases": rows, "passed": len(rows) == 3}
        (round_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summaries.append(summary)
    report = {"protocol": "layered-d-v3", "seed": 42, "rounds": summaries, "all_passed": all(row["passed"] for row in summaries)}
    (OUTPUT / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

