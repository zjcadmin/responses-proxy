from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
from typing import Any

import httpx

from app.config import Settings


class UpstreamHTTPError(RuntimeError):
    def __init__(self, status_code: int, payload: Any) -> None:
        super().__init__(f"Upstream request failed with status {status_code}")
        self.status_code = status_code
        self.payload = payload


@dataclass
class UpstreamStreamHandle:
    client: httpx.AsyncClient
    response: httpx.Response

    async def iter_events(self) -> AsyncIterator[str]:
        async for line in self.response.aiter_lines():
            if not line:
                continue
            if line.startswith("data: "):
                yield line[6:]
            elif line.startswith("data:"):
                yield line[5:]

    async def aclose(self) -> None:
        await self.response.aclose()
        await self.client.aclose()


class UpstreamChatClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    async def create_completion(
        self,
        payload: dict[str, Any],
        bearer_token: str,
    ) -> dict[str, Any]:
        async with self._build_client() as client:
            response = await client.post(
                self._settings.upstream_chat_path,
                headers=self._headers(bearer_token),
                json=payload,
            )
            await self._raise_for_error(response)
            return response.json()

    async def stream_completion(
        self,
        payload: dict[str, Any],
        bearer_token: str,
    ) -> AsyncIterator[str]:
        stream_handle = await self.open_stream(payload, bearer_token)
        try:
            async for event in stream_handle.iter_events():
                yield event
        finally:
            await stream_handle.aclose()

    async def open_stream(
        self,
        payload: dict[str, Any],
        bearer_token: str,
    ) -> UpstreamStreamHandle:
        client = self._build_client()
        try:
            response = await client.send(
                client.build_request(
                    "POST",
                    self._settings.upstream_chat_path,
                    headers=self._headers(bearer_token),
                    json=payload,
                ),
                stream=True,
            )
            await self._raise_for_error(response)
        except Exception:
            await client.aclose()
            raise
        return UpstreamStreamHandle(client=client, response=response)

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._settings.upstream_base_url,
            timeout=self._settings.request_timeout_seconds,
            transport=self._transport,
        )

    def _headers(self, bearer_token: str) -> dict[str, str]:
        headers = dict(self._settings.upstream_headers)
        header_name = self._settings.upstream_api_key_header_name
        if bearer_token and header_name:
            prefix = self._settings.upstream_api_key_prefix
            header_value = compose_auth_header_value(prefix, bearer_token)
            headers.setdefault(header_name, header_value)
        return headers

    @staticmethod
    async def _raise_for_error(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raw_body = await response.aread()
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {"error": {"message": ""}}
        except ValueError:
            payload = {"error": {"message": raw_body.decode("utf-8", errors="replace")}}
        raise UpstreamHTTPError(response.status_code, payload)


def compose_auth_header_value(prefix: str, token: str) -> str:
    normalized_prefix = prefix.strip()
    if not normalized_prefix:
        return token
    if normalized_prefix.endswith(("=", ":", "/")):
        return f"{normalized_prefix}{token}"
    return f"{normalized_prefix} {token}"
