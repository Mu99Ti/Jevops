from __future__ import annotations

import base64
import json
import urllib.parse
from typing import Any

import httpx

DASHBOARD_UID = "jevops-sre-triage"
ALERT_RULE_UID = "jevops-critical-pages"


class GrafanaClient:
    def __init__(
        self,
        base_url: str,
        token: str = "",
        user: str = "",
        password: str = "",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif user:
            headers["Authorization"] = f"Basic {base64.b64encode(f'{user}:{password}'.encode()).decode()}"
        self._headers = headers
        self._client = httpx.Client(timeout=timeout, transport=transport, headers=headers)

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> httpx.Response:
        resp = self._client.request(
            method, f"{self.base_url}{path}", content=json.dumps(payload) if payload is not None else None
        )
        return resp

    def _json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        resp = self._request(method, path, payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"grafana {method} {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else {}

    def health(self) -> dict[str, Any]:
        return self._json("GET", "/api/health")

    def ensure_datasource(self, name: str, es_url: str, index: str) -> dict[str, Any]:
        payload = {
            "name": name,
            "type": "elasticsearch",
            "access": "proxy",
            "url": es_url,
            "jsonData": {"index": index, "interval": "Daily", "timeField": "@timestamp", "esVersion": "8.0.0"},
        }
        existing = self._request("GET", f"/api/datasources/name/{urllib.parse.quote(name)}")
        if existing.status_code == 200:
            ds_id = existing.json()["id"]
            return self._json("PUT", f"/api/datasources/{ds_id}", payload)["datasource"]
        return self._json("POST", "/api/datasources", payload)["datasource"]

    def ensure_contact_point(self, name: str, url: str) -> dict[str, Any]:
        return self._json(
            "POST", "/api/v1/provisioning/contact-points", {"name": name, "type": "webhook", "settings": {"url": url}}
        )

    def ensure_folder(self, title: str, uid: str = "jevops") -> dict[str, Any]:
        resp = self._request("POST", "/api/folders", {"title": title, "uid": uid})
        if resp.status_code == 412:
            return self._json("PUT", f"/api/folders/{uid}", {"title": title, "uid": uid})
        if resp.status_code >= 400:
            raise RuntimeError(f"grafana POST /api/folders -> {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def _ds_panel(
        self,
        panel_id: int,
        title: str,
        metrics: list[dict[str, Any]],
        ds_uid: str,
        panel_type: str = "timeseries",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        panel: dict[str, Any] = {
            "id": panel_id,
            "title": title,
            "type": panel_type,
            "datasource": {"type": "elasticsearch", "uid": ds_uid},
            "gridPos": {"h": 8, "w": 12, "x": (panel_id - 1) % 2 * 12, "y": (panel_id - 1) // 2 * 8},
            "targets": [
                {
                    "refId": "A",
                    "metrics": metrics,
                    "bucketAggs": [
                        {"type": "date_histogram", "settings": {"interval": "auto"}},
                        {"type": "terms", "settings": {"field": "severity", "size": 5}},
                    ],
                    "timeField": "@timestamp",
                }
            ],
        }
        if extra:
            panel.update(extra)
        return panel

    def ensure_dashboard(self, ds_uid: str, folder_uid: str) -> dict[str, Any]:
        panels = [
            self._ds_panel(1, "Pages by severity", [{"type": "count"}], ds_uid, panel_type="piechart"),
            self._ds_panel(2, "Triage decisions over time", [{"type": "count"}], ds_uid),
            self._ds_panel(
                3,
                "Pages by service",
                [{"type": "count"}],
                ds_uid,
                panel_type="barchart",
                extra={
                    "targets": [
                        {
                            "refId": "A",
                            "metrics": [{"type": "count"}],
                            "bucketAggs": [{"type": "terms", "settings": {"field": "service", "size": 10}}],
                            "timeField": "@timestamp",
                        }
                    ]
                },
            ),
            self._ds_panel(
                4,
                "Recent pages",
                [{"type": "raw_data"}],
                ds_uid,
                panel_type="table",
                extra={
                    "targets": [
                        {
                            "refId": "A",
                            "metrics": [{"type": "raw_data", "settings": {"size": 20}}],
                            "timeField": "@timestamp",
                        }
                    ]
                },
            ),
        ]
        dashboard = {
            "uid": DASHBOARD_UID,
            "title": "Jevops SRE Triage",
            "tags": ["jevops", "sre"],
            "timezone": "browser",
            "schemaVersion": 39,
            "version": 0,
            "panels": panels,
        }
        return self._json(
            "POST", "/api/dashboards/db", {"dashboard": dashboard, "folderUid": folder_uid, "overwrite": True}
        )

    def ensure_alert_rule(self, folder_uid: str, ds_uid: str, receiver: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "uid": ALERT_RULE_UID,
            "title": "Jevops: critical pages in last 5m",
            "folderUID": folder_uid,
            "ruleGroup": "jevops",
            "condition": "B",
            "data": [
                {
                    "refId": "A",
                    "relativeTimeRange": {"from": 300, "to": 0},
                    "datasourceUid": ds_uid,
                    "model": {
                        "refId": "A",
                        "datasource": {"type": "elasticsearch", "uid": ds_uid},
                        "metrics": [{"type": "count", "id": "1"}],
                        "timeField": "@timestamp",
                        "bucketAggs": [
                            {"type": "date_histogram", "settings": {"interval": "auto"}},
                            {"type": "terms", "settings": {"field": "severity", "size": 5}},
                        ],
                        "query": "severity:critical AND action:page",
                    },
                },
                {
                    "refId": "B",
                    "relativeTimeRange": {"from": 0, "to": 0},
                    "datasourceUid": "-100",
                    "model": {
                        "refId": "B",
                        "type": "classic_conditions",
                        "datasource": {"type": "__expr__", "uid": "-100"},
                        "conditions": [
                            {
                                "evaluator": {"type": "gt", "params": [0]},
                                "operator": {"type": "and"},
                                "query": {"params": ["A"]},
                                "reducer": {"type": "last", "params": []},
                                "type": "query",
                            }
                        ],
                    },
                },
            ],
            "noDataState": "NoData",
            "execErrState": "Alerting",
            "for": "1m",
            "isPaused": False,
            "annotations": {"summary": "Jevops paged {{ $labels.service }}"},
            "labels": {"jevops": "1"},
            "notification_settings": {"receiver": receiver},
        }
        resp = self._request("POST", "/api/v1/provisioning/alert-rules", payload)
        if resp.status_code == 412:
            return self._json("PUT", f"/api/v1/provisioning/alert-rules/{ALERT_RULE_UID}", payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"grafana POST /api/v1/provisioning/alert-rules -> {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()
