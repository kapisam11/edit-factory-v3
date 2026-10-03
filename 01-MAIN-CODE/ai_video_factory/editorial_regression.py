"""Creative regression gates for V3 editorial behavior."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping, Any

DIMENSIONS=("hook","pacing","coherence","caption_quality","payoff","overall")

@dataclass(frozen=True)
class RegressionResult:
    passed: bool
    dimensions: Mapping[str, float]
    regressions: tuple[str,...]
    def to_dict(self)->dict[str,Any]:
        return {"passed":self.passed,"dimensions":dict(self.dimensions),"regressions":list(self.regressions)}

def compare_editorial_metrics(previous: Mapping[str,float], current: Mapping[str,float], *, tolerance: float=0.05)->RegressionResult:
    regressions=[]
    deltas={}
    for name in DIMENSIONS:
        if name not in previous or name not in current:
            continue
        delta=float(current[name])-float(previous[name])
        deltas[name]=round(delta,4)
        if delta < -float(tolerance):
            regressions.append(name)
    return RegressionResult(not regressions,deltas,tuple(regressions))

__all__=["RegressionResult","compare_editorial_metrics"]
