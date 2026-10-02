"""Deterministic V3 decision-graph replay helpers."""
from __future__ import annotations
from dataclasses import asdict
from typing import Any, Mapping
from .idempotency import stable_hash
from .v3_engine import V3Blueprint, V3Config, create_v3_blueprint

def decision_graph(blueprint: V3Blueprint)->dict[str,Any]:
    return {
        "version": blueprint.version,
        "core_idea": asdict(blueprint.core_idea),
        "edit_type": blueprint.edit_type,
        "hooks": [asdict(x) for x in blueprint.hooks],
        "clip_plan": [asdict(x) for x in blueprint.clip_plan],
        "music": asdict(blueprint.music),
        "retention_map": [asdict(x) for x in blueprint.retention_map],
        "editorial_decisions": [x.to_dict() for x in blueprint.editorial_decisions],
    }

def decision_graph_hash(blueprint: V3Blueprint)->str:
    return stable_hash(decision_graph(blueprint))

def replay_blueprint(topic: str, *, context: str="", config: V3Config|None=None, edit_type: str|None=None)->V3Blueprint:
    return create_v3_blueprint(topic, context=context, config=config, edit_type=edit_type)

def assert_deterministic_replay(topic: str, *, context: str="", config: V3Config|None=None, edit_type: str|None=None)->str:
    first=replay_blueprint(topic,context=context,config=config,edit_type=edit_type)
    second=replay_blueprint(topic,context=context,config=config,edit_type=edit_type)
    first_hash=decision_graph_hash(first)
    second_hash=decision_graph_hash(second)
    if first_hash != second_hash:
        raise AssertionError("V3 decision graph is not deterministic")
    return first_hash

__all__=["assert_deterministic_replay","decision_graph","decision_graph_hash","replay_blueprint"]
