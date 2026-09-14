modules = [
    ('ai_video_factory.knowledge_v2', 'RealKnowledgeBase'),
    ('ai_video_factory.config', 'AIVFConfig'),
    ('ai_video_factory.pipeline', 'build_director_pipeline'),
    ('ai_video_factory.capability_registry', 'build_default_registry'),
    ('ai_video_factory.subtitle_renderer', 'burn_subtitles'),
    ('ai_video_factory.nle_export_v2', 'export_all_nle_formats'),
    ('ai_video_factory.quality_control_v2', 'run_enhanced_qc'),
]

import importlib
for mod, symbol in modules:
    try:
        m = importlib.import_module(mod)
        ok = hasattr(m, symbol)
        print('OK:' if ok else 'MISSING:', mod, symbol)
    except Exception as e:
        print('ERR:', mod, repr(e))
