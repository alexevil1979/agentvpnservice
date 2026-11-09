from __future__ import annotations

import re

_SECRET_LINE = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|private[_-]?key|"
    r"BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY)"
)
_BEARER = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{8,})")
_ENV_ASSIGN = re.compile(
    r"(?i)^(\s*(?:export\s+)?[A-Z0-9_]*(?:PASSWORD|TOKEN|SECRET|KEY|PASS)[A-Z0-9_]*\s*=\s*).+$"
)


def redact_text(text: str, max_len: int = 8000) -> str:
    if not text:
        return ""
    out_lines = []
    for line in text.splitlines():
        if _SECRET_LINE.search(line) and ("=" in line or ":" in line or "BEGIN" in line.upper()):
            if "BEGIN" in line.upper() and "PRIVATE" in line.upper():
                out_lines.append("[REDACTED PRIVATE KEY BLOCK]")
                continue
            m = _ENV_ASSIGN.match(line)
            if m:
                out_lines.append(m.group(1) + "***")
                continue
            out_lines.append("[REDACTED]")
            continue
        line = _BEARER.sub(r"\1***", line)
        out_lines.append(line)
    out = "\n".join(out_lines)
    if len(out) > max_len:
        out = out[:max_len] + "\n…[truncated]"
    return out
