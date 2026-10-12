"""Общие фикстуры: подменённый httpx, который записывает запросы."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Каждому тесту — своё хранилище секретов и пустое окружение."""
    monkeypatch.setenv("SABY_MCP_HOME", str(tmp_path / "home"))
    for var in ("SBIS_SESSION_ID",):
        monkeypatch.delenv(var, raising=False)


class FakeResponse:
    def __init__(self, payload: Any, status: int = 200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload, ensure_ascii=False)

    @property
    def headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def json(self) -> Any:
        return self._payload


class Recorder:
    """Заглушка httpx.AsyncClient: пишет запросы и отвечает заготовками."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.responses: list[FakeResponse] = []

    def push(self, payload: Any, status: int = 200) -> None:
        self.responses.append(FakeResponse(payload, status))

    def result(self, value: Any) -> None:
        """Ответ-обёртка: {"jsonrpc":"2.0","id":0,"result": value}."""
        self.push({"jsonrpc": "2.0", "id": 0, "result": value})

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()

    class FakeClient:
        def __init__(self, *a, **kw):
            self.timeout = kw.get("timeout", 30.0)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None, **kw):
            rec.calls.append({"url": url, "json": json, "headers": headers or {}})
            if rec.responses:
                return rec.responses.pop(0)
            return FakeResponse({"jsonrpc": "2.0", "id": 0, "result": {}})

        async def request(self, method, url, **kw):
            return await self.post(url, json=kw.get("json"), headers=kw.get("headers"))

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return rec


@pytest.fixture
def client():
    from saby_mcp_ro.client import SabyClient

    return SabyClient(session_id="TEST-SESSION")
