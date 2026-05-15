from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from typing import Any

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import Settings, load_settings
from app.hosted_tools import build_hosted_tool_context
from app.protocol import analyze_protocol, strict_protocol_error
from app.store import ConversationStore
from app.translator import (
    PreparedChatRequest,
    StreamAccumulator,
    UnsupportedFeatureError,
    build_error,
    build_response_envelope,
    build_tool_intent_retry_payload,
    build_tool_intent_retry_payload_for_response,
    make_id,
    prepare_chat_request,
    build_response_from_upstream,
    should_retry_tool_intent_response,
)
from app.upstream import UpstreamChatClient, UpstreamHTTPError

STREAM_KEEPALIVE_SECONDS = 1.0
MAX_TOOL_INTENT_RETRIES = 1


def create_app(
    settings_overrides: dict[str, Any] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = load_settings(settings_overrides)
    store = ConversationStore(settings.state_store_path)
    upstream = UpstreamChatClient(settings, transport=transport)
    inflight = InFlightRegistry()

    app = FastAPI(title=settings.app_name)
    app.state.settings = settings
    app.state.store = store
    app.state.upstream = upstream
    app.state.inflight = inflight

    if os.getenv("RESPONSES_PROXY_ENABLE_REQUEST_LOGS") == "1":

        @app.middleware("http")
        async def request_log_middleware(request: Request, call_next):
            started = time.perf_counter()
            status_code = 500
            try:
                response = await call_next(request)
                status_code = response.status_code
                return response
            finally:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                print(
                    f"{request.method} {request.url.path} -> {status_code} ({elapsed_ms} ms)",
                    flush=True,
                )

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

        protocol_report = analyze_protocol(payload)
        if settings.strict_protocol:
            protocol_error = strict_protocol_error(protocol_report)
            if protocol_error:
                return error_response(400, protocol_error)

        conversation_history = load_conversation_history(store, payload)
        if isinstance(conversation_history, JSONResponse):
            return conversation_history

        try:
            hosted_tool_context = await build_hosted_tool_context(payload, settings, transport)
            prepared = prepare_chat_request(
                payload,
                settings,
                conversation_history,
                hosted_tool_messages=hosted_tool_context.messages,
                hosted_output_items=hosted_tool_context.output_items,
                hosted_annotations=hosted_tool_context.annotations,
                protocol_report=protocol_report,
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

        if payload.get("background"):
            response_id = make_id("resp")
            queued_response = build_response_envelope(
                payload=payload,
                response_id=response_id,
                created_at=now_epoch(),
                completed_at=None,
                status="queued",
                output=[],
                output_text="",
                usage=None,
                incomplete_details=None,
                protocol_report=protocol_report,
            )
            store.save(
                response_id,
                queued_response,
                prepared.conversation_messages,
                input_items=prepared.input_items,
                conversation_key=resolve_conversation_key(payload),
                save_response_id=payload.get("store", True),
            )
            task = asyncio.create_task(
                run_background_response(
                    payload=payload,
                    prepared=prepared,
                    response_id=response_id,
                    upstream=upstream,
                    upstream_token=upstream_token,
                    store=store,
                    inflight=inflight,
                )
            )
            inflight.register(response_id, task)
            return JSONResponse(queued_response)

        if payload.get("stream"):
            response_id = make_id("resp")
            return StreamingResponse(
                stream_response(
                    payload=payload,
                    prepared=prepared,
                    response_id=response_id,
                    upstream=upstream,
                    upstream_token=upstream_token,
                    store=store,
                    inflight=inflight,
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
            upstream_response = await create_completion_with_tool_intent_retry(
                payload=payload,
                upstream_payload=prepared.upstream_payload,
                upstream=upstream,
                upstream_token=upstream_token,
            )
            response_id = make_id("resp")
            response, history_output = build_response_from_upstream(
                payload,
                upstream_response,
                response_id,
                hosted_output_items=prepared.hosted_output_items,
                hosted_annotations=prepared.hosted_annotations,
                protocol_report=prepared.protocol_report,
            )
        except UnsupportedFeatureError as exc:
            return error_response(502, str(exc), error_type="upstream_response_error")
        except UpstreamHTTPError as exc:
            return JSONResponse(status_code=normalize_upstream_status_code(exc), content=normalize_upstream_error(exc))

        conversation_key = resolve_conversation_key(payload)
        if payload.get("store", True) or conversation_key:
            store.save(
                response_id,
                response,
                prepared.conversation_messages + history_output,
                input_items=prepared.input_items,
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

    @app.get("/v1/responses/{response_id}/input_items")
    async def list_response_input_items(
        response_id: str,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        authorized = authorize_proxy(settings, extract_bearer_token(authorization))
        if authorized is not None:
            return authorized
        input_items = store.get_input_items(response_id)
        if input_items is None:
            return error_response(404, f"Unknown response_id `{response_id}`.")
        return JSONResponse(build_list_response(input_items))

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
        inflight.cancel(response_id)
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


async def create_completion_with_tool_intent_retry(
    *,
    payload: dict[str, Any],
    upstream_payload: dict[str, Any],
    upstream: UpstreamChatClient,
    upstream_token: str,
) -> dict[str, Any]:
    upstream_response = await upstream.create_completion(upstream_payload, upstream_token)
    if should_retry_tool_intent_response(payload, upstream_response):
        retry_payload = build_tool_intent_retry_payload_for_response(upstream_payload, upstream_response)
        upstream_response = await upstream.create_completion(retry_payload, upstream_token)
    return upstream_response


async def stream_response(
    payload: dict[str, Any],
    prepared: PreparedChatRequest,
    response_id: str,
    upstream: UpstreamChatClient,
    upstream_token: str,
    store: ConversationStore,
    inflight: "InFlightRegistry",
    conversation_history: list[dict[str, Any]],
):
    current_task = asyncio.current_task()
    if current_task is not None:
        inflight.register(response_id, current_task)
    accumulator = StreamAccumulator(
        payload=payload,
        response_id=response_id,
        hosted_output_items=prepared.hosted_output_items,
        hosted_annotations=prepared.hosted_annotations,
        protocol_report=prepared.protocol_report,
    )
    for event in accumulator.initial_events():
        yield event

    active_upstream_payload = prepared.upstream_payload
    retry_count = 0
    try:
        while True:
            upstream_stream = None
            next_event_task = None
            open_stream_task = asyncio.create_task(upstream.open_stream(active_upstream_payload, upstream_token))
            try:
                while True:
                    done, _ = await asyncio.wait({open_stream_task}, timeout=STREAM_KEEPALIVE_SECONDS)
                    if done:
                        upstream_stream = open_stream_task.result()
                        break
                    yield accumulator.in_progress_event()

                upstream_events = upstream_stream.iter_events().__aiter__()
                next_event_task = asyncio.create_task(upstream_events.__anext__())
                while True:
                    done, _ = await asyncio.wait({next_event_task}, timeout=STREAM_KEEPALIVE_SECONDS)
                    if not done:
                        yield accumulator.in_progress_event()
                        continue
                    try:
                        raw_event = next_event_task.result()
                    except StopAsyncIteration:
                        break
                    next_event_task = asyncio.create_task(upstream_events.__anext__())
                    if raw_event == "[DONE]":
                        break
                    chunk = json.loads(raw_event)
                    if is_upstream_stream_error_payload(chunk):
                        raise UpstreamHTTPError(502, chunk)
                    for event in accumulator.consume_chunk(chunk):
                        yield event
            finally:
                if not open_stream_task.done():
                    open_stream_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await open_stream_task
                if next_event_task is not None and not next_event_task.done():
                    next_event_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await next_event_task
                if upstream_stream is not None:
                    await upstream_stream.aclose()

            if retry_count < MAX_TOOL_INTENT_RETRIES and accumulator.should_retry_tool_intent():
                intent_text = accumulator.reset_for_tool_intent_retry()
                active_upstream_payload = build_tool_intent_retry_payload(active_upstream_payload, intent_text)
                retry_count += 1
                yield accumulator.in_progress_event()
                continue
            break
    except json.JSONDecodeError as exc:
        error = build_error(f"Failed to decode upstream stream chunk: {exc.msg}")["error"]
        store_failed_stream_response(payload, prepared, response_id, store, error)
        yield accumulator.failed_event(error)
        return
    except UnsupportedFeatureError as exc:
        error = build_error(str(exc))["error"]
        store_failed_stream_response(payload, prepared, response_id, store, error)
        yield accumulator.failed_event(error)
        return
    except UpstreamHTTPError as exc:
        error = normalize_upstream_error(exc)["error"]
        store_failed_stream_response(payload, prepared, response_id, store, error)
        yield accumulator.failed_event(error)
        return
    except httpx.HTTPError as exc:
        error = build_error(f"Failed to connect to upstream stream: {exc}", error_type="upstream_error")["error"]
        store_failed_stream_response(payload, prepared, response_id, store, error)
        yield accumulator.failed_event(error)
        return
    except Exception as exc:
        error = build_error(f"Proxy stream error: {exc}", error_type="internal_error")["error"]
        store_failed_stream_response(payload, prepared, response_id, store, error)
        yield accumulator.failed_event(error)
        return
    finally:
        inflight.unregister(response_id)

    final_events, response, history_output = accumulator.finalize()
    for event in final_events:
        yield event

    conversation_key = resolve_conversation_key(payload)
    if payload.get("store", True) or conversation_key:
        store.save(
            response_id,
            response,
            prepared.conversation_messages + history_output,
            input_items=prepared.input_items,
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
    if is_upstream_image_input_unsupported(exc):
        return build_error(
            "当前上游模型不支持图片输入。请切换到支持视觉/多模态的模型，或关闭图片输入后重试。"
        )
    if isinstance(exc.payload, dict):
        if "error" in exc.payload and isinstance(exc.payload["error"], dict):
            return exc.payload
        if "detail" in exc.payload:
            return build_error(str(exc.payload["detail"]), error_type="upstream_error")
        return build_error(json.dumps(exc.payload, ensure_ascii=False), error_type="upstream_error")
    return build_error(str(exc.payload), error_type="upstream_error")


def normalize_upstream_status_code(exc: UpstreamHTTPError) -> int:
    if is_upstream_image_input_unsupported(exc):
        return 400
    return exc.status_code


def is_upstream_image_input_unsupported(exc: UpstreamHTTPError) -> bool:
    payload_text = json.dumps(exc.payload, ensure_ascii=False).lower()
    return (
        "image input" in payload_text
        and (
            "no endpoints found" in payload_text
            or "not support" in payload_text
            or "unsupported" in payload_text
        )
    )


def is_upstream_stream_error_payload(chunk: dict[str, Any]) -> bool:
    return isinstance(chunk.get("error"), dict) and not chunk.get("choices")


class InFlightRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def register(self, response_id: str, task: asyncio.Task[Any]) -> None:
        self._tasks[response_id] = task

    def unregister(self, response_id: str) -> None:
        self._tasks.pop(response_id, None)

    def cancel(self, response_id: str) -> bool:
        task = self._tasks.get(response_id)
        if task is None:
            return False
        task.cancel()
        return True


async def run_background_response(
    *,
    payload: dict[str, Any],
    prepared: PreparedChatRequest,
    response_id: str,
    upstream: UpstreamChatClient,
    upstream_token: str,
    store: ConversationStore,
    inflight: InFlightRegistry,
) -> None:
    in_progress = build_response_envelope(
        payload=payload,
        response_id=response_id,
        created_at=now_epoch(),
        completed_at=None,
        status="in_progress",
        output=[],
        output_text="",
        usage=None,
        incomplete_details=None,
        protocol_report=prepared.protocol_report,
    )
    store.update_response(response_id, in_progress)
    try:
        upstream_response = await create_completion_with_tool_intent_retry(
            payload=payload,
            upstream_payload=prepared.upstream_payload,
            upstream=upstream,
            upstream_token=upstream_token,
        )
        response, history_output = build_response_from_upstream(
            payload,
            upstream_response,
            response_id,
            hosted_output_items=prepared.hosted_output_items,
            hosted_annotations=prepared.hosted_annotations,
            protocol_report=prepared.protocol_report,
        )
        store.save(
            response_id,
            response,
            prepared.conversation_messages + history_output,
            input_items=prepared.input_items,
            conversation_key=resolve_conversation_key(payload),
            save_response_id=payload.get("store", True),
        )
    except asyncio.CancelledError:
        store.cancel_response(response_id)
        raise
    except UpstreamHTTPError as exc:
        store.update_response(response_id, failed_response(payload, response_id, normalize_upstream_error(exc)["error"]))
    except Exception as exc:
        store.update_response(
            response_id,
            failed_response(
                payload,
                response_id,
                build_error(f"Background response failed: {exc}", error_type="internal_error")["error"],
            ),
        )
    finally:
        inflight.unregister(response_id)


def failed_response(payload: dict[str, Any], response_id: str, error: dict[str, Any]) -> dict[str, Any]:
    return build_response_envelope(
        payload=payload,
        response_id=response_id,
        created_at=now_epoch(),
        completed_at=now_epoch(),
        status="failed",
        output=[],
        output_text="",
        usage=None,
        incomplete_details=None,
        error=error,
    )


def store_failed_stream_response(
    payload: dict[str, Any],
    prepared: PreparedChatRequest,
    response_id: str,
    store: ConversationStore,
    error: dict[str, Any],
) -> None:
    conversation_key = resolve_conversation_key(payload)
    if not (payload.get("store", True) or conversation_key):
        return
    store.save(
        response_id,
        failed_response(payload, response_id, error),
        prepared.conversation_messages,
        input_items=prepared.input_items,
        conversation_key=conversation_key,
        save_response_id=payload.get("store", True),
    )


def build_list_response(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "object": "list",
        "data": items,
        "first_id": items[0]["id"] if items else None,
        "last_id": items[-1]["id"] if items else None,
        "has_more": False,
    }


def now_epoch() -> int:
    import time

    return int(time.time())


app = create_app()
