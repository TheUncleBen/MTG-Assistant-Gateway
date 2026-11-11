"""The Forge service counts only jobs that still wait: four cancelled ones do not fill the queue."""

from __future__ import annotations

import importlib.util
import pathlib
import time

SERVICE = pathlib.Path(__file__).parent.parent / "docker" / "forge" / "forge_service.py"


def _service():
    spec = importlib.util.spec_from_file_location("forge_service_under_test", SERVICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cancelled_jobs_still_in_the_queue_do_not_count():
    svc = _service()
    jobs = [svc.Job([["1 Forest"]] * 2, 1, "commander", None, 1, None) for _ in range(svc.MAX_QUEUE)]
    for job in jobs:
        svc.JOBS[job.id] = job
        svc.QUEUE.put(job)  # no worker runs here, so they stay in the queue as on a busy service
    assert svc.queued_count() == svc.MAX_QUEUE
    for job in jobs:  # what DELETE /jobs/{id} does to a waiting job
        job.cancelled, job.state, job.finished = True, "cancelled", time.time()
    assert svc.QUEUE.qsize() == svc.MAX_QUEUE and svc.queued_count() == 0


def test_the_full_check_and_the_health_answer_use_that_count():
    text = SERVICE.read_text()
    assert "QUEUE.qsize() + len(HELD)" not in text
    assert "if queued_count() >= MAX_QUEUE:" in text and '"queued": queued_count(),' in text
