"""公开worker + 独立本地方法夹具的差分测试；不计算或伪装官方分数。

在无网络、Python3.9、1CPU/1GiB容器中运行。输入由公开样例和固定种子的
合成场景组成，算法源码从指定audit目录读取，不调用模型、不修改源码。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import time

from research_agent.workflows.d_algorithms.d01 import run_d01
from research_agent.workflows.d_algorithms.d02 import run_d02
from research_agent.workflows.d_algorithms.d05 import run_d05
from research_agent.workflows.d_public_cases import derived_public_cases
from research_agent.workflows.sample_validation import validate_sample_output


def generated_cases(case_id, protocol, count):
    rng = random.Random(414)
    # 中英文之外，协议明确规定只提取[a-z0-9]+；标点、数字和空文档也加入测试。
    vocab = [f"term{i}" for i in range(30)] + ["quantum", "enzyme", "finance", "soil", "模型", "!!!"]
    def text(length):
        return " ".join(rng.choices(vocab, k=length))
    for index in range(count):
        if case_id == "D-01":
            classes = ["biology", "physics", "economics"][:2 + index % 2]
            train = []
            for i in range(12 + index * 2):
                label = classes[i % len(classes)] if index % 2 == 0 else rng.choices(classes, [8, 1, 1][:len(classes)])[0]
                train.append({"text": (label + " ") * (i % 4) + text(i % 15), "label": label})
            data = {"train": train, "texts": ["", "UNSEEN9981", *[text(i) for i in range(1, 14)]]}
        elif case_id == "D-02":
            documents = [{"id": f"doc{i:03}", "title": text(i % 6), "text": text((i * 13) % 90)}
                         for i in range(8 + index * 2)]
            queries = [{"id": f"q{i}", "text": text(i % 7), "group": "rare" if i == 0 else "frequent"}
                       for i in range(7)]
            dev = [dict(q, relevant=rng.sample([d["id"] for d in documents], 1 + i % 3)) for i, q in enumerate(queries)]
            data = {"documents": documents, "dev_queries": dev,
                    "test_queries": [dict(q, id="test-" + q["id"]) for q in queries]}
        else:
            flows = {"a": .5, "b": 1, "c": 3, "unused": 2}
            jobs = []
            horizon = [8, 15, 40][index % 3]
            for i in range(index * 3):
                arrival = rng.randrange(horizon + 5)
                jobs.append({"id": f"job{i:03}", "flow": rng.choice(["a", "b", "c"]),
                             "arrival_ms": arrival, "service_ms": rng.randint(1, 8),
                             "deadline_ms": arrival + rng.choice([0, 1, 4, 30])})
            rng.shuffle(jobs)
            data = {"jobs": jobs, "flow_weights": flows, "servers": 1 + index % 3,
                    "setup_ms": index % 4, "horizon_ms": horizon}
        yield f"synthetic-{index:02}", {"case_id": case_id, "phase": "run", "protocol": protocol, "data": data}


def compare(case_id, actual, expected):
    if case_id == "D-01":
        assert actual["baseline"] == expected["baseline"], "majority predictions differ"
        assert actual["candidate"] == expected["candidate"], "trained predictions differ from local method fixture"
    elif case_id == "D-02":
        for key in ("baseline", "candidate"):
            if key in expected:
                if expected[key] is None:
                    assert actual[key] is None, 'unexpected candidate result'
                    continue
                assert actual[key].keys() == expected[key].keys(), key + " query coverage differs"
                for query, ranking in actual[key].items():
                    scores = {row['id']: row['score'] for row in expected[key][query]}
                    assert {row['id'] for row in ranking} == scores.keys(), key + " document coverage differs"
                    for left, right in zip(ranking, ranking[1:]):
                        a, b = scores[left['id']], scores[right['id']]
                        assert b - a <= 1e-12 * max(1, abs(a), abs(b)), key + " BM25 ranking differs"
        if "selected" in expected.get("details", {}):
            assert actual["details"]["selected"] == expected["details"]["selected"], "group-robust selection differs"
    else:
        def equal(a, b):
            if isinstance(b, dict):
                return isinstance(a, dict) and a.keys() == b.keys() and all(equal(a[k], b[k]) for k in b)
            if isinstance(b, list):
                return isinstance(a, list) and len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
            if type(b) in (int, float):
                return type(a) in (int, float) and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
            return a == b
        for name in ("baseline", "candidate"):
            assert equal(actual[name], expected[name]), name + " full scheduling trace/drop/service differs"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--materials", type=Path, default=Path("/materials"))
    parser.add_argument("--count", type=int, default=15)
    parser.add_argument("--case-ids", default="D-01,D-02,D-05")
    parser.add_argument("--allow-baseline", action="store_true", help="Explicitly validate honest baseline-only cases without claiming candidate completion")
    args = parser.parse_args()
    output = args.audit / "differential"
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for case_id in args.case_ids.split(","):
        protocol = json.loads((args.materials / case_id / "protocol.json").read_text(encoding="utf-8"))
        sample = json.loads((args.materials / case_id / "sample.json").read_text(encoding="utf-8"))
        sample["protocol"] = protocol
        public = args.materials / "公开样例请求"
        cases = [("public-" + path.stem, json.loads(path.read_text(encoding="utf-8"))["payload"])
                 for path in [public / (case_id + ".json"), *sorted((public / case_id).glob("*.json"))]]
        cases.extend(derived_public_cases(case_id, sample))
        cases.extend(generated_cases(case_id, protocol, args.count))
        cases.append(("repeat-public", copy.deepcopy(sample)))
        oracle = {"D-01": run_d01, "D-02": run_d02, "D-05": run_d05}[case_id]
        source = args.audit / case_id / "implementation.py"
        with tempfile.TemporaryDirectory(prefix="d-public-") as directory:
            root = Path(directory)
            shutil.copytree(args.materials / case_id, root, dirs_exist_ok=True)
            shutil.copyfile(source, root / "implementation.py")
            for label, request in cases:
                request["protocol"] = protocol
                stage = request.get("phase", "run")
                row = {"case_id": case_id, "input": label, "phase": stage,
                       "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
                started = time.monotonic()
                try:
                    result = subprocess.run([sys.executable, "-B", str(root / "worker.py")],
                        input=json.dumps({"stage": stage, "payload": request}),
                        text=True, encoding="utf-8", capture_output=True, timeout=20, cwd=root,
                        env={**os.environ, "PYTHONHASHSEED": "42", "PYTHONDONTWRITEBYTECODE": "1"})
                    row["worker_seconds"] = round(time.monotonic() - started, 4)
                    (output / f"{case_id}-{label}.request.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
                    (output / f"{case_id}-{label}.stdout.json").write_text(result.stdout, encoding="utf-8")
                    assert result.returncode == 0, result.stderr[-2000:]
                    actual = json.loads(result.stdout)
                    if stage == "run":
                        validate_sample_output(case_id, request, actual)
                        row['candidate_present'] = actual['candidate'] is not None
                        assert row['candidate_present'] or args.allow_baseline, "only baseline produced"
                        if case_id == 'D-02' and actual['candidate'] is not None:
                            rank_request = dict(request, phase='rank', data=dict(request['data'], selected=actual['details']['selected']))
                            rank_result = subprocess.run([sys.executable, '-B', str(root / 'worker.py')],
                                input=json.dumps({'stage':'rank', 'payload':rank_request}),
                                text=True, encoding='utf-8', capture_output=True, timeout=20, cwd=root,
                                env={**os.environ, 'PYTHONHASHSEED':'42', 'PYTHONDONTWRITEBYTECODE':'1'})
                            assert rank_result.returncode == 0, rank_result.stderr[-2000:]
                            compare(case_id, json.loads(rank_result.stdout), {'candidate':actual['candidate']})
                            row['selected_rank_reproduced'] = True
                    expected = oracle(request)
                    if stage == 'run' and actual['candidate'] is None and args.allow_baseline:
                        expected = dict(expected, candidate=None, details={})
                    compare(case_id, actual, expected)
                    row["passed"] = True
                except Exception as exc:
                    row.update(passed=False, error=f"{type(exc).__name__}: {exc}")
                rows.append(row)
                (output / "summary.json").write_text(json.dumps({"official_score": None, "python": sys.version,
                    "scope": "public and synthetic differential checks against local method fixtures",
                    "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(row, ensure_ascii=False), flush=True)
    assert all(row["passed"] for row in rows), "some public/synthetic differential checks failed"


if __name__ == "__main__":
    main()
