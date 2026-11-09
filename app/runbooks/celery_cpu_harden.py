from __future__ import annotations

from app.runbooks.base import ProbeResult, Runbook, remote_step
from app.runbooks.diagnose import probe as diagnose_probe

HARDEN = r"""
set -euo pipefail
src="/opt/hiddify-manager/hiddify-panel/hiddify-panel-background-tasks.service"
live="/etc/systemd/system/hiddify-panel-background-tasks.service"
drop_dir="/etc/systemd/system/hiddify-panel-background-tasks.service.d"
drop="$drop_dir/loglevel.conf"
py="/opt/hiddify-manager/.venv313/bin/python"
[[ -x "$py" ]] || py="/opt/hiddify-manager/.venv/bin/python"
[[ -x "$py" ]] || { echo "no venv python"; exit 2; }
if [[ -f "$src" ]]; then sed -i 's/--loglevel debug/--loglevel warning/g' "$src" || true; fi
if [[ -f "$live" ]]; then sed -i 's/--loglevel debug/--loglevel warning/g' "$live" || true; fi
install -d "$drop_dir"
cat >"$drop" <<EOF
[Service]
Nice=10
CPUQuota=35%
ExecStart=
ExecStart=${py} -m celery -A hiddifypanel.apps.celery_app:celery_app worker --beat --loglevel warning --concurrency 1 --pool=solo
EOF
systemctl daemon-reload
systemctl restart hiddify-panel-background-tasks
systemctl is-active hiddify-panel-background-tasks
systemctl cat hiddify-panel-background-tasks | grep -E 'ExecStart|Nice|CPUQuota|loglevel' | head -20
"""


def probe(server_id: str) -> ProbeResult:
    base = diagnose_probe(server_id)
    matched = bool(base.matches.get("celery_cpu_harden")) or (
        base.facts.get("celery_loglevel") == "debug"
    )
    base.matches["celery_cpu_harden"] = matched
    return base


run_harden = remote_step("celery_cpu_harden", HARDEN, timeout=120)


RUNBOOK = Runbook(
    id="celery_cpu_harden",
    title="Celery CPU harden",
    description="loglevel=warning, Nice=10, CPUQuota=35% через drop-in.",
    when_description="Юнит background-tasks с --loglevel debug или check=celery_cpu.",
    risk="medium",
    mutating=True,
    match_checks=["celery_cpu", "celery_debug", "cpu_abuse"],
    probe=probe,
    steps=[run_harden],
)
