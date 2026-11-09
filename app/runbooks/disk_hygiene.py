from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.runbooks.base import ProbeResult, Runbook, StepResult, step
from app.runbooks.diagnose import probe as diagnose_probe
from app.ssh_exec import run_remote


def probe(server_id: str) -> ProbeResult:
    base = diagnose_probe(server_id)
    matched = bool(base.matches.get("disk_hygiene"))
    if not matched and "disk_pct" in base.facts:
        try:
            matched = int(base.facts["disk_pct"]) >= 85
        except ValueError:
            matched = False
    base.matches["disk_hygiene"] = matched
    return base


@step("upload_and_run_disk_hygiene")
def run_hygiene(server_id: str) -> StepResult:
    settings = get_settings()
    local = Path(settings.scripts_dir) / "disk-hygiene.sh"
    if not local.exists():
        return StepResult(
            name="upload_and_run_disk_hygiene",
            ok=False,
            output=f"missing local script: {local}",
        )
    script_body = local.read_text(encoding="utf-8")
    # Avoid embedding Telegram secrets from agent into fleet host
    remote = f"""
set -euo pipefail
cat >/tmp/agent-disk-hygiene.sh <<'EOF_AGENT_DISK'
{script_body}
EOF_AGENT_DISK
chmod 700 /tmp/agent-disk-hygiene.sh
export REPORT_MODE=never
export DRY_RUN=0
bash /tmp/agent-disk-hygiene.sh
rm -f /tmp/agent-disk-hygiene.sh
df -h / | tail -1
"""
    ok, out = run_remote(server_id, remote, timeout=300)
    return StepResult(name="upload_and_run_disk_hygiene", ok=ok, output=out)


RUNBOOK = Runbook(
    id="disk_hygiene",
    title="Очистка диска",
    description="Безопасная гигиена журналов/кэша/старых логов без трогания конфигов и БД панели.",
    when_description="Диск занят ≥85% или check=disk_full.",
    risk="low",
    mutating=True,
    match_checks=["disk_full", "disk", "disk_hygiene"],
    probe=probe,
    steps=[run_hygiene],
)
