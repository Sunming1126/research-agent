def run_experiment(request):
    return request["x"] + 10 if request["phase"] == "rank" else 0
