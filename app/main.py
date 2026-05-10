from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import Settings, load_settings
from app.hosted_tools import build_hosted_tool_context_messages
from app.store import ConversationStore
from app.translator import (
    PreparedChatRequest,
    StreamAccumulator,
    UnsupportedFeatureError,
    build_error,
    make_id,
    prepare_chat_request,
    build_response_from_upstream,
)
from app.upstream import UpstreamChatClient, UpstreamHTTPError


def create_app(
    settings_overrides: dict[str, Any] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = load_settings(settings_overrides)
    store = ConversationStore()
    upstream = UpstreamChatClient(settings, transport=transport)

    app = FastAPI(title=settings.app_name)
    app.state.settings = settings
    app.state.store = store
    app.state.upstream = upstream

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name}

    @app.post("/v1/responses")
    async def create_response(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return error_response(400, "Request body must be valid JSON.")

        bearer_token = extract_bearer_token(authorization)
        authorized = authorize_proxy(settings, bearer_token)
        if authorized is not None:
            return authorized

        conversation_history = load_conversation_history(store, payload)
        if isinstance(conversation_history, JSONResponse):
            return conversation_history

        try:
            hosted_tool_messages = await build_hosted_tool_context_messages(payload, settings, transport)
            prepared = prepare_chat_request(
                payload,
                settings,
                conversation_history,
                hosted_tool_messages=hosted_tool_messages,
            )
        except UnsupportedFeatureError as exc:
            return error_response(400, str(exc))
        except httpx.HTTPError as exc:
            return error_response(502, f"Hosted tool bridge failed: {exc}", error_type="upstream_error")

        upstream_token = settings.upstream_api_key or bearer_token
        if not upstream_token:
            return error_response(
                401,
                "No upstream API key is configured. Set RESPONSES_PROXY_UPSTREAM_API_KEY or send a bearer token.",
                error_type="authentication_error",
            )

        if payload.get("stream"):
            response_id = make_id("resp")
            try:
                upstream_stream = await upstream.open_stream(prepared.upstream_payload, upstream_token)
            except UpstreamHTTPError as exc:
                return JSONResponse(status_code=exc.status_code, content=normalize_upstream_error(exc))
            except httpx.HTTPError as exc:
                return error_response(
                    502,
                    f"Failed to connect to upstream stream: {exc}",
                    error_type="upstream_error",
                )
            return StreamingResponse(
                stream_response(
                    payload=payload,
                    prepared=prepared,
                    response_id=response_id,
                    upstream_stream=upstream_stream,
                    store=store,
                    conversation_history=conversation_history,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        try:
            upstream_response = await upstream.create_completion(prepared.upstream_payload, upstream_token)
            response_id = make_id("resp")
            response, history_output = build_response_from_upstream(payload, upstream_response, response_id)
        except UnsupportedFeatureError as exc:
            return error_response(502, str(exc), error_type="upstream_response_error")
        except UpstreamHTTPError as exc:
            return JSONResponse(status_code=exc.status_code, content=normalize_upstream_error(exc))

        conversation_key = resolve_conversation_key(payload)
        if payload.get("store", True) or conversation_key:
            store.save(
                response_id,
                response,
                prepared.conversation_messages + history_output,
                conversation_key=conversation_key,
                save_response_id=payload.get("store", True),
            )
        return JSONResponse(response)

    @app.get("/v1/responses/{response_id}")
    async def get_response(
        response_id: str,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        authorized = authorize_proxy(settings, extract_bearer_token(authorization))
        if authorized is not None:
            return authorized
        response = store.get_response(response_id)
        if response is None:
            return error_response(404, f"Unknown response_id `{response_id}`.")
        return JSONResponse(response)

    @app.delete("/v1/responses/{response_id}")
    async def delete_response(
        response_id: str,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        authorized = authorize_proxy(settings, extract_bearer_token(authorization))
        if authorized is not None:
            return authorized
        if not store.delete_response(response_id):
            return error_response(404, f"Unknown response_id `{response_id}`.")
        return JSONResponse({"id": response_id, "object": "response", "deleted": True})

    @app.post("/v1/responses/{response_id}/cancel")
    async def cancel_response(
        response_id: str,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        authorized = authorize_proxy(settings, extract_bearer_token(authorization))
        if authorized is not None:
            return authorized
        response = store.cancel_response(response_id)
        if response is None:
            return error_response(404, f"Unknown response_id `{response_id}`.")
        return JSONResponse(response)

    return app


def extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "bearer "
    if authorization.lower().startswith(prefix):
        return authorization[len(prefix):].strip()
    return authorization.strip()


def authorize_proxy(settings: Settings, bearer_token: str | None) -> JSONResponse | None:
    if settings.proxy_api_key is None:
        return None
    if bearer_token == settings.proxy_api_key:
        return None
    return error_response(401, "Invalid proxy bearer token.", error_type="authentication_error")


def load_conversation_history(
    store: ConversationStore,
    payload: dict[str, Any],
) -> list[dict[str, Any]] | JSONResponse:
    previous_response_id = payload.get("previous_response_id")
    if not previous_response_id:
        prompt_cache_key = payload.get("prompt_cache_key")
        if isinstance(prompt_cache_key, str) and prompt_cache_key:
            history = store.get_conversation(prompt_cache_key)
            if history is not None:
                return history
        return []
    history = store.get_conversation(previous_response_id)
    if history is None:
        return error_response(404, f"Unknown previous_response_id `{previous_response_id}`.")
    return history


def resolve_conversation_key(payload: dict[str, Any]) -> str | None:
    prompt_cache_key = payload.get("prompt_cache_key")
    if isinstance(prompt_cache_key, str) and prompt_cache_key:
        return prompt_cache_key
    return None


async def stream_response(
    payload: dict[str, Any],
    prepared: PreparedChatRequest,
    response_id: str,
    upstream_stream,
    store: ConversationStore,
    conversation_history: list[dict[str, Any]],
):
    accumulator = StreamAccumulator(payload=payload, response_id=response_id)
    for event in accumulator.initial_events():
        yield event

    try:
        async for raw_event in upstream_stream.iter_events():
            if raw_event == "[DONE]":
                break
            chunk = json.loads(raw_event)
            for event in accumulator.consume_chunk(chunk):
                yield event
    except json.JSONDecodeError as exc:
        yield accumulator.failed_event(build_error(f"Failed to decode upstream stream chunk: {exc.msg}")["error"])
        return
    except UnsupportedFeatureError as exc:
        yield accumulator.failed_event(build_error(str(exc))["error"])
        return
    except UpstreamHTTPError as exc:
        yield accumulator.failed_event(normalize_upstream_error(exc)["error"])
        return
    except Exception as exc:
        yield accumulator.failed_event(
            build_error(f"Proxy stream error: {exc}", error_type="internal_error")["error"]
        )
        return
    finally:
        await upstream_stream.aclose()

    final_events, response, history_output = accumulator.finalize()
    for event in final_events:
        yield event

    conversation_key = resolve_conversation_key(payload)
    if payload.get("store", True) or conversation_key:
        store.save(
            response_id,
            response,
            prepared.conversation_messages + history_output,
            conversation_key=conversation_key,
            save_response_id=payload.get("store", True),
        )

    yield accumulator.completed_event(response)


def error_response(
    status_code: int,
    message: str,
    error_type: str = "invalid_request_error",
) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=build_error(message, error_type=error_type))


def normalize_upstream_error(exc: UpstreamHTTPError) -> dict[str, Any]:
    if isinstance(exc.payload, dict):
        if "error" in exc.payload and isinstance(exc.payload["error"], dict):
            return exc.payload
        if "detail" in exc.payload:
            return build_error(str(exc.payload["detail"]), error_type="upstream_error")
        return build_error(json.dumps(exc.payload, ensure_ascii=False), error_type="upstream_error")
    return build_error(str(exc.payload), error_type="upstream_error")


app = create_app()
