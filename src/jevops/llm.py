from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

READ_LINES_TOOL = {
    "type": "function",
    "function": {
        "name": "read_log_lines",
        "description": (
            "Fetch the full raw text of specific log lines by their ids, e.g. ['L0003']. "
            "Use when the summary is not enough."
        ),
        "parameters": {
            "type": "object",
            "properties": {"ids": {"type": "array", "items": {"type": "string"}, "description": "Line ids like L0003"}},
            "required": ["ids"],
        },
    },
}

SYSTEM_PROMPT = (
    "You are a senior SRE analyst answering questions about a production system from its logs. "
    "You receive the most important log lines selected by a decision model. "
    "Answer concisely: what happened, when it started, blast radius, and the single most likely cause. "
    "Cite evidence with line ids in square brackets like [L0003]. "
    "If a line id appears insufficient, call read_log_lines with the ids to fetch full raw lines. "
    "Never invent line ids."
)

_CITE_RE = re.compile(r"\[(L\d+)\]")


@dataclass(frozen=True)
class LineRef:
    id: str
    ts: str
    service: str
    level: str
    message: str

    def render(self) -> str:
        return f"{self.id} | {self.ts} | {self.service} | {self.level} | {self.message[:400]}"


@dataclass
class QueryAnswer:
    answer: str
    cited: list[str] = field(default_factory=list)
    rounds: int = 0
    tool_rounds: int = 0
    truncated: bool = False
    usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "cited": self.cited,
            "rounds": self.rounds,
            "tool_rounds": self.tool_rounds,
            "truncated": self.truncated,
            "usage": self.usage,
        }


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 90.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client = httpx.Client(timeout=timeout, transport=transport, headers={"Content-Type": "application/json"})

    def close(self) -> None:
        self._client.close()

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        resp = self._client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            content=json.dumps(payload),
        )
        if resp.status_code >= 400:
            raise LLMError(f"llm {resp.status_code}: {resp.text[:300]}")
        return resp.json()


class LLMError(RuntimeError):
    pass


def explain(llm: LLMClient, question: str, lines: dict[str, LineRef], max_rounds: int = 3) -> QueryAnswer:
    timeline = "\n".join(ref.render() for ref in lines.values())
    user = f"Question: {question}\n\nImportant log lines:\n{timeline}"
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    usage: dict[str, int] = {}
    rounds = tool_rounds = 0
    while rounds < max_rounds:
        rounds += 1
        data = llm.chat(messages, tools=[READ_LINES_TOOL])
        if data.get("usage"):
            for k, v in data["usage"].items():
                if isinstance(v, int):
                    usage[k] = usage.get(k, 0) + v
        message = (data.get("choices") or [{}])[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            answer = message.get("content") or ""
            cited = [cid for cid in dict.fromkeys(_CITE_RE.findall(answer)) if cid in lines]
            return QueryAnswer(answer=answer, cited=cited, rounds=rounds, tool_rounds=tool_rounds, usage=usage)
        tool_rounds += 1
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls})
        for call in tool_calls:
            fn = call.get("function") or {}
            if fn.get("name") != "read_log_lines":
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": "unknown tool"})
                continue
            try:
                ids = json.loads(fn.get("arguments") or "{}").get("ids") or []
            except json.JSONDecodeError:
                ids = []
            known = [lines[i] for i in ids if i in lines]
            unknown = [i for i in ids if i not in lines]
            body_lines = [ref.render() for ref in known]
            if unknown:
                body_lines.append(f"unknown ids: {', '.join(unknown)}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": "\n".join(body_lines) or "no matching lines",
                }
            )
    return QueryAnswer(answer="", cited=[], rounds=rounds, tool_rounds=tool_rounds, truncated=True, usage=usage)
