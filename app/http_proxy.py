from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import httpx

from app.config import get_settings
from app import db

log = logging.getLogger("agentvpnservice.http")


def _proxy_urls() -> list[Optional[str]]:
    settings = get_settings()
    urls: list[Optional[str]] = [None]
    preferred = db.get_kv("socks_working_port", settings.socks_preferred or "")
    ports = list(settings.socks_port_list)
    if preferred.isdigit():
        p = int(preferred)
        ports = [p] + [x for x in ports if x != p]
    for p in ports:
        urls.append(f"socks5h://127.0.0.1:{p}")
    return urls


def remember_socks(proxy_url: Optional[str]) -> None:
    if not proxy_url:
        db.set_kv("socks_working_port", "")
        return
    if "127.0.0.1:" in proxy_url:
        port = proxy_url.rsplit(":", 1)[-1]
        db.set_kv("socks_working_port", port)


async def request_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json_body: Any = None,
    timeout: float = 60.0,
) -> Tuple[int, Any, Optional[str]]:
    """Try direct then SOCKS. Returns status, parsed body, proxy_used."""
    last_err: Optional[Exception] = None
    for proxy in _proxy_urls():
        try:
            kwargs: Dict[str, Any] = {"timeout": timeout}
            if proxy:
                kwargs["proxy"] = proxy
            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.request(method, url, headers=headers, json=json_body)
                remember_socks(proxy)
                try:
                    body: Any = resp.json()
                except Exception:
                    body = {"raw": resp.text[:2000]}
                return resp.status_code, body, proxy
        except Exception as e:
            last_err = e
            log.warning("http via %s failed: %s", proxy or "direct", e)
            continue
    raise RuntimeError(f"all transports failed for {url}: {last_err}")


async def request_bytes(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    data: Any = None,
    json_body: Any = None,
    timeout: float = 60.0,
) -> Tuple[int, bytes, Optional[str]]:
    last_err: Optional[Exception] = None
    for proxy in _proxy_urls():
        try:
            kwargs: Dict[str, Any] = {"timeout": timeout}
            if proxy:
                kwargs["proxy"] = proxy
            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.request(
                    method, url, headers=headers, data=data, json=json_body
                )
                remember_socks(proxy)
                return resp.status_code, resp.content, proxy
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"all transports failed for {url}: {last_err}")
