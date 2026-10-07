# Production load tests

Admission and SQLite concurrency:

    PYTHONPATH=01-MAIN-CODE:02-WEB-FILES:03-SIDE-CODE python 03-SIDE-CODE/tools/load_test_admission.py --requests 50

Strict idempotency:

    PYTHONPATH=01-MAIN-CODE:02-WEB-FILES:03-SIDE-CODE python 03-SIDE-CODE/tools/load_test_admission.py --requests 100 --identical

These tests isolate admission from FFmpeg so CI cannot accidentally saturate the encoder. Target-host media throughput remains an explicit deployment acceptance gate.


Real target-host render throughput (run only against a controlled deployment):

    AIVF_DASHBOARD_TOKEN=... PYTHONPATH=01-MAIN-CODE:02-WEB-FILES:03-SIDE-CODE \
      python 03-SIDE-CODE/tools/load_test_real_render.py \
      --base-url http://127.0.0.1:5000 --fixture /tmp/aivf-recovery-smoke.mp4

Default levels are 1, 2, 5, 10, and 20 concurrent submissions. This measures
submission latency and admission behavior; render duration/failure-rate results
are collected from the target host's job history and Prometheus metrics.
