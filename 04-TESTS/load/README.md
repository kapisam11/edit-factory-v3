# Production load tests

Admission and SQLite concurrency:

    PYTHONPATH=01-MAIN-CODE:02-WEB-FILES:03-SIDE-CODE python 03-SIDE-CODE/tools/load_test_admission.py --requests 50

Strict idempotency:

    PYTHONPATH=01-MAIN-CODE:02-WEB-FILES:03-SIDE-CODE python 03-SIDE-CODE/tools/load_test_admission.py --requests 100 --identical

These tests isolate admission from FFmpeg so CI cannot accidentally saturate the encoder. Target-host media throughput remains an explicit deployment acceptance gate.
