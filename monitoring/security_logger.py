"""8.6 Seguridad — Registro de intentos sospechosos de inyección de prompts.

Analiza inputs de usuario buscando patrones comunes de prompt injection
y registra los intentos en security_log.jsonl para que el AlertMonitor
los detecte.

Uso:
    from monitoring.security_logger import SecurityLogger
    logger = SecurityLogger()
    is_suspicious = logger.check_and_log(user_input, source="telegram", user_id="123")
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"

INJECTION_PATTERNS = [
    r"(?i)ignore\s+(all\s+)?previous\s+(instructions|prompts|rules)",
    r"(?i)you\s+are\s+now\s+(?:a|an|the)\s+",
    r"(?i)system\s*:\s*",
    r"(?i)act\s+as\s+(?:a|an|the)\s+",
    r"(?i)forget\s+(all\s+)?(your|previous)\s+",
    r"(?i)new\s+instructions?\s*:",
    r"(?i)override\s+(your|the|all)\s+",
    r"(?i)do\s+not\s+follow\s+(your|the|previous)\s+",
    r"(?i)disregard\s+(all|your|the|previous)\s+",
    r"(?i)\bDAN\b\s+mode",
    r"(?i)jailbreak",
    r"(?i)reveal\s+(your|the|system)\s+(prompt|instructions)",
    r"(?i)what\s+(are|is)\s+your\s+(system\s+)?(prompt|instructions)",
    r"(?i)print\s+your\s+(system|initial)\s+",
    r"(?i)repeat\s+(the|your)\s+(system|initial)\s+(prompt|message|instruction)",
]

COMPILED_PATTERNS = [re.compile(p) for p in INJECTION_PATTERNS]


class SecurityLogger:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._log_path = DATA_DIR / "security_log.jsonl"

    def check_and_log(
        self,
        user_input: str,
        source: str = "unknown",
        user_id: str | None = None,
    ) -> bool:
        if not user_input:
            return False

        matches: list[str] = []
        for pattern in COMPILED_PATTERNS:
            match = pattern.search(user_input)
            if match:
                matches.append(match.group())

        if not matches:
            return False

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "user_id": user_id,
            "patterns_matched": matches,
            "input_preview": user_input[:200],
            "input_length": len(user_input),
        }

        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return True

    def recent_attempts(self, hours: int = 24) -> list[dict[str, Any]]:
        if not self._log_path.exists():
            return []

        import time
        cutoff = time.time() - (hours * 3600)
        entries = []
        with open(self._log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
                    if ts >= cutoff:
                        entries.append(entry)
                except (json.JSONDecodeError, KeyError):
                    continue
        return entries
