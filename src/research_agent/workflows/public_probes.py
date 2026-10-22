"""由当前题公开协议构造小型边界输入，不提供算法实现或平台答案。"""
from __future__ import annotations

from copy import deepcopy


def public_probes(case_id, request):
    probes = [("provided_sample", request, None)]
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
         ["probe_a", "probe_b", "probe_c"]),
        ("deadline_setup_and_window", [job("probe_a", first, 0, 1), job("probe_b", second, 1, 1, 3),
                                        job("probe_c", first, 1, 3)], 1, 2, 4, None),
    ]
    for name, jobs, servers, setup, horizon, completed in cases:
        probe = deepcopy(request)
        probe["data"].update(jobs=jobs, servers=servers, setup_ms=setup, horizon_ms=horizon)
        probes.append((name, probe, completed))
    return probes
