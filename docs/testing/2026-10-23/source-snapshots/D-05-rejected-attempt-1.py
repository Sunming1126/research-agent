import heapq


def _number(value, default=0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def _normalize(request):
    if not isinstance(request, dict):
        request = {}
    data = request.get("data", {})
    if not isinstance(data, dict):
        data = {}

    raw_jobs = data.get("jobs", [])
    if not isinstance(raw_jobs, (list, tuple)):
        raw_jobs = []

    jobs = []
    for index, raw in enumerate(raw_jobs):
        if not isinstance(raw, dict):
            continue
        job_id = raw.get("id", index)
        flow = raw.get("flow", "")
        jobs.append({
            "id": job_id,
            "flow": flow,
            "arrival_ms": _number(raw.get("arrival_ms", 0)),
            "service_ms": max(0.0, _number(raw.get("service_ms", 0))),
            "deadline_ms": _number(raw.get("deadline_ms", float("inf")), float("inf")),
        })

    jobs.sort(key=lambda j: (j["arrival_ms"], str(j["id"])))

    raw_weights = data.get("flow_weights", {})
    weights = {}
    if isinstance(raw_weights, dict):
        for flow, weight in raw_weights.items():
            weights[flow] = _number(weight, 1.0)
            if weights[flow] <= 0:
                weights[flow] = 1.0

    server_count = int(_number(data.get("servers", 1), 1))
    if server_count < 1:
        server_count = 1

    setup_ms = max(0.0, _number(data.get("setup_ms", 0)))
    horizon_ms = max(0.0, _number(data.get("horizon_ms", float("inf")), float("inf")))

    return jobs, weights, server_count, setup_ms, horizon_ms


def _flow_weight(weights, flow):
    if flow in weights:
        return weights[flow]
    return 1.0


def _simulate(jobs, weights, server_count, setup_ms, horizon_ms, fair):
    flows = {}
    for flow in weights:
        flows[flow] = 0.0
    for job in jobs:
        flows.setdefault(job["flow"], 0.0)

    allocated = {}
    for flow in flows:
        allocated[flow] = 0.0

    waiting = []
    sequence = 0
    for position, job in enumerate(jobs):
        heapq.heappush(waiting, (job["arrival_ms"], position, job["flow"], sequence, job))
        sequence += 1

    servers = []
    for index in range(server_count):
        servers.append({
            "index": index,
            "busy": False,
            "end_ms": 0.0,
            "last_flow": None,
        })

    trace = []
    dropped = []
    pending = None

    while True:
        if pending is not None:
            next_arrival = pending[0][0]
        else:
            next_arrival = None

        next_completion = None
        for server in servers:
            if server["busy"]:
                if next_completion is None or server["end_ms"] < next_completion:
                    next_completion = server["end_ms"]

        if pending is not None:
            now = next_arrival
        elif next_completion is not None:
            now = next_completion
        else:
            break

        if now > horizon_ms:
            break

        for server in servers:
            if server["busy"] and server["end_ms"] <= now:
                server["busy"] = False

        while pending is not None and pending[0][0] <= now:
            entry = heapq.heappop(pending)
            heapq.heappush(waiting, entry)

        remaining = []
        while waiting:
            entry = heapq.heappop(waiting)
            job = entry[4]
            if job["deadline_ms"] <= now:
                dropped.append({"id": job["id"], "reason": "deadline"})
            else:
                remaining.append(entry)
        waiting = remaining

        if now > horizon_ms:
            break

        if fair:
            flow_weights = {}
            for flow in flows:
                flow_weights[flow] = _flow_weight(weights, flow)
        else:
            flow_weights = None

        for server in servers:
            if server["busy"]:
                continue
            if not waiting:
                break

            chosen_index = None
            chosen_key = None
            for position in range(len(waiting)):
                entry = waiting[position]
                job = entry[4]
                if fair:
                    key = (
                        allocated[job["flow"]] / flow_weights[job["flow"]],
                        job["arrival_ms"],
                        str(job["id"]),
                        entry[1],
                    )
                else:
                    key = (job["arrival_ms"], str(job["id"]), entry[1])
                if chosen_key is None or key < chosen_key:
                    chosen_key = key
                    chosen_index = position

            entry = waiting.pop(chosen_index)
            job = entry[4]

            if server["last_flow"] is not None and server["last_flow"] != job["flow"]:
                setup = setup_ms
            else:
                setup = 0.0

            dispatch_ms = now
            start_ms = dispatch_ms + setup

            if start_ms >= job["deadline_ms"]:
                dropped.append({"id": job["id"], "reason": "deadline"})
                continue

            end_ms = start_ms + job["service_ms"]

            if end_ms > horizon_ms:
                dropped.append({"id": job["id"], "reason": "horizon"})
                continue

            server["last_flow"] = job["flow"]
            server["busy"] = True
            server["end_ms"] = end_ms

            allocated[job["flow"]] += job["service_ms"]
            flows[job["flow"]] += job["service_ms"]

            trace.append({
                "id": job["id"],
                "flow": job["flow"],
                "arrival_ms": job["arrival_ms"],
                "service_ms": job["service_ms"],
                "deadline_ms": job["deadline_ms"],
                "server": server["index"],
                "dispatch_ms": dispatch_ms,
                "setup_ms": setup,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "wait_ms": start_ms - job["arrival_ms"],
            })

    waiting.extend(list(pending) if pending is not None else [])
    for entry in waiting:
        job = entry[4]
        dropped.append({"id": job["id"], "reason": "horizon"})

    trace.sort(key=lambda item: (item["dispatch_ms"], item["server"]))
    dropped.sort(key=lambda item: str(item["id"]))

    return {
        "trace": trace,
        "served_ms": flows,
        "dropped": dropped,
    }


def run_experiment(request):
    jobs, weights, server_count, setup_ms, horizon_ms = _normalize(request)

    baseline = _simulate(jobs, weights, server_count, setup_ms, horizon_ms, False)
    candidate = _simulate(jobs, weights, server_count, setup_ms, horizon_ms, True)

    return {
        "baseline": baseline,
        "candidate": candidate,
        "details": {},
    }
