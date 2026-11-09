from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from app.ssh_exec import run_remote


@dataclass
class ProbeResult:
    ok: bool
    facts: Dict[str, str] = field(default_factory=dict)
    raw: str = ""
    matches: Dict[str, bool] = field(default_factory=dict)


@dataclass
class StepResult:
    name: str
    ok: bool
    output: str


StepFn = Callable[[str], StepResult]


@dataclass
class Runbook:
    id: str
    title: str
    description: str
    when_description: str
    risk: str  # low | medium | high
    mutating: bool
    match_checks: List[str]
    probe: Callable[[str], ProbeResult]
    steps: List[StepFn]

    def steps_preview(self) -> List[str]:
        return [getattr(fn, "step_name", fn.__name__) for fn in self.steps]

    def matches_incident(self, check_code: str, probe: ProbeResult) -> bool:
        if self.match_checks and check_code not in self.match_checks and "*" not in self.match_checks:
            # still allow if probe matches strongly
            if not any(probe.matches.values()):
                return False
        if self.id == "diagnose":
            return True
        return bool(probe.matches.get(self.id) or any(probe.matches.values()))


def step(name: str):
    def deco(fn: StepFn) -> StepFn:
        setattr(fn, "step_name", name)
        return fn

    return deco


def remote_step(name: str, script: str, timeout: int = 120) -> StepFn:
    @step(name)
    def _run(server_id: str) -> StepResult:
        ok, out = run_remote(server_id, script, timeout=timeout)
        return StepResult(name=name, ok=ok, output=out)

    return _run
