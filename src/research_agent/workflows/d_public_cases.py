"""由公开协议构造小型边界输入；没有隐藏标签、参考指标或提交用算法。"""
from __future__ import annotations

import copy


def derived_public_cases(case_id, request):
    if case_id == "D-01":
        edge = copy.deepcopy(request)
        train = edge["data"]["train"]
        if train:
            # 不依赖公开样例的学科、类别名称、文档长度或均衡分布。
            train.extend(copy.deepcopy(train[:1]) * 3)
            edge["data"]["texts"] = ["", "UNSEENZZ9981?!", train[0]["text"] * 2]
        yield "imbalanced_empty_unknown_repeated_text", edge
    elif case_id == "D-02":
        edge = copy.deepcopy(request)
        for i, doc in enumerate(edge["data"]["documents"]):
            doc["title"] = "" if i % 2 else doc.get("title", "")
            doc["text"] = "" if i % 3 == 0 else doc.get("text", "")
        for query in edge["data"].get("dev_queries", []) + edge["data"]["test_queries"]:
            query["text"] = (query["text"] + " ") * 2 + "UNSEENZZ9981"
        dev = edge["data"].get("dev_queries", [])
        if dev:
            duplicate = copy.deepcopy(dev[0])
            duplicate["id"] = "derived-query-" + duplicate["id"]
            while duplicate["id"] in {q["id"] for q in dev}:
                duplicate["id"] += "-copy"
            dev.append(duplicate)
        yield "empty_fields_repeated_unknown_terms_unequal_groups", edge
    elif case_id == "D-05":
        simultaneous = copy.deepcopy(request)
        for job in simultaneous["data"]["jobs"]:
            job["arrival_ms"] = 0
        yield "simultaneous_public_arrivals", simultaneous
        empty = copy.deepcopy(request)
        empty["data"]["jobs"] = []
        yield "empty_public_jobs", empty

        def scenario(rows, *, servers=1, setup=2, horizon=12):
            data = {"jobs": [dict(zip(("id", "flow", "arrival_ms", "service_ms", "deadline_ms"), row))
                             for row in rows],
                    "servers": servers, "setup_ms": setup, "horizon_ms": horizon,
                    "flow_weights": {"a": 0.5, "b": 2, "idle": 1}}
            return dict(request, data=data)

        yield "idle_setup_rejection_retry_window", scenario([
            ("a0", "a", 0, 2, 30), ("b0", "b", 5, 1, 6),
            ("b1", "b", 5, 20, 30), ("a1", "a", 5, 7, 30),
            ("late", "b", 14, 1, 14)])
        yield "three_servers_reserved_service", scenario([
            ("a0", "a", 0, 3, 30), ("a1", "a", 0, 2, 30),
            ("a2", "a", 0, 1, 30), ("b0", "b", 0, 2, 30),
            ("b1", "b", 0, 1, 30), ("b2", "b", 2, 2, 30)], servers=3, setup=0)
        yield "completion_arrival_expiry_same_time", scenario([
            ("a0", "a", 0, 3, 30), ("b0", "b", 0, 1, 3),
            ("a1", "a", 3, 1, 30), ("b1", "b", 3, 1, 4)], setup=0, horizon=5)
