"""A recorded HTTP double for the news and macro clients (no network).

It speaks the ``httpx``-shaped ``get(url, params=, headers=)`` contract the
data providers use. Responses are keyed by URL path (host included) and are
consumed in order, the last one repeating. A request with no recorded
response raises, because a silently empty response is how a test proves
nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@dataclass
class FakeResponse:
    status_code: int = 200
    content: bytes = b""

    @property
    def text(self) -> str:
        return self.content.decode("utf-8-sig")


@dataclass
class RecordedHttpClient:
    responses: dict[str, list[FakeResponse]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail: dict[str, Exception] = field(default_factory=dict)

    def add(self, url: str, body: bytes | str, status: int = 200) -> RecordedHttpClient:
        key = self._key(url)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.responses.setdefault(key, []).append(FakeResponse(status, data))
        return self

    def add_file(self, url: str, path: str | Path, status: int = 200) -> RecordedHttpClient:
        return self.add(url, (FIXTURES / path).read_bytes(), status)

    @staticmethod
    def _key(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.netloc}{parts.path}"

    def get(self, url: str, *, params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None) -> FakeResponse:
        key = self._key(url)
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        if key in self.fail:
            raise self.fail[key]
        queue = self.responses.get(key)
        if not queue:
            raise AssertionError(f"no recorded response for {key!r}; known: {sorted(self.responses)}")
        return queue.pop(0) if len(queue) > 1 else queue[0]
