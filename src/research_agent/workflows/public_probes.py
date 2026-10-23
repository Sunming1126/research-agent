"""由当前题公开协议构造小型边界输入，不提供算法实现或平台答案。"""
from __future__ import annotations

from copy import deepcopy


def public_probes(case_id, request):
    probes = [("provided_sample", request, None)]
    if case_id == "D-02":
        queries = request["data"].get("dev_queries", [])
        if len({q.get("group", "default") for q in queries}) > 1:
            probe = deepcopy(request)
            duplicate = deepcopy(queries[0])
            identifiers = {q["id"] for q in queries}
            duplicate["id"] = str(duplicate["id"]) + "_public_duplicate"
            while duplicate["id"] in identifiers:
                duplicate["id"] += "_copy"
            probe["data"]["dev_queries"].append(duplicate)
            probes.append(("unequal_dev_group_sizes", probe, None))
        # 单词互斥语料：只核对查询是否实际参与检索，不给完整BM25实现。
        probe = deepcopy(request)
        probe["data"] = {
            "documents": [{"id": "doc_z", "title": "", "text": "alpha"},
                          {"id": "doc_a", "title": "", "text": "beta"},
                          {"id": "doc_m", "title": "", "text": "gamma"}],
            "dev_queries": [{"id": "qa", "text": "alpha", "group": "a", "relevant": ["doc_z"]},
                            {"id": "qb", "text": "beta", "group": "b", "relevant": ["doc_a"]}],
            "test_queries": [{"id": "qa", "text": "alpha"}, {"id": "qb", "text": "beta"}],
        }
        probes.append(("query_changes_ranking", probe, {"d02_expected_top": {"qa": "doc_z", "qb": "doc_a"}}))
        return probes
    if case_id != "D-05":
        return probes
    flows = list(request["data"]["flow_weights"])
    if not flows:
        return probes
    first, second = flows[0], flows[min(1, len(flows) - 1)]

    def job(identifier, flow, arrival, service, deadline=30):
        return {"id": identifier, "flow": flow, "arrival_ms": arrival,
                "service_ms": service, "deadline_ms": deadline}

    cases = [
        ("empty_jobs", [], 1, 2, 12, []),
        # 空闲服务器的旧完成时刻不得阻止时钟跳到下一次到达。
        ("idle_gap_and_switch", [job("probe_a", first, 0, 1), job("probe_b", second, 5, 1)],
         1, 2, 12, ["probe_a", "probe_b"]),
        ("future_first_arrival", [job("probe_future", first, 5, 1)], 2, 0, 12, ["probe_future"]),
        ("simultaneous_reservation", [job("probe_a", first, 0, 2), job("probe_b", first, 0, 2),
                                      job("probe_c", second, 0, 2)], 2, 0, 12,
         {"states": {"probe_a": "completed", "probe_b": "completed", "probe_c": "completed"},
          "trace_ids": {"baseline": ["probe_a", "probe_b", "probe_c"],
                        "candidate": ["probe_a", "probe_c", "probe_b"] if len(flows) > 1 else
                                     ["probe_a", "probe_b", "probe_c"]}}),
        ("deadline_setup_and_window", [job("probe_a", first, 0, 1), job("probe_b", second, 1, 1, 3),
                                        job("probe_c", first, 1, 3)], 1, 2, 4, None),
        ("window_exact_and_pending", [job("probe_a", first, 0, 4), job("probe_b", second, 0, 5)],
         1, 0, 4, {"states": {"probe_a": "completed", "probe_b": "horizon"}}),
        ("window_future_arrivals", [job("probe_a", first, 0, 4), job("probe_b", second, 4, 1),
                                     job("probe_c", first, 7, 1)],
         1, 0, 4, {"states": {"probe_a": "completed", "probe_b": "horizon", "probe_c": "horizon"}}),
    ]
    if len(flows) > 1:
        # 单流没有切换开销，不能构造此双条件冲突；避免自测误拒有效单流算法。
        cases.append(("deadline_before_horizon", [job("probe_a", first, 0, 2), job("probe_b", second, 1, 2, 3)],
                      1, 3, 6, {"states": {"probe_a": "completed", "probe_b": "deadline"}}))
    for name, jobs, servers, setup, horizon, completed in cases:
        probe = deepcopy(request)
        probe["data"].update(jobs=jobs, servers=servers, setup_ms=setup, horizon_ms=horizon)
        probes.append((name, probe, completed))
    return probes
