from __future__ import annotations

from dataclasses import dataclass

from jevops.models import LogEvent, PageDecision, TriageResult

_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


@dataclass
class ServicePolicy:
    min_confidence: float | None = None
    min_human: float | None = None
    cooldown_seconds: int | None = None
    page_enabled: bool = True


@dataclass
class Policy:
    services: dict[str, ServicePolicy] | None = None
    default_min_confidence: float = 0.8
    default_min_human: float = 0.7
    default_cooldown_seconds: int = 1800

    def _service(self, name: str) -> ServicePolicy:
        return (self.services or {}).get(name, ServicePolicy())

    def cooldown_for(self, name: str) -> int:
        override = self._service(name).cooldown_seconds
        return override if override is not None else self.default_cooldown_seconds

    def decide(
        self,
        event: LogEvent,
        triage: TriageResult,
        *,
        cooldown_active: bool,
        paged: bool,
    ) -> PageDecision:
        svc = self._service(event.service)
        min_conf = svc.min_confidence if svc.min_confidence is not None else self.default_min_confidence
        min_human = svc.min_human if svc.min_human is not None else self.default_min_human
        sev_rank = _SEVERITY_RANK.get(triage.severity, 0)
        sev_norm = sev_rank / 2
        composite = (
            0.4 * sev_norm + 0.3 * triage.needs_human + 0.2 * triage.novelty + 0.1 * min(triage.urgency / 3.0, 1.0)
        )
        dedup_key = f"jevops-{event.service}-{triage.category}"

        if triage.is_recovery >= 0.8 and paged:
            return PageDecision(
                action="resolve",
                reason="recovery signal",
                composite=composite,
                dedup_key=dedup_key,
                triage=triage,
                event=event,
            )

        if svc.page_enabled and triage.severity == "critical":
            if triage.severity_conf < min_conf:
                return PageDecision(
                    action="digest",
                    reason=f"confidence {triage.severity_conf:.2f} below {min_conf:.2f}",
                    composite=composite,
                    triage=triage,
                    event=event,
                )
            if triage.needs_human < min_human:
                return PageDecision(
                    action="digest",
                    reason=f"needs_human {triage.needs_human:.2f} below {min_human:.2f}",
                    composite=composite,
                    triage=triage,
                    event=event,
                )
            if cooldown_active:
                return PageDecision(
                    action="digest",
                    reason="suppressed_by_cooldown",
                    composite=composite,
                    dedup_key=dedup_key,
                    triage=triage,
                    event=event,
                )
            return PageDecision(
                action="page",
                reason="critical meets page thresholds",
                composite=composite,
                dedup_key=dedup_key,
                triage=triage,
                event=event,
            )

        if sev_rank >= _SEVERITY_RANK["warning"]:
            reason = (
                "page_disabled"
                if not svc.page_enabled and triage.severity == "critical"
                else "severity warning or lower"
            )
            return PageDecision(action="digest", reason=reason, composite=composite, triage=triage, event=event)

        return PageDecision(action="ignore", reason="info severity", composite=composite, triage=triage, event=event)
