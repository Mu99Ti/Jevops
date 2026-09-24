from __future__ import annotations

import json

import httpx

from jevops.models import PageDecision

PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"


def _summary(decision: PageDecision) -> str:
    e = decision.event
    t = decision.triage
    if e is None or t is None:
        return f"Jevops {decision.action}: {decision.reason}"
    return (
        f"[{t.severity.upper()}] {e.service}/{e.env}: {e.message[:140]}\n"
        f"severity_conf={t.severity_conf:.2f} category={t.category} needs_human={t.needs_human:.2f} "
        f"novelty={t.novelty:.2f} urgency={t.urgency:.1f} model={t.model} reason={decision.reason}"
    )


class Notifier:
    def __init__(
        self,
        slack_webhook_url: str = "",
        pagerduty_routing_key: str = "",
        webhook_url: str = "",
        dry_run: bool = False,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.slack_webhook_url = slack_webhook_url
        self.pagerduty_routing_key = pagerduty_routing_key
        self.webhook_url = webhook_url
        self.dry_run = dry_run
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def notify(self, decision: PageDecision) -> None:
        if self.dry_run:
            return
        if self.slack_webhook_url:
            self._client.post(
                self.slack_webhook_url,
                json={"text": _summary(decision)},
            )
        if self.pagerduty_routing_key and decision.dedup_key:
            event_action = "resolve" if decision.action == "resolve" else "trigger"
            self._client.post(
                PAGERDUTY_EVENTS_URL,
                json={
                    "routing_key": self.pagerduty_routing_key,
                    "event_action": event_action,
                    "dedup_key": decision.dedup_key,
                    "payload": {
                        "summary": _summary(decision)[:1024],
                        "source": "jevops",
                        "severity": "critical" if decision.action == "page" else "info",
                        "custom_details": decision.to_dict(),
                    },
                },
            )
        if self.webhook_url:
            self._client.post(
                self.webhook_url, content=json.dumps(decision.to_dict()), headers={"Content-Type": "application/json"}
            )
