from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_TS_KEYS = ("@timestamp", "timestamp", "time", "ts", "event_time")
_SERVICE_KEYS = ("service", "service_name", "app", "kubernetes.labels.app")
_ENV_KEYS = ("env", "environment", "deployment.environment")
_LEVEL_KEYS = ("level", "levelname", "severity", "log.level")
_MSG_KEYS = ("message", "msg", "log", "event")


def _first(mapping: dict[str, Any], keys: tuple[str, ...], default: Any) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return default


@dataclass(frozen=True)
class LogEvent:
    id: str
    ts: str
    service: str
    env: str
    level: str
    message: str
    fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any], event_id: str = "") -> LogEvent:
        consumed = set()
        for keys in (_TS_KEYS, _SERVICE_KEYS, _ENV_KEYS, _LEVEL_KEYS, _MSG_KEYS):
            for key in keys:
                if key in data:
                    consumed.add(key)
        return cls(
            id=event_id,
            ts=str(_first(data, _TS_KEYS, "")),
            service=str(_first(data, _SERVICE_KEYS, "unknown")),
            env=str(_first(data, _ENV_KEYS, "unknown")),
            level=str(_first(data, _LEVEL_KEYS, "INFO")),
            message=str(_first(data, _MSG_KEYS, "")),
            fields={k: v for k, v in data.items() if k not in consumed},
        )


@dataclass(frozen=True)
class TriageResult:
    event_id: str
    model: str
    severity: str
    severity_conf: float
    severity_probs: dict[str, float]
    category: str
    category_probs: dict[str, float]
    needs_human: float
    novelty: float
    urgency: float
    is_recovery: float
    latency_ms: int
    input_tokens: int
    output_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "model": self.model,
            "severity": self.severity,
            "severity_conf": self.severity_conf,
            "severity_probs": self.severity_probs,
            "category": self.category,
            "category_probs": self.category_probs,
            "needs_human": self.needs_human,
            "novelty": self.novelty,
            "urgency": self.urgency,
            "is_recovery": self.is_recovery,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass(frozen=True)
class PageDecision:
    action: str
    reason: str
    composite: float = 0.0
    dedup_key: str = ""
    triage: TriageResult | None = None
    event: LogEvent | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action,
            "reason": self.reason,
            "composite": round(self.composite, 4),
            "dedup_key": self.dedup_key,
        }
        if self.triage:
            payload["triage"] = self.triage.to_dict()
        if self.event:
            payload["event"] = {
                "id": self.event.id,
                "ts": self.event.ts,
                "service": self.event.service,
                "env": self.event.env,
                "level": self.event.level,
                "message": self.event.message[:500],
            }
        return payload
