"""Small role-based authorization contract shared by dashboard and workers."""
from __future__ import annotations
from enum import Enum
from typing import Iterable

class Role(str, Enum):
    VIEWER="viewer"
    EDITOR="editor"
    ADMIN="admin"

_RANK={Role.VIEWER:10,Role.EDITOR:20,Role.ADMIN:30}

def normalize_role(value:str|Role|None)->Role:
    try: return Role(str(value or "viewer").strip().lower())
    except ValueError as exc: raise ValueError(f"unsupported role: {value!r}") from exc

def role_allows(role:str|Role, required:str|Role)->bool:
    return _RANK[normalize_role(role)] >= _RANK[normalize_role(required)]

def require_role(role:str|Role, required:str|Role)->None:
    if not role_allows(role,required):
        raise PermissionError(f"role {normalize_role(role).value!r} does not satisfy required role {normalize_role(required).value!r}")

def allowed_roles(required:str|Role)->Iterable[str]:
    minimum=_RANK[normalize_role(required)]
    return [r.value for r,rank in _RANK.items() if rank>=minimum]

__all__=["Role","allowed_roles","normalize_role","require_role","role_allows"]
