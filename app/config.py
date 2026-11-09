from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    public_base_url: str = "https://agentvpnservice.1tlt.ru"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8091

    admin_username: str = "admin"
    admin_password: str = "change-me"
    session_secret: str = "change-me-long-random"

    incident_webhook_token: str = "change-me-webhook-token"

    portix_api_key: str = ""
    portix_base_url: str = "https://www.portixapi.com/v1"
    portix_model: str = "grok-4.7"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    socks_ports: str = "1080,1081,1082,1083,1084,1085"
    socks_preferred: str = ""

    dry_run: bool = True
    approval_timeout_minutes: int = 60
    reminder_minutes: int = 15

    inventory_path: str = str(ROOT / "inventory.yml")
    data_dir: str = str(ROOT / "data")
    scripts_dir: str = str(ROOT / "scripts")

    enabled_runbooks: str = (
        "diagnose,disk_hygiene,celery_cpu_harden,celery_worker_restart,haproxy_maps_apply"
    )

    @property
    def socks_port_list(self) -> List[int]:
        ports: List[int] = []
        raw = self.socks_ports or ""
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ports.append(int(part))
            except ValueError:
                continue
        pref = (self.socks_preferred or "").strip()
        if pref.isdigit():
            p = int(pref)
            if p in ports:
                ports = [p] + [x for x in ports if x != p]
            else:
                ports = [p] + ports
        return ports or [1080, 1084]

    @property
    def enabled_runbook_ids(self) -> List[str]:
        return [x.strip() for x in self.enabled_runbooks.split(",") if x.strip()]

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "agent.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
