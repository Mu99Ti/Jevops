from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from jevops.models import LogEvent, TriageResult

MAX_BURST_EVENTS = 255
SEVERITIES = ("info", "warning", "critical")
CATEGORIES = ("deployment", "capacity", "dependency", "application_bug", "security", "data", "unknown")


class JevError(RuntimeError):
    pass


class JevValidationError(JevError):
    pass


@dataclass(frozen=True)
class RankItem:
    event: LogEvent
    relevance: float
    pageable: float


def triage_questions() -> dict[str, dict[str, Any]]:
    return {
        "severity": {
            "type": "choice",
            "instructions": "On-call severity of this event for the SRE on duty",
            "criteria": {
                "info": "Noise, no action needed",
                "warning": "Degradation with workaround; business-hours action",
                "critical": "User-facing outage, data-loss risk, or sustained SLO burn; pages immediately",
            },
        },
        "category": {
            "type": "choice",
            "instructions": "Most likely root-cause category for this event",
            "criteria": {
                "deployment": "Regression from a recent release, rollout, or configuration change",
                "capacity": "Saturation of a finite resource such as memory, connections, threads, disk, or CPU",
                "dependency": "Upstream or downstream service failure or slowdown",
                "application_bug": "Code defect in the service itself",
                "security": "Attack, intrusion, authentication or authorization anomaly",
                "data": "Data pipeline, storage, or consistency problem",
                "unknown": "Insufficient evidence to attribute a cause",
            },
        },
        "urgency": {
            "type": "score",
            "instructions": "How immediately a human should look at this",
            "criteria": [
                "Can wait until standup",
                "Investigate within the hour",
                "Investigate now",
                "Page the on-call now",
            ],
        },
        "needs_human": {
            "type": "noul",
            "instructions": "An SRE should be interrupted for this right now, not merely shown on a dashboard",
            "criteria": {
                "true": "Wake-up worthy given blast radius and evidence",
                "false": "Dashboard or ticket level",
            },
        },
        "novelty": {
            "type": "noul",
            "instructions": (
                "This is a genuinely new failure pattern, "
                "not a known recurring error already baselined for this service"
            ),
        },
        "recovery": {
            "type": "noul",
            "instructions": (
                "This event indicates the previously paged incident for this service is recovering or already resolved"
            ),
        },
    }


def rank_event_state(events: list[LogEvent]) -> str:
    lines = []
    for i, e in enumerate(events[:MAX_BURST_EVENTS]):
        msg = " ".join(e.message.split())[:300]
        lines.append(f"E{i:03d}| {e.service} {e.env} {e.level}: {msg}")
    return "\n".join(lines)


class JevClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "jev-latest",
        timeout: float = 25.0,
        attempts: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.attempts = attempts
        self._sleep = sleep
        self._client = httpx.Client(timeout=timeout, transport=transport, http2=False)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JevClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @staticmethod
    def _validate_choice(answer: dict[str, Any], ids: set[str]) -> str:
        probs = answer.get("probabilities") or {}
        if set(probs) != ids:
            raise JevValidationError(f"choice probabilities {sorted(probs)} do not match offered {sorted(ids)}")
        values = []
        for value in probs.values():
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise JevValidationError(f"invalid probability {value!r}")
            values.append(value)
        if abs(sum(values) - 1.0) > 0.02:
            raise JevValidationError(f"probabilities sum to {sum(values):.3f}")
        choice = answer.get("choice")
        if choice not in ids:
            raise JevValidationError(f"choice {choice!r} not among offered")
        if probs[choice] != max(values):
            raise JevValidationError("choice is not the highest-probability option")
        return choice

    @staticmethod
    def _noul(answer: dict[str, Any]) -> float:
        value = answer.get("noul")
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise JevValidationError(f"invalid noul {value!r}")
        return float(value)

    def system_one(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        payload = {"model": self.model, "state": state, "questions": questions}
        url = f"{self.base_url}/v1/systemone"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last = ""
        for attempt in range(1, self.attempts + 1):
            try:
                resp = self._client.post(url, headers=headers, content=json.dumps(payload))
            except httpx.TransportError as e:
                last = f"transport: {e}"
                if attempt == self.attempts:
                    break
                self._sleep(0.5 * attempt)
                continue
            if resp.status_code in (429, 503, 529):
                last = resp.text
                if attempt == self.attempts:
                    break
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 0.5 * attempt
                except ValueError:
                    delay = 0.5 * attempt
                self._sleep(delay)
                continue
            if resp.status_code >= 400:
                raise JevError(f"jev {resp.status_code}: {resp.text[:500]}")
            return resp.json()
        raise JevError(f"jev failed after {self.attempts} attempts: {last[:500]}")

    def rank_burst(self, events: list[LogEvent]) -> list[RankItem]:
        if not events:
            return []
        state = rank_event_state(events)
        ids = {f"E{i:03d}" for i in range(min(len(events), MAX_BURST_EVENTS))}
        questions: dict[str, dict[str, Any]] = {
            "worst": {
                "type": "choice",
                "instructions": (
                    "Which event most needs an SRE to look at it right now? "
                    "Rank all events by how much they deserve attention."
                ),
                "criteria": dict.fromkeys(ids, None),
            },
            "page_worthy": {
                "type": "noul",
                "instructions": "At least one event in this window is severe enough to interrupt a human now",
            },
        }
        data = self.system_one(state=state, questions=questions)
        answers = data["answers"]
        worst = answers["worst"]
        self._validate_choice(worst, ids)
        pageable = self._noul(answers["page_worthy"])
        probs = worst["probabilities"]
        items = []
        for i, event in enumerate(events[:MAX_BURST_EVENTS]):
            items.append(RankItem(event=event, relevance=float(probs.get(f"E{i:03d}", 0.0)), pageable=pageable))
        return sorted(items, key=lambda r: r.relevance, reverse=True)

    def triage(self, event: LogEvent) -> TriageResult:
        state = {
            "service": event.service,
            "env": event.env,
            "level": event.level,
            "log": event.message[:4000],
            "timestamp": event.ts,
            "context": {k: v for k, v in event.fields.items() if isinstance(v, (str, int, float, bool))},
        }
        start = time.perf_counter()
        data = self.system_one(state=state, questions=triage_questions())
        latency_ms = int((time.perf_counter() - start) * 1000)
        answers = data["answers"]
        severity_answer = answers["severity"]
        self._validate_choice(severity_answer, set(SEVERITIES))
        category_answer = answers["category"]
        self._validate_choice(category_answer, set(CATEGORIES))
        usage = data.get("usage") or {}
        return TriageResult(
            event_id=event.id,
            model=data.get("model", self.model),
            severity=severity_answer["choice"],
            severity_conf=float(severity_answer.get("confidence", 0.0)),
            severity_probs={k: float(v) for k, v in severity_answer["probabilities"].items()},
            category=category_answer["choice"],
            category_probs={k: float(v) for k, v in category_answer["probabilities"].items()},
            needs_human=self._noul(answers["needs_human"]),
            novelty=self._noul(answers["novelty"]),
            urgency=float(answers["urgency"].get("score", 0.0)),
            is_recovery=self._noul(answers["recovery"]),
            latency_ms=latency_ms,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
        )
