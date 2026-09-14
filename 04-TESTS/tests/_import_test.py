import importlib, traceback

try:
    m = importlib.import_module('ai_video_factory.visuals_fetcher')
    print('OK import visuals_fetcher')
except Exception:
    traceback.print_exc()
