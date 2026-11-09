from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.http_proxy import request_json

log = logging.getLogger("agentvpnservice.portix")

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


async def list_models() -> Dict[str, Any]:
    settings = get_settings()
    if not settings.portix_api_key:
        return {"ok": False, "error": "PORTIX_API_KEY empty"}
    url = settings.portix_base_url.rstrip("/") + "/models"
    status, body, proxy = await request_json(
        "GET",
        url,
        headers={"Authorization": f"Bearer {settings.portix_api_key}"},
        timeout=45,
    )
    return {"ok": status < 400, "status": status, "body": body, "proxy": proxy}


async def plan_runbook(
    *,
    server_id: str,
    check_code: str,
    summary: str,
    evidence: str,
    probe_facts: Dict[str, str],
    probe_matches: Dict[str, bool],
    catalog: List[dict],
) -> Dict[str, Any]:
    """Return {runbook_id|None, explanation, needs_human, confidence}."""
    settings = get_settings()
    allowed = {c["id"] for c in catalog}
    if not settings.portix_api_key:
        # heuristic fallback without model
        for cid, matched in probe_matches.items():
            if matched and cid in allowed:
                return {
                    "runbook_id": cid,
                    "explanation": "Модель недоступна; выбран сценарий по совпадению проб.",
                    "needs_human": False,
                    "confidence": 0.4,
                    "source": "heuristic",
                }
        return {
            "runbook_id": None,
            "explanation": "Модель недоступна и автоматического совпадения нет — нужен человек.",
            "needs_human": True,
            "confidence": 0.0,
            "source": "heuristic",
        }

    system = (
        "Ты планировщик починки VPN-парка Hiddify. "
        "Выбери ровно один runbook_id из списка allowed или верни needs_human=true. "
        "Нельзя предлагать команды, DNS, Cloudflare, reinstall, wipe. "
        "Ответь ТОЛЬКО JSON: "
        '{"runbook_id":"id или null","explanation":"по-русски","needs_human":true/false,"confidence":0..1}'
    )
    user = {
        "server_id": server_id,
        "check": check_code,
        "summary": summary,
        "evidence": evidence[:2000],
        "probe_facts": probe_facts,
        "probe_matches": probe_matches,
        "allowed_runbooks": catalog,
    }
    url = settings.portix_base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.portix_model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
    }
    try:
        status, body, proxy = await request_json(
            "POST",
            url,
            headers={
                "Authorization": f"Bearer {settings.portix_api_key}",
                "Content-Type": "application/json",
            },
            json_body=payload,
            timeout=90,
        )
    except Exception as e:
        log.exception("portix failed")
        return {
            "runbook_id": None,
            "explanation": f"Portix ошибка: {e}",
            "needs_human": True,
            "confidence": 0.0,
            "source": "error",
        }

    content = ""
    try:
        content = body["choices"][0]["message"]["content"]
    except Exception:
        content = json.dumps(body, ensure_ascii=False)[:2000]

    parsed = _parse_plan(content)
    rid = parsed.get("runbook_id")
    if rid and rid not in allowed:
        return {
            "runbook_id": None,
            "explanation": f"Модель предложила неизвестный id={rid}. Нужен человек.",
            "needs_human": True,
            "confidence": 0.0,
            "source": "portix",
            "raw": content[:1500],
            "proxy": proxy,
            "http_status": status,
        }
    if parsed.get("needs_human") or not rid:
        return {
            "runbook_id": None,
            "explanation": parsed.get("explanation") or "Нужен человек.",
            "needs_human": True,
            "confidence": float(parsed.get("confidence") or 0),
            "source": "portix",
            "proxy": proxy,
            "http_status": status,
        }
    # require probe match or matching check for high risk
    conf = float(parsed.get("confidence") or 0)
    if conf < 0.35:
        return {
            "runbook_id": None,
            "explanation": parsed.get("explanation") or "Низкая уверенность.",
            "needs_human": True,
            "confidence": conf,
            "source": "portix",
            "proxy": proxy,
        }
    return {
        "runbook_id": rid,
        "explanation": parsed.get("explanation") or "",
        "needs_human": False,
        "confidence": conf,
        "source": "portix",
        "proxy": proxy,
        "http_status": status,
    }


def _parse_plan(content: str) -> Dict[str, Any]:
    content = (content or "").strip()
    try:
        return json.loads(content)
    except Exception:
        pass
    m = _JSON_BLOCK.search(content)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {"runbook_id": None, "explanation": content[:500], "needs_human": True, "confidence": 0}
