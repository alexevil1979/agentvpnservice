from __future__ import annotations

from typing import Dict, List, Optional

from app.runbooks.base import Runbook, ProbeResult
from app.runbooks import (
    diagnose,
    disk_hygiene,
    celery_cpu_harden,
    celery_worker_restart,
    haproxy_maps_apply,
)

_REGISTRY: Dict[str, Runbook] = {
    diagnose.RUNBOOK.id: diagnose.RUNBOOK,
    disk_hygiene.RUNBOOK.id: disk_hygiene.RUNBOOK,
    celery_cpu_harden.RUNBOOK.id: celery_cpu_harden.RUNBOOK,
    celery_worker_restart.RUNBOOK.id: celery_worker_restart.RUNBOOK,
    haproxy_maps_apply.RUNBOOK.id: haproxy_maps_apply.RUNBOOK,
}


def all_runbooks() -> List[Runbook]:
    return list(_REGISTRY.values())


def get_runbook(runbook_id: str) -> Optional[Runbook]:
    return _REGISTRY.get(runbook_id)


def catalog_for_planner(enabled_ids: List[str]) -> List[dict]:
    out = []
    for rb in all_runbooks():
        if rb.id == "diagnose":
            continue
        if enabled_ids and rb.id not in enabled_ids:
            continue
        out.append(
            {
                "id": rb.id,
                "title": rb.title,
                "risk": rb.risk,
                "description": rb.description,
                "when": rb.when_description,
                "steps_preview": rb.steps_preview(),
            }
        )
    return out


__all__ = [
    "Runbook",
    "ProbeResult",
    "all_runbooks",
    "get_runbook",
    "catalog_for_planner",
]
