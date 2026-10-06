from __future__ import annotations

import multiprocessing
import time

from ai_video_factory.job_recovery import reconcile_job, process_is_alive


def _sleep_forever() -> None:
    time.sleep(120)


def test_worker_process_death_is_reconciled_to_interrupted():
    process = multiprocessing.get_context("spawn").Process(target=_sleep_forever)
    process.start()
    try:
        assert process_is_alive(process.pid)
        process.terminate()
        process.join(timeout=10)
        assert not process_is_alive(process.pid)

        seen: list[tuple[str, str]] = []
        recovered = reconcile_job(
            job_id="job-crash",
            pid=process.pid,
            heartbeat_epoch=time.time() - 7200,
            last_seen_epoch=time.time() - 7200,
            now=time.time(),
            timeout_seconds=60,
            mark_interrupted=lambda job_id, reason: seen.append((job_id, reason)),
        )

        assert recovered is True
        assert seen and seen[0][0] == "job-crash"
