from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jevops.config import Config, load_services_toml
from jevops.elastic import ElasticClient
from jevops.grafana import GrafanaClient
from jevops.intake import build_server
from jevops.jev import JevClient
from jevops.llm import LLMClient
from jevops.notifier import Notifier
from jevops.pipeline import Pipeline
from jevops.prefilter import load_noise_patterns
from jevops.query import run_query
from jevops.store import Store

DECISIONS_MAPPINGS = {
    "properties": {
        "service": {"type": "keyword"},
        "env": {"type": "keyword"},
        "action": {"type": "keyword"},
        "severity": {"type": "keyword"},
        "category": {"type": "keyword"},
        "model": {"type": "keyword"},
        "dedup_key": {"type": "keyword"},
        "severity_conf": {"type": "float"},
        "needs_human": {"type": "float"},
        "novelty": {"type": "float"},
        "urgency": {"type": "float"},
        "is_recovery": {"type": "float"},
        "composite": {"type": "float"},
        "latency_ms": {"type": "integer"},
        "ts": {"type": "date"},
        "@timestamp": {"type": "date"},
        "at": {"type": "date"},
    }
}

CONTACT_POINT_NAME = "jevops-notify"


def _load_config() -> Config:
    config = Config.from_env()
    config.services = load_services_toml(config.services_path)
    return config


def _build_pipeline(config: Config) -> Pipeline:
    store = Store(config.db_path)
    jev = JevClient(base_url=config.typesafe_base_url, api_key=config.typesafe_api_key, model=config.typesafe_model)
    notifier = Notifier(
        slack_webhook_url=config.slack_webhook_url,
        pagerduty_routing_key=config.pagerduty_routing_key,
        webhook_url=config.webhook_url,
        dry_run=config.dry_run,
    )
    es = None
    if config.es_url:
        es = ElasticClient(
            config.es_url,
            username=config.es_username,
            password=config.es_password,
            api_key=config.es_api_key,
            verify=config.es_verify_tls,
        )
    return Pipeline(
        config=config,
        store=store,
        jev=jev,
        notifier=notifier,
        es=es,
        noise=load_noise_patterns(Path(config.noise_path)),
    )


def _build_llm(config: Config) -> LLMClient | None:
    if not config.text_model_base_url or not config.text_model_api_key:
        return None
    return LLMClient(config.text_model_base_url, api_key=config.text_model_api_key, model=config.text_model)


def _cmd_serve(config: Config) -> int:
    pipeline = _build_pipeline(config)
    llm = _build_llm(config)

    def query_fn(payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload.get("question") or "").strip()
        if not question:
            return {"error": "question is required"}
        now = datetime.now(UTC)
        start = str(payload.get("from") or (now - timedelta(hours=4)).isoformat())
        end = str(payload.get("to") or now.isoformat())
        try:
            return run_query(pipeline.store, pipeline.jev, llm, question, start, end, config)
        except ValueError as e:
            return {"error": str(e)}

    server = build_server(pipeline, config.host, config.port, query_fn=query_fn)
    print(
        json.dumps(
            {
                "listening": f"http://{config.host}:{config.port}",
                "endpoints": ["/ingest", "/healthz", "/decisions", "/query"],
            }
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


def _cmd_query(config: Config, question: str, from_s: str | None, to_s: str | None) -> int:
    pipeline = _build_pipeline(config)
    llm = _build_llm(config)
    now = datetime.now(UTC)
    start = from_s or (now - timedelta(hours=4)).isoformat()
    end = to_s or now.isoformat()
    try:
        result = run_query(pipeline.store, pipeline.jev, llm, question, start, end, config)
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0


def _cmd_poll_es(config: Config) -> int:
    if not config.es_url:
        print(json.dumps({"error": "ES_URL is required for poll-es"}))
        return 2
    pipeline = _build_pipeline(config)
    es = pipeline.es
    assert es is not None
    since = pipeline.store.checkpoint_get("es") or "now-5m"
    while True:
        hits, cursor = es.search_since(config.es_index_source, since, size=config.es_poll_size)
        if not hits:
            time.sleep(config.es_poll_seconds)
            continue
        decisions = pipeline.process_events(hits)
        newest = max((h.get("@timestamp") or h.get("timestamp") or since) for h in hits)
        pipeline.store.checkpoint_set("es", newest)
        print(json.dumps({"polled": len(hits), "decisions": [d.to_dict() for d in decisions], "since": newest}))
        if len(hits) < config.es_poll_size:
            time.sleep(config.es_poll_seconds)


def _cmd_provision(config: Config) -> int:
    results: dict[str, Any] = {}
    if config.es_url:
        es = ElasticClient(
            config.es_url,
            username=config.es_username,
            password=config.es_password,
            api_key=config.es_api_key,
            verify=config.es_verify_tls,
        )
        es.ensure_index(config.es_index_decisions, DECISIONS_MAPPINGS)
        results["elasticsearch"] = {"index": config.es_index_decisions, "version": es.ping()}
    if config.grafana_url:
        g = GrafanaClient(
            config.grafana_url, token=config.grafana_token, user=config.grafana_user, password=config.grafana_password
        )
        results["grafana_health"] = g.health()
        ds = g.ensure_datasource(config.grafana_datasource_name, config.es_url, f"{config.es_index_decisions}*")
        results["datasource"] = {"name": ds.get("name"), "uid": ds.get("uid")}
        notify_url = config.webhook_url or f"http://127.0.0.1:{config.port}/ingest"
        cp = g.ensure_contact_point(CONTACT_POINT_NAME, notify_url)
        results["contact_point"] = {"name": CONTACT_POINT_NAME, "uid": cp.get("uid")}
        folder = g.ensure_folder("Jevops")
        dashboard = g.ensure_dashboard(ds["uid"], folder["uid"])
        results["dashboard"] = {
            "uid": dashboard.get("uid"),
            "url": f"{config.grafana_url.rstrip('/')}/d/{dashboard.get('uid')}",
        }
        rule = g.ensure_alert_rule(folder["uid"], ds["uid"], CONTACT_POINT_NAME)
        results["alert_rule"] = {"uid": rule.get("uid"), "title": rule.get("title")}
    if not results:
        print(json.dumps({"error": "set ES_URL and/or GRAFANA_URL to provision"}))
        return 2
    print(json.dumps(results, indent=2))
    return 0


def _cmd_replay(config: Config, path: str) -> int:
    events: list[dict[str, Any]] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        if isinstance(data, list):
            events.extend(data)
        else:
            events.append(data)
    pipeline = _build_pipeline(config)
    decisions = pipeline.process_events(events)
    print(json.dumps({"events": len(events), "decisions": [d.to_dict() for d in decisions]}, indent=2))
    return 0


def _cmd_selfcheck(config: Config) -> int:
    report: dict[str, Any] = {}
    try:
        jev = JevClient(base_url=config.typesafe_base_url, api_key=config.typesafe_api_key, model=config.typesafe_model)
        data = jev.system_one("selftest", {"ok": {"type": "noul", "instructions": "Is this a connectivity self-test?"}})
        report["jev"] = {"ok": True, "model": data.get("model"), "noul": data["answers"]["ok"].get("noul")}
    except Exception as e:
        report["jev"] = {"ok": False, "error": str(e)[:200]}
    if config.es_url:
        try:
            report["elasticsearch"] = {
                "ok": True,
                "version": ElasticClient(
                    config.es_url,
                    username=config.es_username,
                    password=config.es_password,
                    api_key=config.es_api_key,
                    verify=config.es_verify_tls,
                ).ping(),
            }
        except Exception as e:
            report["elasticsearch"] = {"ok": False, "error": str(e)[:200]}
    if config.grafana_url:
        try:
            report["grafana"] = {
                "ok": True,
                "health": GrafanaClient(
                    config.grafana_url,
                    token=config.grafana_token,
                    user=config.grafana_user,
                    password=config.grafana_password,
                ).health(),
            }
        except Exception as e:
            report["grafana"] = {"ok": False, "error": str(e)[:200]}
    print(json.dumps(report, indent=2))
    return 0 if all(v.get("ok", True) for v in report.values()) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jevops", description="SRE log triage with TypeSafe Jev over ELK + Grafana")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the HTTP intake server")
    sub.add_parser("poll-es", help="poll Elasticsearch for new log events and triage them")
    sub.add_parser(
        "provision",
        help="create the Elasticsearch index and Grafana datasource, dashboard, contact point, and alert rule",
    )
    sub.add_parser("selfcheck", help="verify TypeSafe, Elasticsearch, and Grafana connectivity")
    replay = sub.add_parser("replay", help="triage a JSONL file of events")
    replay.add_argument("path")
    query = sub.add_parser("query", help="ask what is important in a time range (hierarchical drill-down + LLM answer)")
    query.add_argument("question")
    query.add_argument("--from", dest="from_time", default=None, help="ISO start, e.g. 2026-09-24T08:00:00Z")
    query.add_argument("--to", dest="to_time", default=None, help="ISO end (default now)")
    args = parser.parse_args(argv)
    config = _load_config()
    if args.command == "serve":
        return _cmd_serve(config)
    if args.command == "poll-es":
        return _cmd_poll_es(config)
    if args.command == "provision":
        return _cmd_provision(config)
    if args.command == "replay":
        return _cmd_replay(config, args.path)
    if args.command == "query":
        return _cmd_query(config, args.question, args.from_time, args.to_time)
    if args.command == "selfcheck":
        return _cmd_selfcheck(config)
    return 1


if __name__ == "__main__":
    sys.exit(main())
