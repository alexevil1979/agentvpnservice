from __future__ import annotations

from app.runbooks.base import ProbeResult, Runbook, remote_step
from app.runbooks.diagnose import probe as diagnose_probe

RESTART = r"""
set -euo pipefail
systemctl restart hiddify-panel-background-tasks
sleep 2
systemctl is-active hiddify-panel-background-tasks
systemctl --no-pager -l status hiddify-panel-background-tasks | head -25
"""


def probe(server_id: str) -> ProbeResult:
    base = diagnose_probe(server_id)
    matched = bool(base.matches.get("celery_worker_restart")) or (
        base.facts.get("celery_error") == "PendingRollbackError"
    )
    base.matches["celery_worker_restart"] = matched
    return base


run_restart = remote_step("celery_worker_restart", RESTART, timeout=60)


RUNBOOK = Runbook(
    id="celery_worker_restart",
    title="Restart celery worker",
    description="Только systemctl restart hiddify-panel-background-tasks.",
    when_description="PendingRollbackError / usage stuck / check=celery_stuck.",
    risk="medium",
    mutating=True,
    match_checks=["celery_stuck", "usage_stuck", "celery_restart"],
    probe=probe,
    steps=[run_restart],
)
