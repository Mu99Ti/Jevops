from __future__ import annotations

import base64
import json
from typing import Any

import httpx


class ElasticClient:
    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        api_key: str = "",
        verify: bool = True,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"ApiKey {api_key}"
        elif username:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        self._headers = headers
        self._client = httpx.Client(timeout=timeout, transport=transport, verify=verify, headers=headers)

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        resp = self._client.request(method, f"{self.base_url}{path}", **kwargs)
        if resp.status_code >= 400 and method != "HEAD":
            raise RuntimeError(f"elasticsearch {method} {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp

    def ping(self) -> str:
        data = self._request("GET", "/").json()
        return str(data.get("version", {}).get("number", ""))

    def ensure_index(self, index: str, mappings: dict[str, Any]) -> None:
        exists = self._request("HEAD", f"/{index}")
        if exists.status_code == 200:
            return
        self._request("PUT", f"/{index}", content=json.dumps({"mappings": mappings}))

    def bulk(self, index: str, docs: list[dict[str, Any]]) -> int:
        if not docs:
            return 0
        lines: list[str] = []
        for doc in docs:
            doc_id = str(doc.pop("id", "")) if "id" in doc else ""
            meta = {"index": {"_id": doc_id}} if doc_id else {"index": {}}
            lines.append(json.dumps(meta))
            lines.append(json.dumps(doc))
        body = "\n".join(lines) + "\n"
        resp = self._request(
            "POST",
            f"/{index}/_bulk",
            content=body,
            headers={"Content-Type": "application/x-ndjson"},
        )
        data = resp.json()
        if data.get("errors"):
            reasons = []
            for item in data.get("items", []):
                err = item.get("index", {}).get("error", {})
                reasons.append(str(err.get("reason", err)))
            raise RuntimeError(f"bulk indexing failed: {'; '.join(reasons)[:300]}")
        return len(docs)

    def search_since(
        self,
        index_pattern: str,
        since: str,
        size: int = 200,
        search_after: list[Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[Any]]:
        body: dict[str, Any] = {
            "size": size,
            "query": {"range": {"@timestamp": {"gte": since}}},
            "sort": [{"@timestamp": {"order": "asc"}}, {"_id": {"order": "asc"}}],
        }
        if search_after:
            body["search_after"] = search_after
        resp = self._request("POST", f"/{index_pattern}/_search", content=json.dumps(body))
        hits = resp.json().get("hits", {}).get("hits", [])
        sources = [hit.get("_source", {}) for hit in hits]
        cursor: list[Any] = hits[-1].get("sort") if hits else (search_after or [])
        return sources, cursor
