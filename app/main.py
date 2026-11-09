from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app import __version__, db
from app.auth_web import create_session_token, login_redirect, require_admin, verify_login
from app.config import get_settings, reload_settings
from app.portix import list_models
from app.runbooks import all_runbooks, get_runbook
from app.telegram_bot import send_message
from app.worker import enqueue, execute_incident, handle_decision, start_background, stop_background

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("agentvpnservice")

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="agentvpnservice", version=__version__)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


class IncidentIn(BaseModel):
    server_id: str = Field(..., min_length=1, max_length=120)
    check: str = Field(..., min_length=1, max_length=80)
    summary: str = ""
    evidence: str = ""
    observed_at: Optional[str] = None
    host: Optional[str] = None  # ignored; inventory is source of truth


def _check_webhook(authorization: Optional[str]) -> None:
    settings = get_settings()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="bearer required")
    token = authorization.split(" ", 1)[1].strip()
    import secrets

    if not secrets.compare_digest(token, settings.incident_webhook_token):
        raise HTTPException(status_code=403, detail="bad token")


@app.on_event("startup")
async def _startup() -> None:
    db.init_db()
    Path(get_settings().data_dir).mkdir(parents=True, exist_ok=True)
    await start_background()
    log.info("agentvpnservice %s started", __version__)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await stop_background()


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "version": __version__, "dry_run": get_settings().dry_run}


@app.post("/api/v1/incidents")
async def create_incident_api(
    body: IncidentIn,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _check_webhook(authorization)
    result = db.create_incident(
        server_id=body.server_id.strip(),
        check_code=body.check.strip(),
        summary=body.summary or "",
        evidence=body.evidence or "",
        observed_at=body.observed_at,
    )
    inc = result["incident"]
    if result["created"]:
        await enqueue(inc["id"])
        return JSONResponse({"incident_id": inc["id"], "status": inc["status"]}, status_code=202)
    return JSONResponse(
        {"incident_id": inc["id"], "status": inc["status"], "deduped": True},
        status_code=200,
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if not verify_login(username, password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Неверный логин или пароль"},
            status_code=401,
        )
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        "avs_session",
        create_session_token(username),
        httponly=True,
        samesite="lax",
        max_age=86400 * 7,
    )
    return resp


@app.post("/logout")
async def logout() -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("avs_session")
    return resp


@app.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    status: Optional[str] = None,
    server_id: Optional[str] = None,
    user: str = Depends(require_admin),
):
    items = db.list_incidents(status=status or None, server_id=server_id or None, limit=150)
    return templates.TemplateResponse(
        "incidents.html",
        {
            "request": request,
            "user": user,
            "items": items,
            "status": status or "",
            "server_id": server_id or "",
            "settings": get_settings(),
        },
    )


@app.get("/incidents/{incident_id}", response_class=HTMLResponse)
async def incident_page(request: Request, incident_id: str, user: str = Depends(require_admin)):
    inc = db.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, "not found")
    steps = db.list_steps(incident_id)
    probe = {}
    try:
        probe = json.loads(inc.get("probe_json") or "{}")
    except Exception:
        pass
    rb = get_runbook(inc.get("proposed_runbook") or "")
    return templates.TemplateResponse(
        "incident.html",
        {
            "request": request,
            "user": user,
            "inc": inc,
            "steps": steps,
            "probe": probe,
            "runbook": rb,
            "settings": get_settings(),
        },
    )


@app.post("/incidents/{incident_id}/decide")
async def decide_web(
    incident_id: str,
    decision: str = Form(...),
    user: str = Depends(require_admin),
):
    inc = db.get_incident(incident_id)
    if not inc:
        raise HTTPException(404)
    if inc["status"] != "awaiting_approval" or not inc.get("nonce"):
        raise HTTPException(400, "not awaiting approval")
    await handle_decision(incident_id, inc["nonce"], decision, f"web:{user}")
    return RedirectResponse(f"/incidents/{incident_id}", status_code=303)


@app.get("/runbooks", response_class=HTMLResponse)
async def runbooks_page(request: Request, user: str = Depends(require_admin)):
    return templates.TemplateResponse(
        "runbooks.html",
        {"request": request, "user": user, "runbooks": all_runbooks(), "settings": get_settings()},
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user: str = Depends(require_admin)):
    settings = get_settings()
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "settings": settings,
            "socks_working": db.get_kv("socks_working_port", ""),
            "msg": None,
        },
    )


@app.post("/settings", response_class=HTMLResponse)
async def settings_save(
    request: Request,
    user: str = Depends(require_admin),
    portix_model: str = Form(""),
    dry_run: Optional[str] = Form(None),
    enabled_runbooks: str = Form(""),
    approval_timeout_minutes: int = Form(60),
    reminder_minutes: int = Form(15),
    socks_preferred: str = Form(""),
    portix_api_key_new: str = Form(""),
    incident_webhook_token_new: str = Form(""),
    telegram_bot_token_new: str = Form(""),
):
    """Persist overrides into .env-like kv + rewrite selected keys in process via db kv.

    Secrets only replaced when new value non-empty. Full .env rewrite is done carefully.
    """
    from app.config import ROOT

    env_file = ROOT / ".env"
    updates = {
        "PORTIX_MODEL": portix_model.strip() or get_settings().portix_model,
        "DRY_RUN": "1" if dry_run else "0",
        "ENABLED_RUNBOOKS": enabled_runbooks.strip() or get_settings().enabled_runbooks,
        "APPROVAL_TIMEOUT_MINUTES": str(approval_timeout_minutes),
        "REMINDER_MINUTES": str(reminder_minutes),
        "SOCKS_PREFERRED": socks_preferred.strip(),
    }
    if portix_api_key_new.strip():
        updates["PORTIX_API_KEY"] = portix_api_key_new.strip()
    if incident_webhook_token_new.strip():
        updates["INCIDENT_WEBHOOK_TOKEN"] = incident_webhook_token_new.strip()
    if telegram_bot_token_new.strip():
        updates["TELEGRAM_BOT_TOKEN"] = telegram_bot_token_new.strip()

    _merge_env_file(env_file, updates)
    reload_settings()
    db.set_kv("socks_working_port", socks_preferred.strip())
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "settings": get_settings(),
            "socks_working": db.get_kv("socks_working_port", ""),
            "msg": "Сохранено. Перезапуск unit подхватит .env полностью; модель/dry-run уже в процессе через reload.",
        },
    )


def _merge_env_file(path: Path, updates: dict[str, str]) -> None:
    lines: list[str] = []
    existing: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.strip().startswith("#") or "=" not in line:
                lines.append(line)
                continue
            k, v = line.split("=", 1)
            existing[k.strip()] = v
            lines.append(line)
    for k, v in updates.items():
        existing[k] = v
    # rewrite cleanly
    out = []
    seen = set()
    for line in lines:
        if not line.strip() or line.strip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        k = line.split("=", 1)[0].strip()
        if k in updates:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    path.chmod(0o600)


@app.post("/settings/test-telegram")
async def test_telegram(user: str = Depends(require_admin)):
    r = await send_message("Тест агента agentvpnservice: связь OK")
    return RedirectResponse("/settings?tg=" + ("ok" if r.get("ok") else "fail"), status_code=303)


@app.post("/settings/test-portix")
async def test_portix(user: str = Depends(require_admin)):
    r = await list_models()
    db.set_kv("last_portix_test", json.dumps({"ok": r.get("ok"), "status": r.get("status"), "proxy": r.get("proxy")}, ensure_ascii=False))
    return RedirectResponse("/settings?portix=" + ("ok" if r.get("ok") else "fail"), status_code=303)


@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and not request.url.path.startswith("/api/"):
        return login_redirect()
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
