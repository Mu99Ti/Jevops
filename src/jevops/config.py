from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from jevops.policy import ServicePolicy


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass
class Config:
    typesafe_api_key: str = ""
    typesafe_model: str = "jev-latest"
    typesafe_base_url: str = "https://api.typesafe.ai"
    host: str = "0.0.0.0"
    port: int = 8080
    db_path: str = "jevops.db"
    services_path: str = "services.toml"
    services: dict[str, ServicePolicy] = field(default_factory=dict)
    min_level: str = "ERROR"
    recovery_hints: tuple[str, ...] = (
        "cleared",
        "recovered",
        "recovering",
        "back to normal",
        "back online",
        "green again",
        "all systems healthy",
    )
    noise_path: str = "noise.txt"
    max_message: int = 4000
    max_burst: int = 255
    top_k: int = 3
    page_min_confidence: float = 0.8
    page_min_human: float = 0.7
    cooldown_seconds: int = 1800
    dry_run: bool = False
    es_url: str = ""
    es_username: str = ""
    es_password: str = ""
    es_api_key: str = ""
    es_verify_tls: bool = True
    es_index_source: str = "logs-*"
    es_index_decisions: str = "jevops-decisions"
    es_poll_seconds: int = 10
    es_poll_size: int = 200
    grafana_url: str = ""
    grafana_token: str = ""
    grafana_user: str = ""
    grafana_password: str = ""
    grafana_datasource_name: str = "Jevops Elasticsearch"
    slack_webhook_url: str = ""
    pagerduty_routing_key: str = ""
    webhook_url: str = ""
    text_model_api_key: str = ""
    text_model_base_url: str = ""
    text_model: str = ""
    query_chunks: int = 8
    query_sub: int = 4
    query_depth: int = 4
    query_leaf: int = 5
    query_imp: float = 0.5
    query_max_leaves: int = 50
    query_max_rounds: int = 3

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            typesafe_api_key=os.environ.get("TYPESAFE_API_KEY", ""),
            typesafe_model=os.environ.get("TYPESAFE_MODEL", "jev-latest"),
            typesafe_base_url=os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai"),
            host=os.environ.get("JEVOPS_HOST", "0.0.0.0"),
            port=_i("JEVOPS_PORT", 8080),
            db_path=os.environ.get("JEVOPS_DB", "jevops.db"),
            services_path=os.environ.get("JEVOPS_SERVICES", "services.toml"),
            min_level=os.environ.get("JEVOPS_MIN_LEVEL", "ERROR"),
            recovery_hints=tuple(
                h.strip()
                for h in os.environ.get(
                    "JEVOPS_RECOVERY_HINTS",
                    "cleared,recovered,recovering,back to normal,back online,green again,all systems healthy",
                ).split(",")
                if h.strip()
            ),
            noise_path=os.environ.get("JEVOPS_NOISE", "noise.txt"),
            max_message=_i("JEVOPS_MAX_MESSAGE", 4000),
            max_burst=min(_i("JEVOPS_MAX_BURST", 255), 255),
            top_k=_i("JEVOPS_TOP_K", 3),
            page_min_confidence=_f("JEVOPS_PAGE_MIN_CONFIDENCE", 0.8),
            page_min_human=_f("JEVOPS_PAGE_MIN_HUMAN", 0.7),
            cooldown_seconds=_i("JEVOPS_COOLDOWN_SECONDS", 1800),
            dry_run=os.environ.get("JEVOPS_DRY_RUN", "").lower() in ("1", "true", "yes"),
            es_url=os.environ.get("ES_URL", ""),
            es_username=os.environ.get("ES_USERNAME", ""),
            es_password=os.environ.get("ES_PASSWORD", ""),
            es_api_key=os.environ.get("ES_API_KEY", ""),
            es_verify_tls=os.environ.get("ES_VERIFY_TLS", "true").lower() in ("1", "true", "yes"),
            es_index_source=os.environ.get("ES_INDEX_SOURCE", "logs-*"),
            es_index_decisions=os.environ.get("ES_INDEX_DECISIONS", "jevops-decisions"),
            es_poll_seconds=_i("JEVOPS_ES_POLL_SECONDS", 10),
            es_poll_size=_i("JEVOPS_ES_POLL_SIZE", 200),
            grafana_url=os.environ.get("GRAFANA_URL", ""),
            grafana_token=os.environ.get("GRAFANA_TOKEN", ""),
            grafana_user=os.environ.get("GRAFANA_USER", ""),
            grafana_password=os.environ.get("GRAFANA_PASSWORD", ""),
            grafana_datasource_name=os.environ.get("GRAFANA_DS_NAME", "Jevops Elasticsearch"),
            slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL", ""),
            pagerduty_routing_key=os.environ.get("PAGERDUTY_ROUTING_KEY", ""),
            webhook_url=os.environ.get("JEVOPS_WEBHOOK_URL", ""),
            text_model_api_key=os.environ.get("TEXT_MODEL_API_KEY", ""),
            text_model_base_url=os.environ.get("TEXT_MODEL_BASE_URL", ""),
            text_model=os.environ.get("TEXT_MODEL", ""),
            query_chunks=_i("JEVOPS_QUERY_CHUNKS", 8),
            query_sub=_i("JEVOPS_QUERY_SUB", 4),
            query_depth=_i("JEVOPS_QUERY_DEPTH", 4),
            query_leaf=_i("JEVOPS_QUERY_LEAF", 5),
            query_imp=_f("JEVOPS_QUERY_IMP", 0.5),
            query_max_leaves=_i("JEVOPS_QUERY_MAX_LEAVES", 50),
            query_max_rounds=_i("JEVOPS_QUERY_MAX_ROUNDS", 3),
        )


def load_services_toml(path: Path | str) -> dict[str, ServicePolicy]:
    import tomllib

    p = Path(path)
    if not p.exists():
        return {}
    data = tomllib.loads(p.read_text())
    services: dict[str, ServicePolicy] = {}
    for name, raw in (data.get("service") or {}).items():
        services[name] = ServicePolicy(
            min_confidence=float(raw.get("min_confidence", -1)) if "min_confidence" in raw else None,
            min_human=float(raw.get("min_human", -1)) if "min_human" in raw else None,
            cooldown_seconds=int(raw["cooldown_seconds"]) if "cooldown_seconds" in raw else None,
            page_enabled=bool(raw.get("page_enabled", True)),
        )
    return services
