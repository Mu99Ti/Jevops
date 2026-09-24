from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from jevops.config import Config
from jevops.elastic import ElasticClient
from jevops.jev import JevClient, JevError
from jevops.models import LogEvent, PageDecision
from jevops.notifier import Notifier
from jevops.policy import Policy, ServicePolicy
from jevops.prefilter import build_event, should_consider
from jevops.store import Store


class Pipeline:
    def __init__(
        self,
        *,
        config: Config,
        store: Store,
        jev: JevClient,
        notifier: Notifier,
        es: ElasticClient | None = None,
        noise: tuple[str, ...] = (),
    ) -> None:
        self.config = config
        self.store = store
        self.jev = jev
        self.notifier = notifier
        self.es = es
        self.noise = noise
        services: dict[str, ServicePolicy] = {}
        for name, override in (config.services or {}).items():
            services[name] = ServicePolicy(
                min_confidence=override.min_confidence,
                min_human=override.min_human,
                cooldown_seconds=override.cooldown_seconds,
                page_enabled=override.page_enabled,
            )
        self.policy = Policy(
            services=services,
            default_min_confidence=config.page_min_confidence,
            default_min_human=config.page_min_human,
            default_cooldown_seconds=config.cooldown_seconds,
        )

    def process_events(self, raw_events: list[dict[str, Any]]) -> list[PageDecision]:
        events = [build_event(raw, self.config.max_message) for raw in raw_events if isinstance(raw, dict)]
        candidates = [
            e for e in events if should_consider(e, self.config.min_level, self.noise, self.config.recovery_hints)
        ]
        fresh: list[LogEvent] = []
        for event in candidates:
            if self.store.add_event(event):
                fresh.append(event)
        if not fresh:
            return []

        to_triage = fresh
        if len(fresh) > self.config.top_k:
            try:
                ranked = self.jev.rank_burst(fresh[: self.config.max_burst])
                to_triage = [r.event for r in ranked[: self.config.top_k]]
            except JevError:
                to_triage = fresh[: self.config.top_k]

        decisions: list[PageDecision] = []
        for event in to_triage:
            try:
                triage = self.jev.triage(event)
            except JevError as e:
                decision = PageDecision(action="digest", reason=f"model_error: {str(e)[:120]}", event=event)
                self.store.record_decision(event.id, decision.action, decision.reason, decision.composite)
                decisions.append(decision)
                continue
            self.store.save_triage(triage)
            cooldown = self.store.page_active(event.service, triage.category, self.policy.cooldown_for(event.service))
            paged = self.store.paged_for(event.service)
            decision = self.policy.decide(event, triage, cooldown_active=cooldown, paged=paged)
            self.store.record_decision(event.id, decision.action, decision.reason, decision.composite)
            if decision.action == "page":
                self.notifier.notify(decision)
                self.store.mark_paged(event.id, decision.dedup_key)
            elif decision.action == "resolve":
                self.notifier.notify(decision)
                self.store.mark_resolved(decision.dedup_key)
            decisions.append(decision)
        self._export(decisions)
        return decisions

    def _export(self, decisions: list[PageDecision]) -> None:
        if not self.es or not decisions:
            return
        now = datetime.now(UTC).isoformat()
        docs: list[dict[str, Any]] = []
        for d in decisions:
            e, t = d.event, d.triage
            if e is None or t is None:
                continue
            docs.append(
                {
                    "id": e.id,
                    "@timestamp": e.ts or now,
                    "ts": e.ts,
                    "service": e.service,
                    "env": e.env,
                    "level": e.level,
                    "message": e.message[:1000],
                    "action": d.action,
                    "reason": d.reason,
                    "composite": round(d.composite, 4),
                    "dedup_key": d.dedup_key,
                    "severity": t.severity,
                    "severity_conf": t.severity_conf,
                    "category": t.category,
                    "category_probs": t.category_probs,
                    "needs_human": t.needs_human,
                    "novelty": t.novelty,
                    "urgency": t.urgency,
                    "is_recovery": t.is_recovery,
                    "model": t.model,
                    "latency_ms": t.latency_ms,
                    "input_tokens": t.input_tokens,
                    "output_tokens": t.output_tokens,
                    "at": now,
                }
            )
        if docs:
            self.es.bulk(self.config.es_index_decisions, docs)
