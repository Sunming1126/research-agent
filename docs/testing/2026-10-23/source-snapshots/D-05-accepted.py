import heapq


def _number(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
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
            "arrival_ms": _number(raw.get("arrival_ms", 0.0), 0.0),
            "service_ms": max(0.0, _number(raw.get("service_ms", 0.0), 0.0)),
            "deadline_ms": _number(raw.get("deadline_ms", float("inf")), float("inf")),
        })

    jobs.sort(key=lambda j: (j["arrival_ms"], str(j["id"])))

    raw_weights = data.get("flow_weights", {})
    weights = {}
    if isinstance(raw_weights, dict):
        for flow, weight in raw_weights.items():
            value = _number(weight, 1.0)
            if value <= 0:
                value = 1.0
            weights[flow] = value

    server_count = int(_number(data.get("servers", 1), 1.0))
    if server_count < 1:
        server_count = 1

    setup_ms = max(0.0, _number(data.get("setup_ms", 0.0), 0.0))
    horizon_ms = _number(data.get("horizon_ms", float("inf")), float("inf"))
    if horizon_ms != horizon_ms:
        horizon_ms = float("inf")

    return jobs, weights, server_count, setup_ms, horizon_ms


def _weight_of(weights, flow):
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
    pending = list(jobs)
    next_arrival = 0

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

    while True:
        while next_arrival < len(pending) and pending[next_arrival]["arrival_ms"] <= 0.0:
            entry = (pending[next_arrival]["arrival_ms"], next_arrival, pending[next_arrival])
            heapq.heappush(waiting, entry)
            next_arrival += 1

        now = None
        if waiting:
            now = 0.0
        if next_arrival < len(pending):
            arrival_time = pending[next_arrival]["arrival_ms"]
            if now is None or arrival_time < now:
                now = arrival_time
        for server in servers:
            if server["busy"]:
                if now is None or server["end_ms"] < now:
                    now = server["end_ms"]

        if now is None:
            break
        if now > horizon_ms:
            break

        for server in servers:
            if server["busy"] and server["end_ms"] <= now:
                server["busy"] = False

        while next_arrival < len(pending) and pending[next_arrival]["arrival_ms"] <= now:
            entry = (pending[next_arrival]["arrival_ms"], next_arrival, pending[next_arrival])
            heapq.heappush(waiting, entry)
            next_arrival += 1

        kept = []
        while waiting:
            entry = heapq.heappop(waiting)
            job = entry[2]
            if job["deadline_ms"] <= now:
                dropped.append({"id": job["id"], "reason": "deadline"})
            else:
                kept.append(entry)
        waiting = kept

        if now > horizon_ms:
            break

        dispatched_any = False
        for server in servers:
            if server["busy"]:
                continue
            while waiting:
                chosen_index = None
                chosen_key = None
                for position in range(len(waiting)):
                    entry = waiting[position]
                    job = entry[2]
                    if fair:
                        key = (
                            allocated[job["flow"]] / _weight_of(weights, job["flow"]),
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
                job = entry[2]

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
                dispatched_any = True
                break
            if not dispatched_any:
                continue

        if not dispatched_any:
            # No server could accept any waiting job at this instant.
            # Find the strictly earliest future event.
            future = None
            if next_arrival < len(pending):
                arrival_time = pending[next_arrival]["arrival_ms"]
                if arrival_time > now and (future is None or arrival_time < future):
                    future = arrival_time
            for server in servers:
                if server["busy"] and server["end_ms"] > now:
                    if future is None or server["end_ms"] < future:
                        future = server["end_ms"]
            if future is None:
                break
            if future > horizon_ms:
                break
            # Fast-forward is handled by the next loop iteration choosing
            # now = min(argmin of waiting arrival/completion). Since waiting
            # jobs already exist, the loop would recompute now = 0.0; so we
            # must discard waiting jobs' arrival-based now by only using
            # completion/arrival events when no dispatchable job exists.
            # Emulate the jump directly:
            jump = future
            for server in servers:
                if server["busy"] and server["end_ms"] <= jump:
                    server["busy"] = False
            while next_arrival < len(pending) and pending[next_arrival]["arrival_ms"] <= jump:
                entry = (pending[next_arrival]["arrival_ms"], next_arrival, pending[next_arrival])
                heapq.heappush(waiting, entry)
                next_arrival += 1
            kept = []
            while waiting:
                entry = heapq.heappop(waiting)
                job = entry[2]
                if job["deadline_ms"] <= jump:
                    dropped.append({"id": job["id"], "reason": "deadline"})
                else:
                    kept.append(entry)
            waiting = kept
            if jump > horizon_ms:
                break
            # after jump attempt dispatch once more
            dispatched_any2 = False
            for server in servers:
                if server["busy"]:
                    continue
                while waiting:
                    chosen_index = None
                    chosen_key = None
                    for position in range(len(waiting)):
                        entry = waiting[position]
                        job = entry[2]
                        if fair:
                            key = (
                                allocated[job["flow"]] / _weight_of(weights, job["flow"]),
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
                    job = entry[2]
                    if server["last_flow"] is not None and server["last_flow"] != job["flow"]:
                        setup = setup_ms
                    else:
                        setup = 0.0
                    dispatch_ms = jump
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
                    dispatched_any2 = True
                    break
            if not dispatched_any2 and not waiting:
                # nothing to do; continue to let event loop find next event
                pass

        # Check termination: no busy servers, no waiting, no pending
        if not waiting and not any(s["busy"] for s in servers) and next_arrival >= len(pending):
            break

    for entry in waiting:
        job = entry[2]
        dropped.append({"id": job["id"], "reason": "horizon"})
    while next_arrival < len(pending):
        job = pending[next_arrival]
        dropped.append({"id": job["id"], "reason": "horizon"})
        next_arrival += 1

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
