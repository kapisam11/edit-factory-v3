# Edit Factory load tests

These tests are intentionally opt-in and are not part of the normal unit/integration
CI gate. They exercise the single-host SQLite admission layer at increasing concurrency.

Run:

`uv run pytest -q 04-TESTS/load`

The suite measures submission/admission throughput for 1, 2, 5, 10, and 20 concurrent
writers and reports failures, elapsed time, and effective requests/second.

For end-to-end HTTP load, run the dashboard separately and use the same concurrency
levels against `POST /api/jobs`; the database tests here isolate the storage boundary.
