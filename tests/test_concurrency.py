from concurrent.futures import ThreadPoolExecutor


def test_parallel_sub_agents_share_one_budget_without_losing_spend(supervisor):
    task = supervisor.create_task("parallel", budget_usd=100)
    runs = [supervisor.create_run(task) for _ in range(6)]

    def worker(run_id):
        for i in range(1, 21):
            supervisor.ingest(run_id, {"event_id": f"{run_id}-{i}", "seq": i, "type": "model.call",
                                       "data": {"model": "demo-model", "input_tokens": 10_000, "output_tokens": 0}})
            supervisor.decide(run_id, before_model_call=True)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(worker, runs))     # raises if any thread hit a deadlock or error

    totals = supervisor.task_spend(task)
    assert totals["calls"] == 120
    assert totals["estimated_micros"] == 120 * 30_000   # every call counted exactly once


def test_parallel_runs_stop_at_the_shared_budget(supervisor):
    task = supervisor.create_task("parallel", budget_usd=0.5)   # ~16 calls of $0.03
    runs = [supervisor.create_run(task) for _ in range(4)]

    def worker(run_id):
        sent = 0
        for i in range(1, 21):
            if not supervisor.decide(run_id, before_model_call=True)["allowed"]:
                break
            supervisor.ingest(run_id, {"event_id": f"{run_id}-{i}", "seq": i, "type": "model.call",
                                       "data": {"model": "demo-model", "input_tokens": 10_000, "output_tokens": 0}})
            sent += 1
        return sent

    with ThreadPoolExecutor(max_workers=4) as pool:
        sent = sum(pool.map(worker, runs))

    # Each run may have one call in flight when the budget is crossed (estimates, not
    # reservations), so the overshoot is bounded by the number of parallel runs.
    assert 17 <= sent <= 17 + len(runs)
    assert all(not supervisor.decide(r, before_model_call=True)["allowed"] for r in runs)
