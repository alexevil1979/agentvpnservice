from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from app import db
from app.config import get_settings
from app.portix import plan_runbook
from app.runbooks import catalog_for_planner, get_runbook
from app.runbooks.diagnose import probe as diagnose_probe
from app.telegram_bot import TelegramPoller, notify_needs_human, notify_proposal, notify_result

log = logging.getLogger("agentvpnservice.worker")

_queue: Optional[asyncio.Queue] = None
_poller: Optional[TelegramPoller] = None
_bg_tasks: list[asyncio.Task] = []


def queue() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


async def enqueue(incident_id: str) -> None:
    await queue().put(incident_id)


async def start_background() -> None:
    global _poller
    q = queue()
    _bg_tasks.append(asyncio.create_task(_consumer(), name="incident-consumer"))
    _bg_tasks.append(asyncio.create_task(_reminder_loop(), name="reminder-loop"))
    _poller = TelegramPoller(on_decision=handle_decision)
    _poller.start()


async def stop_background() -> None:
    global _poller
    if _poller:
        await _poller.stop()
    for t in _bg_tasks:
        t.cancel()


async def _consumer() -> None:
    while True:
        incident_id = await queue().get()
        try:
            await process_incident(incident_id)
        except Exception:
            log.exception("process_incident %s", incident_id)
            db.update_incident(
                incident_id,
                status="needs_human",
                proposal_text="Внутренняя ошибка обработки. Смотри journald.",
            )
        finally:
            queue().task_done()


async def process_incident(incident_id: str) -> None:
    settings = get_settings()
    inc = db.get_incident(incident_id)
    if not inc:
        return
    if inc["status"] not in ("received",):
        return

    db.update_incident(incident_id, status="probing")
    # diagnose always
    probe = await asyncio.to_thread(diagnose_probe, inc["server_id"])
    db.add_step(incident_id, "diagnose", probe.ok, probe.raw)
    db.update_incident(incident_id, probe_json=db.dumps({"facts": probe.facts, "matches": probe.matches, "ok": probe.ok}))

    if not probe.ok and "not in inventory" in (probe.raw or ""):
        db.update_incident(
            incident_id,
            status="needs_human",
            proposal_text="Сервер не найден в inventory или SSH недоступен.",
            result_summary=probe.raw[:500],
        )
        await notify_needs_human(db.get_incident(incident_id) or inc)
        return

    catalog = catalog_for_planner(settings.enabled_runbook_ids)
    # force match for check-driven high-risk maps runbook
    matches = dict(probe.matches)
    for c in catalog:
        rb = get_runbook(c["id"])
        if rb and inc["check_code"] in rb.match_checks:
            matches[c["id"]] = True

    plan = await plan_runbook(
        server_id=inc["server_id"],
        check_code=inc["check_code"],
        summary=inc["summary"],
        evidence=inc["evidence"],
        probe_facts=probe.facts,
        probe_matches=matches,
        catalog=catalog,
    )

    if plan.get("needs_human") or not plan.get("runbook_id"):
        db.update_incident(
            incident_id,
            status="needs_human",
            proposal_text=plan.get("explanation") or "Нужен человек",
            proposed_runbook=None,
        )
        await notify_needs_human(db.get_incident(incident_id) or inc)
        return

    rb = get_runbook(plan["runbook_id"])
    if not rb:
        db.update_incident(incident_id, status="needs_human", proposal_text="Неизвестный runbook")
        await notify_needs_human(db.get_incident(incident_id) or inc)
        return

    # For high risk require check match
    if rb.risk == "high" and inc["check_code"] not in rb.match_checks:
        db.update_incident(
            incident_id,
            status="needs_human",
            proposal_text=f"Сценарий {rb.id} высокий риск и check не из allowlist — нужен человек.",
        )
        await notify_needs_human(db.get_incident(incident_id) or inc)
        return

    nonce = secrets.token_urlsafe(12)
    updated = db.update_incident(
        incident_id,
        status="awaiting_approval",
        proposed_runbook=rb.id,
        proposal_text=plan.get("explanation") or rb.description,
        risk=rb.risk,
        nonce=nonce,
        decision=None,
        decision_at=None,
        decision_by=None,
    )
    tg = await notify_proposal(updated or inc, rb.title, rb.steps_preview())
    if tg.get("ok"):
        try:
            mid = str(tg["body"]["result"]["message_id"])
            db.update_incident(incident_id, telegram_message_id=mid)
        except Exception:
            pass


async def handle_decision(incident_id: str, nonce: str, decision: str, who: str) -> None:
    inc = db.get_incident(incident_id)
    if not inc:
        return
    if inc["status"] != "awaiting_approval":
        return
    if not inc.get("nonce") or not secrets.compare_digest(inc["nonce"], nonce):
        return

    now = db.utcnow()
    if decision == "rejected":
        db.update_incident(
            incident_id,
            status="rejected",
            decision="rejected",
            decision_at=now,
            decision_by=who,
            nonce=None,
            result_summary="Отклонено админом",
        )
        await notify_result(db.get_incident(incident_id) or inc)
        return

    db.update_incident(
        incident_id,
        status="approved",
        decision="approved",
        decision_at=now,
        decision_by=who,
        nonce=None,
    )
    asyncio.create_task(execute_incident(incident_id))


async def execute_incident(incident_id: str) -> None:
    settings = get_settings()
    inc = db.get_incident(incident_id)
    if not inc or not inc.get("proposed_runbook"):
        return
    rb = get_runbook(inc["proposed_runbook"])
    if not rb:
        db.update_incident(incident_id, status="failed", result_summary="runbook missing")
        return

    if settings.dry_run:
        db.update_incident(
            incident_id,
            status="done_dry_run",
            result_summary=f"Dry-run: сценарий {rb.id} НЕ выполнен. Шаги были бы: {', '.join(rb.steps_preview())}",
        )
        db.add_step(incident_id, "dry_run", True, "skipped mutating steps because DRY_RUN=1")
        await notify_result(db.get_incident(incident_id) or inc)
        return

    db.update_incident(incident_id, status="running")
    all_ok = True
    for fn in rb.steps:
        name = getattr(fn, "step_name", fn.__name__)
        try:
            result = await asyncio.to_thread(fn, inc["server_id"])
            db.add_step(incident_id, result.name, result.ok, result.output)
            if not result.ok:
                all_ok = False
                break
        except Exception as e:
            db.add_step(incident_id, name, False, str(e))
            all_ok = False
            break

    db.update_incident(
        incident_id,
        status="done" if all_ok else "failed",
        result_summary=("OK " if all_ok else "FAIL ") + rb.id,
    )
    await notify_result(db.get_incident(incident_id) or inc)


async def _reminder_loop() -> None:
    while True:
        await asyncio.sleep(60)
        settings = get_settings()
        try:
            items = db.list_incidents(status="awaiting_approval", limit=50)
            now = datetime.now(timezone.utc)
            for inc in items:
                created = inc.get("updated_at") or inc.get("created_at")
                if not created:
                    continue
                try:
                    ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except Exception:
                    continue
                age_min = (now - ts).total_seconds() / 60.0
                if age_min < settings.reminder_minutes:
                    continue
                if inc.get("reminded_at"):
                    continue
                if age_min > settings.approval_timeout_minutes:
                    db.update_incident(
                        inc["id"],
                        status="expired",
                        result_summary="Таймаут ожидания подтверждения",
                        nonce=None,
                    )
                    await notify_result(db.get_incident(inc["id"]) or inc)
                    continue
                rb = get_runbook(inc.get("proposed_runbook") or "")
                await notify_proposal(
                    inc,
                    rb.title if rb else (inc.get("proposed_runbook") or "?"),
                    rb.steps_preview() if rb else [],
                )
                db.update_incident(inc["id"], reminded_at=db.utcnow())
        except Exception:
            log.exception("reminder")
