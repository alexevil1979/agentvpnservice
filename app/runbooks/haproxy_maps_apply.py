from __future__ import annotations

from app.runbooks.base import ProbeResult, Runbook, StepResult, step
from app.runbooks.diagnose import probe as diagnose_probe
from app.ssh_exec import run_remote

APPLY = r"""
set -euo pipefail
ts=$(date +%Y%m%d%H%M%S)
mkdir -p /var/backups/hiddify
if [[ -d /opt/hiddify-manager/hiddify-panel ]]; then
  tar -czf "/var/backups/hiddify/pre-maps-apply-${ts}.tgz" \
    -C /opt/hiddify-manager/hiddify-panel backup 2>/dev/null || true
fi
if [[ -x /opt/hiddify-manager/common/commander.py ]]; then
  /opt/hiddify-manager/common/commander.py apply-configs || true
fi
if [[ -x /opt/hiddify-manager/apply_configs.sh ]]; then
  bash /opt/hiddify-manager/apply_configs.sh
elif [[ -x /opt/hiddify-manager/common/apply_configs.sh ]]; then
  bash /opt/hiddify-manager/common/apply_configs.sh
else
  echo "apply_configs.sh not found"
  exit 2
fi
echo ===MAPS_AFTER===
ls -la /opt/hiddify-manager/haproxy/maps | head -30
systemctl is-active hiddify-haproxy || systemctl restart hiddify-haproxy || true
systemctl is-active hiddify-haproxy
"""


def probe(server_id: str) -> ProbeResult:
    """Maps mismatch is hard to assert without DB credentials; rely on check code + light signal."""
    base = diagnose_probe(server_id)
    matched = False
    # signal: check will drive match; optional decoy/admin failures noted in evidence by hidsal
    if "decoy" in base.raw.lower() or "path" in base.raw.lower():
        # weak signal only — require matching check_code for approval path
        pass
    base.matches["haproxy_maps_apply"] = matched
    return base


@step("haproxy_maps_apply")
def run_apply(server_id: str) -> StepResult:
    ok, out = run_remote(server_id, APPLY, timeout=600)
    return StepResult(name="haproxy_maps_apply", ok=ok, output=out)


RUNBOOK = Runbook(
    id="haproxy_maps_apply",
    title="HAProxy maps apply_configs",
    description="Бэкап панели (best-effort) + apply_configs.sh. Высокий риск на живом проде.",
    when_description="check=haproxy_maps / admin_decoy / path_mismatch после restore path.",
    risk="high",
    mutating=True,
    match_checks=["haproxy_maps", "admin_decoy", "path_mismatch", "maps_mismatch"],
    probe=probe,
    steps=[run_apply],
)
