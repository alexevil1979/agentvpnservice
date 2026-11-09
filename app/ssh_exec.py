from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Dict, Optional, Tuple

from app.config import get_settings
from app.inventory import get_server, resolve_auth
from app.redact import redact_text
from app import db

log = logging.getLogger("agentvpnservice.ssh")


def _socks_ports() -> list[int]:
    settings = get_settings()
    preferred = db.get_kv("socks_working_port", settings.socks_preferred or "")
    ports = list(settings.socks_port_list)
    if preferred.isdigit():
        p = int(preferred)
        ports = [p] + [x for x in ports if x != p]
    return ports


def _write_key_file(key_data: str) -> str:
    fd, path = tempfile.mkstemp(prefix="agent-ssh-", suffix=".key")
    os.close(fd)
    Path(path).write_text(key_data if key_data.endswith("\n") else key_data + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def run_remote(
    server_id: str,
    remote_script: str,
    *,
    timeout: int = 120,
) -> Tuple[bool, str]:
    """Run a bash script on the fleet host. Returns (ok, redacted_output)."""
    server = get_server(server_id)
    if not server:
        return False, f"server_id not in inventory: {server_id}"
    auth = resolve_auth(server)
    host = auth.get("host")
    if not host:
        return False, "inventory host empty"
    user = auth["user"]
    port = auth["port"]
    script = textwrap.dedent(remote_script).strip() + "\n"

    attempts: list[Optional[int]] = [None] + _socks_ports()
    last_out = ""
    key_path_tmp: Optional[str] = None
    try:
        identity: Optional[str] = auth.get("key_path")
        if auth.get("key_data"):
            key_path_tmp = _write_key_file(auth["key_data"])
            identity = key_path_tmp

        for socks in attempts:
            cmd = [
                "ssh",
                "-o",
                "BatchMode=yes" if auth["method"] != "password" else "BatchMode=no",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "ConnectTimeout=8",
                "-o",
                "ServerAliveInterval=5",
                "-p",
                str(port),
            ]
            if socks is not None:
                proxy = f"nc -X 5 -x 127.0.0.1:{socks} %h %p"
                cmd += ["-o", f"ProxyCommand={proxy}"]
            if identity and auth["method"] in ("key", "agent"):
                cmd += ["-i", identity]
            cmd.append(f"{user}@{host}")
            cmd.append("bash -s")

            env = os.environ.copy()
            try:
                if auth["method"] == "password":
                    if not auth.get("password"):
                        return False, "password_env not set or empty"
                    # sshpass if available
                    if subprocess.call(["which", "sshpass"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
                        return False, "sshpass required for password auth"
                    cmd = ["sshpass", "-p", auth["password"]] + [
                        c for c in cmd if c != "BatchMode=no"
                    ]
                    # rebuild cleanly
                    cmd = [
                        "sshpass",
                        "-p",
                        auth["password"],
                        "ssh",
                        "-o",
                        "StrictHostKeyChecking=accept-new",
                        "-o",
                        "ConnectTimeout=8",
                        "-o",
                        "PreferredAuthentications=password",
                        "-o",
                        "PubkeyAuthentication=no",
                        "-p",
                        str(port),
                    ]
                    if socks is not None:
                        cmd += ["-o", f"ProxyCommand=nc -X 5 -x 127.0.0.1:{socks} %h %p"]
                    cmd += [f"{user}@{host}", "bash -s"]

                proc = subprocess.run(
                    cmd,
                    input=script,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=env,
                )
                out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
                last_out = out
                if proc.returncode == 0 or "Permission denied" not in out:
                    if socks is not None:
                        db.set_kv("socks_working_port", str(socks))
                    ok = proc.returncode == 0
                    return ok, redact_text(out)
            except subprocess.TimeoutExpired:
                last_out = f"timeout via socks={socks}"
                continue
            except Exception as e:
                last_out = str(e)
                continue
        return False, redact_text(last_out or "ssh unreachable")
    finally:
        if key_path_tmp:
            try:
                os.unlink(key_path_tmp)
            except OSError:
                pass


def probe_reachable(server_id: str) -> Tuple[bool, str]:
    ok, out = run_remote(server_id, "echo OK; hostname; uptime", timeout=40)
    return ok, out
