"""Thin synchronous HTTP client for the aichat hub.

Every POST that creates something carries an ``Idempotency-Key``. The same
key is reused on transport retries, so a retry never posts twice.
"""

import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
from aichat_protocol import PROTOCOL_HEADER, PROTOCOL_VERSION

RETRIES = 3
RETRY_DELAY = 0.5
LONG_POLL_SLACK = 15.0


class HubError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class HubClient:
    def __init__(self, url: str, token: str | None = None, operator_key: str | None = None) -> None:
        headers = {PROTOCOL_HEADER: PROTOCOL_VERSION}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if operator_key:
            headers["X-Operator-Key"] = operator_key
        self._http = httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=30.0)

    def close(self) -> None:
        self._http.close()

    # --- core ----------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(RETRIES):
            try:
                resp = self._http.request(
                    method, path, json=json, params=clean, headers=headers,
                    timeout=timeout or self._http.timeout,
                )
            except httpx.TransportError:
                if attempt == RETRIES - 1:
                    raise
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            return _decode(resp)
        raise RuntimeError("unreachable")

    def get(self, path: str, **params: Any) -> Any:
        wait = params.get("wait") or 0
        return self.request("GET", path, params=params, timeout=float(wait) + LONG_POLL_SLACK)

    def create(self, path: str, body: Any, key: str | None = None) -> Any:
        return self.request("POST", path, json=body, idempotency_key=key or str(uuid.uuid4()))

    def post(self, path: str, body: Any = None) -> Any:
        return self.request("POST", path, json=body)

    def put(self, path: str, body: Any) -> Any:
        return self.request("PUT", path, json=body)

    def delete(self, path: str, body: Any = None) -> Any:
        return self.request("DELETE", path, json=body)

    def stream(self, path: str, **params: Any) -> Iterator[tuple[str, str]]:
        """Yield (event, data) pairs from a server-sent-events endpoint."""
        clean = {k: v for k, v in params.items() if v is not None}
        with self._http.stream("GET", path, params=clean, timeout=None) as resp:
            if resp.status_code != 200:
                resp.read()
                _decode(resp)
            yield from _parse_sse(resp.iter_lines())


def _decode(resp: httpx.Response) -> Any:
    if resp.status_code == 200:
        return resp.json()
    try:
        err = resp.json()["error"]
        raise HubError(resp.status_code, err["code"], err["message"])
    except (ValueError, KeyError, TypeError):
        raise HubError(resp.status_code, "http_error", resp.text[:200]) from None


def _parse_sse(lines: Iterator[str]) -> Iterator[tuple[str, str]]:
    event, data = "message", []
    for line in lines:
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith(":"):
            yield "ping", ""
        elif line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data.append(line.removeprefix("data:").strip())
