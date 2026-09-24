from __future__ import annotations

import hashlib
import re
from pathlib import Path

from jevops.models import LogEvent

_NUM_RE = re.compile(r"\d+(?:\.\d+)*")
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")

_LEVEL_RANK = {
    "DEBUG": 10,
    "INFO": 20,
    "NOTICE": 25,
    "WARN": 30,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
    "FATAL": 50,
    "ALERT": 60,
}


def normalize(message: str) -> str:
    text = _UUID_RE.sub("uuid", message)
    text = _HEX_RE.sub("hex", text)
    text = _NUM_RE.sub("num", text)
    return text.lower()


def fingerprint(service: str, level: str, message: str) -> str:
    basis = f"{service.lower()}|{level.lower()}|{normalize(message)}"
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


def _level_rank(level: str) -> int:
    return _LEVEL_RANK.get(level.upper(), 20)


def should_consider(
    event: LogEvent,
    min_level: str = "ERROR",
    noise_patterns: tuple[str, ...] = (),
    recovery_hints: tuple[str, ...] = (),
) -> bool:
    if _level_rank(event.level) < _level_rank(min_level):
        lowered = event.message.lower()
        if not recovery_hints or not any(hint.lower() in lowered for hint in recovery_hints):
            return False
    lowered = event.message.lower()
    return not any(pattern.lower() in lowered for pattern in noise_patterns)


def build_event(data: dict, max_message: int = 4000) -> LogEvent:
    base = LogEvent.from_mapping(data)
    message = base.message[:max_message]
    return LogEvent(
        id=fingerprint(base.service, base.level, base.message),
        ts=base.ts,
        service=base.service,
        env=base.env,
        level=base.level.upper(),
        message=message,
        fields=base.fields,
    )


def load_noise_patterns(path: Path | None) -> tuple[str, ...]:
    if path is None or not path.exists():
        return ()
    return tuple(line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#"))
