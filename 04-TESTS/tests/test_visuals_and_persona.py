from ai_video_factory.visuals_fetcher import prune_visuals
from ai_video_factory.persona import extract_personas


def test_prune_motion_by_purpose():
    visuals = [
        {"purpose": "clip", "match_score": 0.9, "motion_score": 0.01},
        {"purpose": "thumbnail", "match_score": 0.9, "motion_score": 0.02},
        {"purpose": "image", "match_score": 0.9},
    ]
    kept, removed = prune_visuals(visuals, min_match_score=0.2, min_motion=0.07, motion_thresholds={"thumbnail": 0.01})
    assert any(v.get('purpose') == 'thumbnail' for v in kept), 'thumbnail should be kept by lower threshold'
    assert any(v.get('purpose') == 'clip' for v in removed), 'clip should be removed'
    assert any(v.get('purpose') == 'image' for v in removed), 'image without motion should be removed'


def test_persona_basic():
    txt = 'Alex betrayed Sam. Alex is sneaky. Sam is trusting.'
    ps = extract_personas(txt, use_spacy=False)
    assert isinstance(ps, list) and any('Alex' in p['name'] or 'Sam' in p['name'] for p in ps), 'persona extraction should identify names'
