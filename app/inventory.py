from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app.config import get_settings


def load_inventory() -> Dict[str, Any]:
    path = Path(get_settings().inventory_path)
    if not path.exists():
        return {"version": 1, "defaults": {}, "servers": []}
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        return {"version": 1, "defaults": {}, "servers": []}
    data.setdefault("defaults", {})
    data.setdefault("servers", [])
    return data


def list_servers() -> List[Dict[str, Any]]:
    inv = load_inventory()
    defaults = inv.get("defaults") or {}
    out: List[Dict[str, Any]] = []
    for raw in inv.get("servers") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        merged = {**defaults, **raw}
        auth_def = dict(defaults.get("auth") or {})
        auth_srv = dict(raw.get("auth") or {})
        merged["auth"] = {**auth_def, **auth_srv}
        out.append(merged)
    return out


def get_server(server_id: str) -> Optional[Dict[str, Any]]:
    for s in list_servers():
        if s.get("id") == server_id:
            return s
    return None


def resolve_auth(server: Dict[str, Any]) -> Dict[str, Any]:
    auth = dict(server.get("auth") or {})
    method = (auth.get("method") or "key").lower()
    result: Dict[str, Any] = {
        "method": method,
        "user": server.get("ssh_user") or "root",
        "port": int(server.get("ssh_port") or 22),
        "host": server.get("host"),
        "password": None,
        "key_path": None,
        "key_data": None,
    }
    if method == "password":
        env_name = auth.get("password_env")
        if env_name:
            result["password"] = os.environ.get(env_name)
    elif method == "key":
        key_path_env = auth.get("key_path_env")
        key_env = auth.get("key_env")
        if key_path_env and os.environ.get(key_path_env):
            result["key_path"] = os.environ[key_path_env]
        if key_env and os.environ.get(key_env):
            result["key_data"] = os.environ[key_env]
    return result
