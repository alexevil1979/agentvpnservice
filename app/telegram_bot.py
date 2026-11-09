from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.http_proxy import request_json
from app import db

log = logging.getLogger("agentvpnservice.telegram")


def _api(method: str) -> str:
    token = get_settings().telegram_bot_token
    return f"https://api.telegram.org/bot{token}/{method}"


async def send_message(
    text: str,
    *,
    reply_markup: Optional[dict] = None,
    chat_id: Optional[str] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    if not settings.telegram_bot_token or not (chat_id or settings.telegram_chat_id):
        return {"ok": False, "error": "telegram not configured"}
    payload: Dict[str, Any] = {
        "chat_id": str(chat_id or settings.telegram_chat_id),
        "text": text[:3900],
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        status, body, proxy = await request_json("POST", _api("sendMessage"), json_body=payload)
        return {"ok": status == 200 and bool(body.get("ok")), "body": body, "proxy": proxy}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def edit_message(chat_id: str, message_id: int, text: str) -> Dict[str, Any]:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text[:3900],
        "disable_web_page_preview": True,
    }
    try:
        status, body, proxy = await request_json("POST", _api("editMessageText"), json_body=payload)
        return {"ok": status == 200 and bool(body.get("ok")), "body": body, "proxy": proxy}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def answer_callback(callback_id: str, text: str = "") -> None:
    try:
        await request_json(
            "POST",
            _api("answerCallbackQuery"),
            json_body={"callback_query_id": callback_id, "text": text[:180]},
        )
    except Exception:
        pass


def approval_keyboard(incident_id: str, nonce: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "Фиксить", "callback_data": f"fix:{incident_id}:{nonce}"},
                {"text": "Отклонить", "callback_data": f"rej:{incident_id}:{nonce}"},
            ]
        ]
    }


async def notify_proposal(incident: Dict[str, Any], runbook_title: str, steps: List[str]) -> Dict[str, Any]:
    settings = get_settings()
    url = f"{settings.public_base_url.rstrip('/')}/incidents/{incident['id']}"
    dry = "ДА (dry-run)" if settings.dry_run else "нет"
    lines = [
        f"Инцидент {incident['server_id']} / {incident['check_code']}",
        f"Статус: ждёт решения",
        f"Сценарий: {runbook_title} ({incident.get('proposed_runbook')})",
        f"Риск: {incident.get('risk') or '-'}",
        f"Dry-run: {dry}",
        "",
        incident.get("proposal_text") or "",
        "",
        "Шаги:",
        *[f"- {s}" for s in steps],
        "",
        f"Карточка: {url}",
    ]
    return await send_message(
        "\n".join(lines),
        reply_markup=approval_keyboard(incident["id"], incident["nonce"] or ""),
    )


async def notify_needs_human(incident: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_settings()
    url = f"{settings.public_base_url.rstrip('/')}/incidents/{incident['id']}"
    text = (
        f"Инцидент {incident['server_id']} / {incident['check_code']}\n"
        f"Нужен человек — автопочинка не предложена.\n\n"
        f"{incident.get('proposal_text') or incident.get('summary') or ''}\n\n"
        f"Карточка: {url}"
    )
    return await send_message(text)


async def notify_result(incident: Dict[str, Any]) -> Dict[str, Any]:
    text = (
        f"Инцидент {incident['server_id']} / {incident['check_code']}\n"
        f"Итог: {incident.get('status')}\n"
        f"{incident.get('result_summary') or ''}"
    )
    return await send_message(text)


class TelegramPoller:
    def __init__(self, on_decision):
        self.on_decision = on_decision
        self._offset = 0
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="telegram-poller")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await asyncio.wait([self._task], timeout=5)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            settings = get_settings()
            if not settings.telegram_bot_token:
                await asyncio.sleep(5)
                continue
            try:
                status, body, _ = await request_json(
                    "GET",
                    _api("getUpdates"),
                    # getUpdates via query is awkward with json; use POST
                )
            except Exception:
                # use POST long poll
                pass
            try:
                status, body, _ = await request_json(
                    "POST",
                    _api("getUpdates"),
                    json_body={"offset": self._offset, "timeout": 25, "allowed_updates": ["callback_query"]},
                    timeout=35,
                )
                if status != 200 or not body.get("ok"):
                    await asyncio.sleep(3)
                    continue
                for upd in body.get("result") or []:
                    self._offset = max(self._offset, int(upd.get("update_id", 0)) + 1)
                    cb = upd.get("callback_query")
                    if not cb:
                        continue
                    await self._handle_callback(cb)
            except Exception as e:
                log.warning("telegram poll: %s", e)
                await asyncio.sleep(5)

    async def _handle_callback(self, cb: dict) -> None:
        data = (cb.get("data") or "").strip()
        await answer_callback(cb.get("id") or "", "Принято")
        parts = data.split(":")
        if len(parts) != 3 or parts[0] not in ("fix", "rej"):
            return
        action, incident_id, nonce = parts
        decision = "approved" if action == "fix" else "rejected"
        user = cb.get("from") or {}
        who = user.get("username") or str(user.get("id") or "telegram")
        await self.on_decision(incident_id, nonce, decision, who)
