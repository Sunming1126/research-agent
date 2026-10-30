from __future__ import annotations

from collections import defaultdict


def _simulate(data: dict, fair: bool) -> dict:
    jobs = sorted(list(data.get("jobs") or []), key=lambda row: (row["arrival_ms"], row["id"]))
    weights = {str(key): float(value) for key, value in (data.get("flow_weights") or {}).items()}
    servers = int(data.get("servers", 1))
    setup_ms = float(data.get("setup_ms", 0))
    horizon = float(data.get("horizon_ms", 0))
    state = [{"busy_until": 0.0, "last_flow": None} for _ in range(servers)]
    waiting = []
    dropped = []
    trace = []
    served = defaultdict(float)
    allocated = defaultdict(float)
    next_index = 0
    now = 0.0

    def add_arrivals():
        nonlocal next_index
        while next_index < len(jobs) and jobs[next_index]["arrival_ms"] <= now:
            waiting.append(jobs[next_index])
            next_index += 1

    def drop_expired():
        keep = []
        for job in waiting:
            if job["deadline_ms"] <= now:
                dropped.append({"id": job["id"], "reason": "deadline"})
            else:
                keep.append(job)
        waiting[:] = keep

    while next_index < len(jobs) or waiting or any(server["busy_until"] > now for server in state):
        if now >= horizon:
            break
        if not waiting and not any(server["busy_until"] <= now for server in state):
            future = [job["arrival_ms"] for job in jobs[next_index:]]
            future.extend(server["busy_until"] for server in state if server["busy_until"] > now)
            if not future:
                break
            now = min(future)
        if now >= horizon:
            break
        add_arrivals()
        drop_expired()
        free = [index for index, server in enumerate(state) if server["busy_until"] <= now]
        while waiting and free:
            server_index = min(free)
            free.remove(server_index)
            server = state[server_index]
            if fair:
                position = min(range(len(waiting)), key=lambda pos: (
                    (allocated[waiting[pos]["flow"]] / weights[waiting[pos]["flow"]]),
                    waiting[pos]["arrival_ms"], waiting[pos]["id"],
                ))
            else:
                position = min(range(len(waiting)), key=lambda pos: (waiting[pos]["arrival_ms"], waiting[pos]["id"]))
            job = waiting.pop(position)
            setup = 0.0 if server["last_flow"] is None or server["last_flow"] == job["flow"] else setup_ms
            start = now + setup
            end = start + float(job["service_ms"])
            if start >= job["deadline_ms"]:
                dropped.append({"id": job["id"], "reason": "deadline"})
                free.append(server_index)
                continue
            if end > horizon:
                dropped.append({"id": job["id"], "reason": "horizon"})
                free.append(server_index)
                continue
            allocated[job["flow"]] += float(job["service_ms"])
            trace.append({**job, "server": server_index, "dispatch_ms": now, "setup_ms": setup,
                          "start_ms": start, "end_ms": end, "wait_ms": start - job["arrival_ms"]})
            state[server_index] = {"busy_until": end, "last_flow": job["flow"]}
            served[job["flow"]] += float(job["service_ms"])
        if waiting or next_index < len(jobs) or any(server["busy_until"] > now for server in state):
            future = [job["arrival_ms"] for job in jobs[next_index:] if job["arrival_ms"] > now]
            future.extend(server["busy_until"] for server in state if server["busy_until"] > now)
            if future:
                now = min(future)
            elif waiting:
                # 所有等待任务都在当前时刻被 deadline/horizon 规则处理。
                now = horizon
            else:
                break
    # 窗口结束后，尚未进入 trace/dropped 的任务统一记录为 horizon。
    known = {row["id"] for row in trace} | {row["id"] for row in dropped}
    for job in jobs:
        if job["id"] not in known:
            dropped.append({"id": job["id"], "reason": "horizon"})
    return {
        "trace": sorted(trace, key=lambda row: (row["dispatch_ms"], row["server"], row["id"])),
        "served_ms": {flow: served.get(flow, 0.0) for flow in sorted(weights)},
        "dropped": sorted(dropped, key=lambda row: row["id"]),
    }


def run_d05(request: dict) -> dict:
    """按 D-05 protocol 返回 FIFO 与加权公平的实际任务轨迹。"""
    if request.get("phase", "run") != "run":
        raise ValueError("D-05 only supports phase=run")
    data = request.get("data") or {}
    if not isinstance(data.get("jobs"), list):
        raise ValueError("D-05 requires jobs")
    return {"baseline": _simulate(data, fair=False), "candidate": _simulate(data, fair=True), "details": {}}

