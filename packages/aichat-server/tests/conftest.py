from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from aichat_server.config import Settings
from aichat_server.main import create_app

OP_KEY = "test-operator-key"


@dataclass
class Actor:
    name: str
    token: str
    client: httpx.AsyncClient
    headers: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        self.headers = {"Authorization": f"Bearer {self.token}"}

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.client.get(url, headers=self.headers, **kw)

    async def post(self, url: str, json: Any = None, **kw: Any) -> httpx.Response:
        headers = {**self.headers, **kw.pop("headers", {})}
        return await self.client.post(url, json=json, headers=headers, **kw)

    async def put(self, url: str, json: Any = None) -> httpx.Response:
        return await self.client.put(url, json=json, headers=self.headers)

    async def delete(self, url: str, json: Any = None) -> httpx.Response:
        return await self.client.request("DELETE", url, json=json, headers=self.headers)

    async def ok(self, method: str, url: str, json: Any = None, **kw: Any) -> Any:
        if method == "get":
            resp = await self.get(url, **kw)
        else:
            resp = await getattr(self, method)(url, json, **kw)
        assert resp.status_code == 200, resp.text
        return resp.json()


class Hub:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.op = {"X-Operator-Key": OP_KEY}

    async def namespace(self, admin: str = "main") -> tuple[str, Actor]:
        resp = await self.client.post("/ops/namespaces", json={"admin_name": admin}, headers=self.op)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        return body["ns_id"], await self.join(body["join_code"])

    async def join(self, code: str) -> Actor:
        resp = await self.client.post("/join", json={"code": code})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        return Actor(body["name"], body["token"], self.client)

    async def agent(self, creator: Actor, name: str, **extra: Any) -> Actor:
        body = await creator.ok("post", "/admin/agents", {"name": name, **extra})
        return await self.join(body["join_code"])

    async def child(self, parent: Actor, name: str, **extra: Any) -> Actor:
        body = await parent.ok("post", "/agents/children", {"name": name, **extra})
        return await self.join(body["join_code"])

    async def human(self, ns: str, name: str = "human-alice") -> Actor:
        resp = await self.client.post(f"/ops/ns/{ns}/humans", json={"name": name}, headers=self.op)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        return Actor(body["name"], body["token"], self.client)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        operator_key=OP_KEY,
        rate_per_sec=10_000,
        rate_burst=10_000,
        join_rate_per_sec=10_000,
        join_rate_burst=10_000,
        max_wait=5,
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub") as c:
        yield c


@pytest.fixture
def hub(client) -> Hub:
    return Hub(client)


def code_of(resp: httpx.Response) -> str:
    return resp.json()["error"]["code"]
