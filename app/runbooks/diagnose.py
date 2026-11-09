from __future__ import annotations

from app.runbooks.base import ProbeResult, Runbook, StepResult, step
from app.ssh_exec import run_remote

DIAG_SCRIPT = r"""
set +e
echo ===HOST===
hostname; date -Is; uptime
echo ===DISK===
df -h / | tail -1
echo ===UNITS===
for u in hiddify-panel hiddify-panel-background-tasks hiddify-haproxy hiddify-xray nginx redis-server mariadb; do
  systemctl is-active "$u" 2>/dev/null | awk -v u="$u" '{print u":"$0}'
done
echo ===CELERY_CMD===
systemctl cat hiddify-panel-background-tasks 2>/dev/null | grep -E 'ExecStart|loglevel|Nice|CPUQuota' | head -20
echo ===PORTS===
ss -lnt | awk 'NR==1 || /:(80|443|9000|9001)\s/'
echo ===PATH_DB===
python3 - <<'PY'
import os
try:
  from sqlalchemy import create_engine, text
  # common panel sqlite/mysql paths vary; try env from panel
except Exception as e:
  print('sqlalchemy_skip', e)
print('proxy_path_check')
os.system("grep -R --include='*.map' -n 'proxy' /opt/hiddify-manager/haproxy/maps 2>/dev/null | head -20")
PY
echo ===MAPS===
ls -la /opt/hiddify-manager/haproxy/maps 2>/dev/null | head -30
echo ===CELERY_LOG_TAIL===
ls -1t /opt/hiddify-manager/log/system/hiddify_panel_background_tasks*.log 2>/dev/null | head -1 | xargs -r tail -n 40
echo ===DONE===
"""


def probe(server_id: str) -> ProbeResult:
    ok, out = run_remote(server_id, DIAG_SCRIPT, timeout=90)
    facts = {}
    matches = {}
    for line in out.splitlines():
        if line.startswith("disk ") or "% " in line and line.strip()[0].isdigit():
            pass
    # disk percent
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[-1] == "/" and parts[-2].endswith("%"):
            try:
                pct = int(parts[-2].rstrip("%"))
                facts["disk_pct"] = str(pct)
                if pct >= 85:
                    matches["disk_hygiene"] = True
            except ValueError:
                pass
        if "df -h" in out:
            pass
    # parse df -h line like: /dev/sda1  40G  35G  3G  92% /
    for line in out.splitlines():
        if line.strip().endswith(" /") and "%" in line:
            tok = [t for t in line.split() if t.endswith("%")]
            if tok:
                try:
                    pct = int(tok[0].rstrip("%"))
                    facts["disk_pct"] = str(pct)
                    if pct >= 85:
                        matches["disk_hygiene"] = True
                except ValueError:
                    pass
    if "--loglevel debug" in out:
        facts["celery_loglevel"] = "debug"
        matches["celery_cpu_harden"] = True
    if "PendingRollbackError" in out:
        facts["celery_error"] = "PendingRollbackError"
        matches["celery_worker_restart"] = True
    if "maps" in out.lower():
        facts["maps_listed"] = "1"
    return ProbeResult(ok=ok, facts=facts, raw=out, matches=matches)


@step("diagnose_collect")
def diagnose_collect(server_id: str) -> StepResult:
    pr = probe(server_id)
    return StepResult(name="diagnose_collect", ok=pr.ok, output=pr.raw)


RUNBOOK = Runbook(
    id="diagnose",
    title="Диагностика (только чтение)",
    description="Снимает диск, юниты, celery, порты, maps. Ничего не меняет.",
    when_description="Всегда перед выбором починки.",
    risk="low",
    mutating=False,
    match_checks=["*"],
    probe=probe,
    steps=[diagnose_collect],
)
