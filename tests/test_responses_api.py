from __future__ import annotations

from collections.abc import Iterator
import asyncio
import base64
import json
import re
import time
from typing import Any

import httpx
from fastapi.testclient import TestClient

import app.main as main_module
import app.translator as translator_module
from app.main import create_app


def _make_client(handler, settings_overrides: dict[str, Any] | None = None) -> TestClient:
    transport = httpx.MockTransport(handler)
    overrides = {
        "upstream_base_url": "https://upstream.example/v1",
        "upstream_model": "deepseek-chat",
        "upstream_api_key": "upstream-secret",
        "proxy_api_key": "proxy-secret",
        "request_timeout_seconds": 10.0,
        "upstream_supports_image_input": False,
    }
    if settings_overrides:
        overrides.update(settings_overrides)
    app = create_app(
        overrides,
        transport=transport,
    )
    return TestClient(app)


def test_json_response_is_translated_to_chat_completions() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["url"] = str(request.url)
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello from upstream"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 4,
                    "total_tokens": 15,
                },
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Say hello"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert recorded["url"] == "https://upstream.example/v1/chat/completions"
    assert recorded["json"]["messages"] == [{"role": "user", "content": "Say hello"}]
    assert payload["object"] == "response"
    assert payload["status"] == "completed"
    assert payload["output_text"] == "Hello from upstream"
    assert payload["completed_at"] >= payload["created_at"]
    assert payload["parallel_tool_calls"] is True
    assert payload["previous_response_id"] is None
    assert payload["store"] is True
    assert payload["text"] == {"format": {"type": "text"}}
    assert payload["tool_choice"] == "auto"
    assert payload["tools"] == []
    assert payload["metadata"] == {}
    assert payload["usage"] == {
        "input_tokens": 11,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 4,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 15,
    }


def test_developer_role_is_mapped_to_system_for_upstream_chat() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-dev-role",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Acknowledged"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 2,
                    "total_tokens": 14,
                },
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": [
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "Follow the repo conventions."}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Say hello"}],
                },
            ],
        },
    )

    assert response.status_code == 200
    assert recorded["json"]["messages"] == [
        {"role": "system", "content": "Follow the repo conventions."},
        {"role": "user", "content": "Say hello"},
    ]


def test_openai_hosted_tools_are_ignored_for_chat_completions_upstream() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-hosted-tools",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "I answered without hosted tools."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 7,
                    "total_tokens": 19,
                },
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Say hello",
            "tools": [
                {"type": "web_search"},
                {"type": "function", "name": "echo", "parameters": {"type": "object", "properties": {}}},
            ],
            "tool_choice": {"type": "web_search"},
        },
    )

    assert response.status_code == 200
    assert recorded["json"]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert "tool_choice" not in recorded["json"]


def test_custom_upstream_headers_are_sent_with_configured_auth_header() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-custom-headers",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Headers look good."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                },
            },
        )

    client = _make_client(
        handler,
        settings_overrides={
            "upstream_headers": {"X-Provider": "mimo"},
            "upstream_api_key_header_name": "X-API-Key",
            "upstream_api_key_prefix": "",
        },
    )

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Say hello"},
    )

    assert response.status_code == 200
    assert recorded["headers"]["x-provider"] == "mimo"
    assert recorded["headers"]["x-api-key"] == "upstream-secret"
    assert "authorization" not in recorded["headers"]


def test_bearer_prefix_without_trailing_space_is_normalized_for_auth_header() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-bearer-prefix",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Prefix normalized."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 3,
                    "total_tokens": 13,
                },
            },
        )

    client = _make_client(
        handler,
        settings_overrides={
            "upstream_api_key_header_name": "Authorization",
            "upstream_api_key_prefix": "Bearer",
        },
    )

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Say hello"},
    )

    assert response.status_code == 200
    assert recorded["headers"]["authorization"] == "Bearer upstream-secret"


def test_namespace_tools_are_flattened_into_compatible_function_tools() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-namespace-tools",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Namespace tools were flattened."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 13,
                    "completion_tokens": 6,
                    "total_tokens": 19,
                },
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Say hello",
            "tools": [
                {
                    "type": "namespace",
                    "name": "local",
                    "tools": [
                        {
                            "type": "function",
                            "name": "echo",
                            "parameters": {"type": "object", "properties": {}},
                        },
                        {"type": "web_search"},
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert recorded["json"]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def test_function_tools_are_sanitized_for_strict_chat_providers() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-sanitized-tools",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Tools accepted."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 13, "completion_tokens": 3, "total_tokens": 16},
            },
        )

    client = _make_client(handler, {"upstream_supports_image_input": True})

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Use a tool",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "description": "Echo text",
                        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
                        "strict": True,
                        "x-provider-extra": "reject-me",
                    },
                    "strict": True,
                    "server_label": "custom",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert recorded["json"]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo text",
                "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
            },
        }
    ]


def test_tool_requests_default_to_explicit_auto_tool_choice_and_agent_hint() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-agent-hint",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler, settings_overrides={"upstream_model": "deepseek-chat"})

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Read files and write a report",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    assert recorded["json"]["tool_choice"] == "auto"
    assert recorded["json"]["messages"][0]["role"] == "system"
    assert "Do not stop after saying you will run or inspect something" in recorded["json"]["messages"][0]["content"]
    assert "native function tool interface" in recorded["json"]["messages"][0]["content"]
    assert "<tool_call>" not in recorded["json"]["messages"][0]["content"]
    assert recorded["json"]["messages"][1:] == [{"role": "user", "content": "Read files and write a report"}]


def test_mimo_tool_requests_use_prompt_tool_mode_instead_of_native_tools() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-mimo-prompt-tools",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '<tool_call>{"name":"shell_command","arguments":{"command":"ls -la"}}</tool_call>',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(
        handler,
        settings_overrides={
            "upstream_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "upstream_model": "mimo-v2.5",
            "tool_call_mode": "prompt",
        },
    )

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "请查看当前目录文件列表",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    assert "tools" not in recorded["json"]
    assert "tool_choice" not in recorded["json"]
    assert "Available tools:" in recorded["json"]["messages"][0]["content"]
    assert "If the user asks in Chinese" in recorded["json"]["messages"][0]["content"]
    assert "Never ask the user to provide the exact shell command" in recorded["json"]["messages"][0]["content"]
    assert "appears corrupted" in recorded["json"]["messages"][0]["content"]
    assert "Current proxy host platform:" in recorded["json"]["messages"][0]["content"]
    assert "Use only the tool names listed below" in recorded["json"]["messages"][0]["content"]
    assert "single reusable script" in recorded["json"]["messages"][0]["content"]
    assert "Do not issue repeated python -c" in recorded["json"]["messages"][0]["content"]
    assert "Do not embed large base64 payloads" in recorded["json"]["messages"][0]["content"]
    assert "Do not use apply_patch" in recorded["json"]["messages"][0]["content"]
    assert "write_file" in recorded["json"]["messages"][0]["content"]
    assert "parameters JSON schema" not in recorded["json"]["messages"][0]["content"]
    assert response.json()["output"][0]["type"] == "function_call"


def test_mimo_tool_requests_use_native_tools_by_default() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-mimo-native-tools",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_native",
                                    "type": "function",
                                    "function": {
                                        "name": "shell_command",
                                        "arguments": '{"command":"Get-ChildItem"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(
        handler,
        settings_overrides={
            "upstream_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "upstream_model": "mimo-v2.5-pro",
        },
    )

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "List files",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    assert "tools" in recorded["json"]
    assert recorded["json"]["tool_choice"] == "auto"
    assert "<tool_call>" not in json.dumps(recorded["json"]["messages"], ensure_ascii=False)
    item = response.json()["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "shell_command"


def test_mimo_native_tools_hide_update_plan_when_execution_tool_is_available() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-mimo-filtered-tools",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(
        handler,
        settings_overrides={
            "upstream_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "upstream_model": "mimo-v2.5-pro",
        },
    )

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create a file.",
            "tools": [
                {
                    "type": "function",
                    "name": "update_plan",
                    "parameters": {
                        "type": "object",
                        "properties": {"plan": {"type": "array"}},
                    },
                },
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                        "required": ["command"],
                    },
                },
            ],
        },
    )

    assert response.status_code == 200
    upstream_tool_names = [
        tool["function"]["name"]
        for tool in recorded["json"]["tools"]
        if isinstance(tool.get("function"), dict)
    ]
    assert upstream_tool_names == ["shell"]
    assert "update_plan" not in recorded["json"]["messages"][0]["content"]
    assert "Available native function tools: shell." in recorded["json"]["messages"][0]["content"]


def test_native_tool_intent_text_is_retried_as_function_call() -> None:
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            {
                "id": "chatcmpl-intent-only",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "继续写入完整的消息同步看板 HTML 文件。"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            {
                "id": "chatcmpl-retry-tool",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_retry",
                                    "type": "function",
                                    "function": {
                                        "name": "shell",
                                        "arguments": '{"command":["powershell.exe","-NoProfile","-Command","Write-Output ok"]}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=next(responses))

    client = _make_client(
        handler,
        settings_overrides={
            "upstream_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "upstream_model": "mimo-v2.5-pro",
        },
    )

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "继续",
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    assert len(requests) == 2
    retry_messages = requests[1]["messages"]
    assert retry_messages[-1]["role"] == "user"
    assert "did not call a tool" in retry_messages[-1]["content"]
    payload = response.json()
    assert payload["output"][0]["type"] == "function_call"
    assert payload["output"][0]["name"] == "shell"
    assert "继续写入完整" not in payload["output_text"]


def test_native_tool_call_suppresses_action_preamble_text() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-tool-with-preamble",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Let me write the full HTML directly via PowerShell.",
                            "tool_calls": [
                                {
                                    "id": "call_native_preamble",
                                    "type": "function",
                                    "function": {
                                        "name": "shell",
                                        "arguments": '{"command":["powershell.exe","-NoProfile","-Command","Write-Output ok"]}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(
        handler,
        settings_overrides={
            "upstream_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "upstream_model": "mimo-v2.5-pro",
        },
    )

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "继续",
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["output_text"] == ""
    assert len(payload["output"]) == 1
    assert payload["output"][0]["type"] == "function_call"
    assert "Let me write" not in json.dumps(payload, ensure_ascii=False)


def test_text_tool_call_marker_is_converted_to_response_function_call() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-text-tool",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '<tool_call>{"name":"shell_command","arguments":{"command":"Get-ChildItem"}}</tool_call>',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler, {"upstream_supports_image_input": True})

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "List files",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["output_text"] == ""
    assert payload["output"][0]["type"] == "function_call"
    assert payload["output"][0]["name"] == "shell_command"
    assert json.loads(payload["output"][0]["arguments"]) == {"command": "Get-ChildItem"}


def test_xml_style_text_tool_call_marker_is_converted_without_leaking_text() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-xml-text-tool",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "Let me inspect the folder first.\n"
                                "<tool_call>\n"
                                "<function=shell>\n"
                                '<parameter=command>["dir", "E:\\AI\\needs"]</parameter>\n'
                                "</function>\n"
                                "</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "List files",
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["output_text"] == ""
    assert payload["output"][0]["type"] == "function_call"
    assert payload["output"][0]["name"] == "shell"
    assert json.loads(payload["output"][0]["arguments"]) == {"command": ["dir", "E:\\AI\\needs"]}
    assert "<tool_call>" not in json.dumps(payload, ensure_ascii=False)


def test_xml_style_shell_alias_array_command_is_coerced_for_string_command_schema() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-xml-shell-alias",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "<tool_call><function=shell>"
                                '<parameter=command>["dir", "E:\\AI needs"]</parameter>'
                                "</function></tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "List files",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    item = response.json()["output"][0]
    assert item["name"] == "shell_command"
    arguments = json.loads(item["arguments"])
    assert arguments["command"].startswith("dir ")
    assert "AI needs" in arguments["command"]


def test_relaxed_function_colon_shell_marker_is_converted_without_leaking_text() -> None:
    command = (
        '["python", "-c", '
        '"import os; os.makedirs(r\'C:\\Users\\zjc\\Documents\\Codex\\2026-05-11\\html\', exist_ok=True)"]'
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-relaxed-shell-marker",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "Let me write the file using Python instead."
                                "<tool_call>\n"
                                "function:shell\n"
                                f"<parameter=command>{command}</parameter>\n"
                                "</function>\n"
                                "</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create the referenced HTML page.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "<tool_call>" not in json.dumps(payload, ensure_ascii=False)
    item = payload["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "shell_command"
    arguments = json.loads(item["arguments"])
    assert "python" in arguments["command"]
    assert "os.makedirs" in arguments["command"]
    assert "2026-05-11" in arguments["command"]


def test_tag_named_shell_marker_is_converted_without_leaking_text() -> None:
    command = '["mkdir", "-p", "C:\\\\Users\\\\zjc\\\\Documents\\\\Codex\\\\2026-05-11\\\\new-chat-2"]'

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-tag-named-shell-marker",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "我来仔细分析截图并生成对应的HTML页面。\n\n"
                                "<tool_call>\n"
                                "<shell>\n"
                                f"<command>{command}</command>\n"
                                "</shell>\n"
                                "</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create the referenced HTML page.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "<tool_call>" not in json.dumps(payload, ensure_ascii=False)
    item = payload["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "shell_command"
    arguments = json.loads(item["arguments"])
    assert "New-Item" in arguments["command"]
    assert "mkdir -p" not in arguments["command"]
    assert "new-chat-2" in arguments["command"]


def test_large_python_dash_c_shell_command_is_rewritten_to_temp_script_runner() -> None:
    inline_script = (
        "import base64\n"
        "html_b64 = '''" + ("PCFET0NUWVBFIGh0bWw+" * 80) + "'''\n"
        "print(base64.b64decode(html_b64))\n"
    )
    command = json.dumps(["python", "-c", inline_script])

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-large-python-c",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "<tool_call>\n"
                                "<shell>\n"
                                f"<command>{command}</command>\n"
                                "</shell>\n"
                                "</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Write the HTML file.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    item = response.json()["output"][0]
    arguments = json.loads(item["arguments"])
    assert "script-" in arguments["command"]
    assert ".py" in arguments["command"]
    assert "Test-Path" in arguments["command"]
    assert "FromBase64String" in arguments["command"]
    assert "Remove-Item" not in arguments["command"]
    assert "python -c" not in arguments["command"]


def test_large_python_dash_c_string_shell_command_is_rewritten_to_temp_script_runner() -> None:
    inline_script = (
        "import base64\n"
        "html_b64 = '" + ("PCFET0NUWVBFIGh0bWw+" * 80) + "'\n"
        "print(base64.b64decode(html_b64))\n"
    )
    command = f'python -c "{inline_script}"'

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-large-python-c-string",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "<tool_call>\n"
                                "<shell>\n"
                                f"<command>{command}</command>\n"
                                "</shell>\n"
                                "</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Write the HTML file.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    item = response.json()["output"][0]
    arguments = json.loads(item["arguments"])
    assert "script-" in arguments["command"]
    assert ".py" in arguments["command"]
    assert "Test-Path" in arguments["command"]
    assert "FromBase64String" in arguments["command"]
    assert "Remove-Item" not in arguments["command"]
    assert "python -c" not in arguments["command"]


def test_python_dash_c_string_parser_keeps_inner_quotes() -> None:
    inline_script = 'import base64\nprint("hello")\npayload = "' + ("A" * 900) + '"\n'
    command = f'python -c "{inline_script}"'

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-python-c-inner-quotes",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "<tool_call>\n"
                                "<shell>\n"
                                f"<command>{command}</command>\n"
                                "</shell>\n"
                                "</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Run generated script.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    arguments = json.loads(response.json()["output"][0]["arguments"])
    assert "script-" in arguments["command"]
    assert ".py" in arguments["command"]
    assert "Remove-Item" not in arguments["command"]
    assert "python -c" not in arguments["command"]


def test_oversized_python_dash_c_shell_command_is_spooled_to_script() -> None:
    inline_script = "import base64\npayload = '" + ("A" * 20_000) + "'\nprint(len(payload))\n"
    command = json.dumps(["python", "-c", inline_script])

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-oversized-python-c",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "<tool_call>\n"
                                "<shell>\n"
                                f"<command>{command}</command>\n"
                                "</shell>\n"
                                "</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Write the HTML file.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    item = response.json()["output"][0]
    arguments = json.loads(item["arguments"])
    assert "script-" in arguments["command"]
    assert ".py" in arguments["command"]
    assert "blocked an oversized inline Python command" not in arguments["command"]
    assert "Remove-Item" not in arguments["command"]
    assert "python -c" not in arguments["command"]


def test_xml_read_marker_is_translated_to_shell_command_without_leaking_text() -> None:
    requested_path = "C:/Users/zjc/.codex/plugins/cache/openai-primary-runtime/documents/SKILL.md"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-xml-read-alias",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "<tool_call>\n"
                                "<function=read>\n"
                                f"<parameter=path>{requested_path}</parameter>\n"
                                "</function>\n"
                                "</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Read the document skill.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "<tool_call>" not in json.dumps(payload, ensure_ascii=False)
    item = payload["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "shell_command"
    arguments = json.loads(item["arguments"])
    assert requested_path in arguments["command"]
    assert "Get-Content" in arguments["command"] or "cat" in arguments["command"]


def test_xml_apply_patch_command_marker_is_translated_to_shell_write_without_leaking_text() -> None:
    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: E:\\workspace\\scripts\\gen_spec.py\n"
        "+# coding: utf-8\n"
        "+print('hello')\n"
        "*** End Patch"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-xml-apply-patch",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "<tool_call>\n"
                                "<function=apply_patch>\n"
                                f"<parameter=command>{json.dumps(['apply_patch', patch_text])}</parameter>\n"
                                "</function>\n"
                                "</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Write the file",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "<tool_call>" not in json.dumps(payload, ensure_ascii=False)
    item = payload["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "shell_command"
    arguments = json.loads(item["arguments"])
    assert isinstance(arguments["command"], str)
    assert "gen_spec.py" in arguments["command"]
    assert "WriteAllText" in arguments["command"]
    assert "FromBase64String" in arguments["command"]


def test_xml_apply_patch_marker_with_relaxed_windows_path_and_escaped_newlines_is_translated() -> None:
    relaxed_command = (
        '["apply_patch", "*** Begin Patch\\n'
        "*** Update File: E:\\个人文件\\AI\\需规\\scripts\\gen_spec.py\\n"
        "+# coding: utf-8\\n"
        "+print(1)\\n"
        '*** End Patch"]'
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-xml-relaxed-apply-patch",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "<tool_call>\n"
                                "<function=apply_patch>\n"
                                f"<parameter=command>{relaxed_command}</parameter>\n"
                                "</function>\n"
                                "</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Write the file",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "<tool_call>" not in json.dumps(payload, ensure_ascii=False)
    item = payload["output"][0]
    assert item["name"] == "shell_command"
    arguments = json.loads(item["arguments"])
    arguments["command"].encode("ascii")
    assert "FromBase64String" in arguments["command"]
    assert "个人文件" not in arguments["command"]
    assert "WriteAllText" in arguments["command"]


def test_unclosed_xml_apply_patch_create_file_marker_is_translated_without_leaking_text() -> None:
    patch_text = (
        "*** Begin Patch\n"
        "*** Create File: E:\\workspace\\map\\gen_mimo_page.py\n"
        "+import os\n"
        "+path = os.path.join(os.getcwd(), 'mimo-model.html')\n"
        "+print(path)\n"
        "*** End Patch"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-unclosed-xml-apply-patch",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "Let me write a Python script file instead to avoid escaping issues."
                                "<tool_call>\n"
                                "<function=apply_patch>\n"
                                f"<parameter=command>{patch_text}"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Write the page generator.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "<tool_call>" not in serialized
    assert "Let me write a Python script file" not in serialized
    item = payload["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "shell_command"
    arguments = json.loads(item["arguments"])
    assert "gen_mimo_page.py" in arguments["command"]
    assert "WriteAllText" in arguments["command"]
    assert "FromBase64String" in arguments["command"]


def test_apply_patch_file_path_new_content_marker_is_translated_without_leaking_text() -> None:
    new_content = (
        "import os\n"
        "\n"
        "path = os.path.join(r'E:\\workspace\\api-relay', 'api-relay.html')\n"
        "html_content = []\n"
        "html_content.append('<!DOCTYPE html>')\n"
        "html_content.append('<html lang=\"zh-CN\">')\n"
        "html_content.append('<title>API \\\\u4e2d\\\\u8f6c\\\\u7ad9</title>')\n"
        "open(path, 'w', encoding='utf-8').write('\\n'.join(html_content))\n"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-apply-patch-file-path-new-content",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "Let me write a generator script and then execute it."
                                "<tool_call>\n"
                                "<function=apply_patch>\n"
                                "<parameter=file_path>E:\\workspace\\api-relay\\gen.py</parameter>\n"
                                f"<parameter=new_content>{new_content}"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create an API relay HTML page.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "<tool_call>" not in serialized
    assert "Let me write a generator script" not in serialized
    item = payload["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "shell_command"
    arguments = json.loads(item["arguments"])
    assert "gen.py" in arguments["command"]
    assert "WriteAllText" in arguments["command"]
    assert "FromBase64String" in arguments["command"]


def test_unparseable_text_tool_call_marker_fails_fast_without_leaking_marker() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-unparseable-tool-marker",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "I will do it.<tool_call>\n"
                                "<function=apply_patch>\n"
                                "<parameter=file_path>E:\\workspace\\missing-content.py</parameter>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create a file.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "<tool_call>" not in serialized
    item = payload["output"][0]
    assert item["type"] == "message"
    assert "could not parse malformed tool call" in payload["output_text"]
    assert "function_call" not in serialized


def test_prompt_write_file_marker_is_translated_to_shell_write() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-write-file-marker",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                '<tool_call>{"name":"write_file","arguments":'
                                '{"path":"E:\\\\workspace\\\\api-relay.html",'
                                '"content":"<!doctype html>OK"}}</tool_call>'
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create an HTML file.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "<tool_call>" not in serialized
    item = payload["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "shell_command"
    arguments = json.loads(item["arguments"])
    assert "api-relay.html" in arguments["command"]
    assert "WriteAllText" in arguments["command"]
    assert "FromBase64String" in arguments["command"]


def test_large_prompt_write_file_marker_spools_payload_to_short_copy_command(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(translator_module, "TOOL_PAYLOAD_DIR", tmp_path)
    large_content = "<!doctype html>\n" + ("A" * 40_000)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-large-write-file-marker",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                '<tool_call>{"name":"write_file","arguments":'
                                + json.dumps(
                                    {
                                        "path": "E:\\workspace\\large-kanban.html",
                                        "content": large_content,
                                    },
                                    ensure_ascii=False,
                                )
                                + "}</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create a large HTML file.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    item = payload["output"][0]
    assert item["type"] == "function_call"
    arguments = json.loads(item["arguments"])
    command = arguments["command"]
    assert len(command) < 2_000
    assert "Copy-Item" in command
    assert "FromBase64String" not in command
    assert "large-kanban.html" in command
    payload_files = list(tmp_path.glob("write-*.txt"))
    assert len(payload_files) == 1
    assert payload_files[0].read_text(encoding="utf-8") == large_content


def test_large_write_file_with_chinese_paths_uses_ascii_safe_powershell(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(translator_module, "TOOL_PAYLOAD_DIR", tmp_path / "负载")
    large_content = "<!doctype html>\n" + ("消息" * 12_000)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-large-chinese-write-file",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                '<tool_call>{"name":"write_file","arguments":'
                                + json.dumps(
                                    {
                                        "path": "E:\\个人文件\\AI\\test2\\消息同步看板.html",
                                        "content": large_content,
                                    },
                                    ensure_ascii=False,
                                )
                                + "}</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create a large HTML file.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    item = response.json()["output"][0]
    command = json.loads(item["arguments"])["command"]
    assert isinstance(command, list)
    assert command[:2] == ["powershell.exe", "-NoProfile"]
    script = command[-1]
    script.encode("ascii")
    assert "Copy-Item" in script
    assert "个人文件" not in script
    assert "消息同步看板" not in script
    assert "负载" not in script
    assert "FromBase64String" in script
    payload_files = list((tmp_path / "负载").glob("write-*.txt"))
    assert len(payload_files) == 1
    assert payload_files[0].read_text(encoding="utf-8") == large_content


def test_powershell_here_string_write_is_rewritten_to_spooled_shell_array(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(translator_module, "TOOL_PAYLOAD_DIR", tmp_path)
    html = "<!DOCTYPE html>\n<html><body>" + ("消息" * 3_000) + "</body></html>\n"
    powershell_script = (
        "$content = @'"
        + html
        + "'@\n"
        "$path = 'E:\\workspace\\message-board.html'\n"
        "$utf8 = New-Object System.Text.UTF8Encoding($true)\n"
        "[System.IO.File]::WriteAllText($path, $content.Replace(\"`r`n\",\"`n\"), $utf8)\n"
        "Write-Output $path"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-powershell-here-string-write",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_write_html",
                                    "type": "function",
                                    "function": {
                                        "name": "shell",
                                        "arguments": json.dumps(
                                            {"command": ["powershell.exe", "-Command", powershell_script]},
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create a large HTML file.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    item = response.json()["output"][0]
    arguments = json.loads(item["arguments"])
    command = arguments["command"]
    assert isinstance(command, list)
    assert command[:2] == ["powershell.exe", "-NoProfile"]
    assert "Copy-Item" in command[-1]
    assert "message-board.html" in command[-1]
    assert "<!DOCTYPE html>" not in command[-1]
    payload_files = list(tmp_path.glob("write-*.txt"))
    assert len(payload_files) == 1
    assert payload_files[0].read_text(encoding="utf-8") == html


def test_powershell_nested_python_dash_c_is_rewritten_for_shell_array() -> None:
    nested_script = (
        "$env:PYTHONIOENCODING='utf-8'; python -c \"\n"
        "import pathlib\n"
        "p = pathlib.Path(r'E:\\\\workspace\\\\wcf_monitor.html')\n"
        "html = '''<!DOCTYPE html><html><body><button onclick=\\\"clearMsgs()\\\">清空</button></body></html>'''\n"
        "p.write_text(html, encoding='utf-8-sig')\n"
        "print('wrote', p.stat().st_size)\n"
        "\""
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-nested-python-c",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_nested_python",
                                    "type": "function",
                                    "function": {
                                        "name": "shell",
                                        "arguments": json.dumps(
                                            {"command": ["powershell.exe", "-Command", nested_script]},
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create a monitor page.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    arguments = json.loads(response.json()["output"][0]["arguments"])
    command = arguments["command"]
    assert isinstance(command, list)
    assert command[:2] == ["powershell.exe", "-NoProfile"]
    assert "script-" in command[-1]
    assert ".py" in command[-1]
    assert "Test-Path" in command[-1]
    assert "FromBase64String" in command[-1]
    assert "Remove-Item" not in command[-1]
    assert "python -c" not in command[-1]
    assert "<!DOCTYPE html>" not in command[-1]


def test_small_write_file_with_chinese_target_uses_ascii_safe_path() -> None:
    content = "<!doctype html><title>消息</title>"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-small-chinese-write-file",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                '<tool_call>{"name":"write_file","arguments":'
                                + json.dumps(
                                    {
                                        "path": "E:\\个人文件\\AI\\test2\\消息.html",
                                        "content": content,
                                    },
                                    ensure_ascii=False,
                                )
                                + "}</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create an HTML file.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    command = json.loads(response.json()["output"][0]["arguments"])["command"]
    command.encode("ascii")
    assert "WriteAllText" in command
    assert "FromBase64String" in command
    assert "个人文件" not in command
    assert "消息.html" not in command


def test_duplicate_write_file_markers_to_same_path_are_deduplicated() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-duplicate-write-file-marker",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                '<tool_call>{"name":"write_file","arguments":'
                                '{"path":"E:\\\\workspace\\\\kanban.html","content":"first"}}</tool_call>'
                                '<tool_call>{"name":"write_file","arguments":'
                                '{"path":"E:\\\\workspace\\\\kanban.html","content":"second"}}</tool_call>'
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create an HTML file.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    function_calls = [item for item in response.json()["output"] if item["type"] == "function_call"]
    assert len(function_calls) == 1
    arguments = json.loads(function_calls[0]["arguments"])
    assert "kanban.html" in arguments["command"]
    assert "second" not in arguments["command"]


def test_jsonish_write_file_marker_with_raw_html_quotes_is_converted() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-jsonish-write-file-marker",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                '<tool_call>{"name":"write_file","arguments":'
                                '{"path":"E:\\\\workspace\\\\kanban.html",'
                                '"content":"<!doctype html>\\n<html lang="zh-CN">\\n'
                                '<meta charset="UTF-8"><title>轻量看板</title>"}}</tool_call>'
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create an HTML file.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["output_text"] == ""
    item = payload["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "shell_command"
    arguments = json.loads(item["arguments"])
    assert "kanban.html" in arguments["command"]
    assert "WriteAllText" in arguments["command"]


def test_malformed_tool_call_with_html_body_is_synthesized_as_write_file() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-malformed-html-write-file-marker",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "Let me write it.<tool_call>{"
                                '"name":"write_file","arguments":{'
                                '"content":"<!DOCTYPE html>\\n<html lang=\\"zh-CN\\">\\n'
                                '<head><meta charset=\\"UTF-8\\"><title>消息同步看板</title></head>\\n'
                                '<body><h1>消息同步看板</h1></body>\\n</html>"'
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "帮我用一个html设计一个轻量的消息同步看板",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "could not parse malformed" not in payload["output_text"]
    item = payload["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "shell_command"
    arguments = json.loads(item["arguments"])
    assert "message-sync-board.html" in arguments["command"]
    assert "WriteAllText" in arguments["command"]


def test_get_content_raw_generated_file_is_rewritten_to_bounded_read() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-bounded-read",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                '<tool_call>{"name":"shell_command","arguments":'
                                + json.dumps(
                                    {
                                        "command": [
                                            "powershell.exe",
                                            "-NoProfile",
                                            "-Command",
                                            "Get-Content -LiteralPath 'E:\\workspace\\kanban.html' -Raw",
                                        ]
                                    }
                                )
                                + "}</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Verify the HTML file.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    item = response.json()["output"][0]
    arguments = json.loads(item["arguments"])
    script = arguments["command"][-1]
    assert "-TotalCount 80" in script
    assert "-Raw" not in script
    assert "truncated full-file read" in script


def test_previous_response_history_compacts_oversized_tool_arguments() -> None:
    requests: list[dict[str, Any]] = []
    oversized_command = "A" * 12_000
    responses = iter(
        [
            {
                "id": "chatcmpl-big-tool-call",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_big",
                                    "type": "function",
                                    "function": {
                                        "name": "shell_command",
                                        "arguments": json.dumps({"command": oversized_command}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            {
                "id": "chatcmpl-after-big-tool-call",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=next(responses))

    client = _make_client(handler)

    first = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create a page.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    second = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "previous_response_id": first.json()["id"],
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": "call_big",
                    "output": "execution error: command line too long",
                }
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    replayed_messages = requests[1]["messages"]
    assistant_tool_message = next(message for message in replayed_messages if message.get("tool_calls"))
    replayed_arguments = assistant_tool_message["tool_calls"][0]["function"]["arguments"]
    assert len(replayed_arguments) < 1_000
    assert "Responses Proxy compacted oversized shell command" in replayed_arguments
    assert "A" * 100 not in replayed_arguments


def test_compacted_shell_history_keeps_array_command_schema_for_native_shell() -> None:
    requests: list[dict[str, Any]] = []
    oversized_command = [
        "powershell.exe",
        "-Command",
        "Write-Output " + json.dumps("A" * 8_000),
    ]
    responses = iter(
        [
            {
                "id": "chatcmpl-big-native-shell",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_big",
                                    "type": "function",
                                    "function": {
                                        "name": "shell",
                                        "arguments": json.dumps({"command": oversized_command}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            {
                "id": "chatcmpl-after-big-native-shell",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=next(responses))

    client = _make_client(handler)
    tools = [
        {
            "type": "function",
            "name": "shell",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                "required": ["command"],
            },
        }
    ]

    first = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Create a page.", "tools": tools},
    )

    second = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "previous_response_id": first.json()["id"],
            "input": [{"type": "function_call_output", "call_id": "call_big", "output": "ok"}],
            "tools": tools,
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assistant_tool_message = next(message for message in requests[1]["messages"] if message.get("tool_calls"))
    replayed_arguments = json.loads(assistant_tool_message["tool_calls"][0]["function"]["arguments"])
    assert isinstance(replayed_arguments["command"], list)
    assert replayed_arguments["command"][:2] == ["powershell.exe", "-NoProfile"]
    assert "Responses Proxy history placeholder" in replayed_arguments["command"][-1]
    assert "A" * 100 not in json.dumps(replayed_arguments, ensure_ascii=False)


def test_tool_parse_error_outputs_are_compacted_before_upstream_replay() -> None:
    requests: list[dict[str, Any]] = []
    noisy_output = (
        "failed to parse function arguments: invalid type: string "
        + json.dumps("powershell.exe -Command " + ("A" * 12_000))
        + ", expected a sequence"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-after-parse-error",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Continuing"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Create file"}]},
                {
                    "type": "function_call",
                    "call_id": "call_bad",
                    "name": "shell",
                    "arguments": {"command": "powershell.exe -Command Write-Output hi"},
                },
                {"type": "function_call_output", "call_id": "call_bad", "output": noisy_output},
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "继续"}]},
            ],
            "tools": [{"type": "function", "name": "shell", "parameters": {"type": "object"}}],
        },
    )

    assert response.status_code == 200
    tool_message = next(message for message in requests[0]["messages"] if message["role"] == "tool")
    assert len(tool_message["content"]) < 1_800
    assert "Responses Proxy compacted oversized tool output" in tool_message["content"]
    assert "A" * 100 not in tool_message["content"]


def test_shell_apply_patch_command_is_rewritten_to_shell_write() -> None:
    patch_text = (
        "*** Begin Patch\n"
        "*** Add File: E:\\workspace\\page.html\n"
        "+<!doctype html>\n"
        "+ok\n"
        "*** End Patch"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-shell-apply-patch-command",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                '<tool_call>{"name":"shell_command","arguments":'
                                + json.dumps({"command": ["apply_patch", patch_text]}, ensure_ascii=False)
                                + "}</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create an HTML file.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    item = payload["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "shell_command"
    arguments = json.loads(item["arguments"])
    assert "page.html" in arguments["command"]
    assert "WriteAllText" in arguments["command"]
    assert "FromBase64String" in arguments["command"]
    assert "apply_patch" not in arguments["command"]


def test_unclosed_tag_named_shell_marker_is_converted_without_leaking_text() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-unclosed-tag-shell-marker",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "I will inspect it.<tool_call>\n"
                                "<shell>\n"
                                "<command>Get-ChildItem -Force"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "List files.",
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "<tool_call>" not in json.dumps(payload, ensure_ascii=False)
    item = payload["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "shell"
    assert json.loads(item["arguments"]) == {"command": "Get-ChildItem -Force"}


def test_streaming_text_tool_call_marker_is_buffered_and_converted_without_leaking_marker() -> None:
    upstream_events = [
        {
            "id": "chatcmpl-stream-text-tool",
            "object": "chat.completion.chunk",
            "created": 1_714_444_444,
            "model": "deepseek-chat",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": '<tool_call>{"name":"shell_command",'},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-stream-text-tool",
            "object": "chat.completion.chunk",
            "created": 1_714_444_444,
            "model": "deepseek-chat",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": '"arguments":{"command":"Get-ChildItem"}}</tool_call>'},
                    "finish_reason": "stop",
                }
            ],
        },
    ]
    chunks = [f"data: {json.dumps(event)}\n\n" for event in upstream_events]
    chunks.append("data: [DONE]\n\n")

    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Iterator[bytes]:
            for chunk in chunks:
                yield chunk.encode("utf-8")

        async def aclose(self) -> None:
            return None

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncStream(),
        )

    client = _make_client(handler)

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "List files",
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    ) as response:
        body = response.read().decode("utf-8")
        events = _parse_sse_events(body)

    assert response.status_code == 200
    assert "<tool_call>" not in body
    assert "response.output_text.delta" not in body
    added = next(event[1] for event in events if event[0] == "response.output_item.added")
    done = next(event[1] for event in events if event[0] == "response.output_item.done")
    assert added["item"]["type"] == "function_call"
    assert done["item"]["name"] == "shell_command"
    assert json.loads(done["item"]["arguments"]) == {"command": "Get-ChildItem"}


def test_streaming_xml_style_text_tool_call_marker_is_buffered_and_converted() -> None:
    upstream_events = [
        {
            "id": "chatcmpl-stream-xml-tool",
            "object": "chat.completion.chunk",
            "created": 1_714_444_444,
            "model": "mimo-v2.5-pro",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": "I will inspect it.\n<tool_call>\n<function=shell>\n",
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-stream-xml-tool",
            "object": "chat.completion.chunk",
            "created": 1_714_444_444,
            "model": "mimo-v2.5-pro",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "<parameter=command>ls -la</parameter>\n</function>\n</tool_call>"},
                    "finish_reason": "stop",
                }
            ],
        },
    ]
    chunks = [f"data: {json.dumps(event)}\n\n" for event in upstream_events]
    chunks.append("data: [DONE]\n\n")

    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Iterator[bytes]:
            for chunk in chunks:
                yield chunk.encode("utf-8")

        async def aclose(self) -> None:
            return None

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncStream(),
        )

    client = _make_client(handler)

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "List files",
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    ) as response:
        body = response.read().decode("utf-8")
        events = _parse_sse_events(body)

    assert response.status_code == 200
    assert "<tool_call>" not in body
    assert "response.output_text.delta" not in body
    done = next(event[1] for event in events if event[0] == "response.output_item.done")
    assert done["item"]["name"] == "shell"
    assert json.loads(done["item"]["arguments"]) == {"command": "ls -la"}


def test_streaming_response_is_emitted_as_sse_events() -> None:
    chunks = [
        'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1714444444,"model":"deepseek-chat","choices":[{"index":0,"delta":{"role":"assistant","content":"Hel"},"finish_reason":null}]}\n\n',
        'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1714444444,"model":"deepseek-chat","choices":[{"index":0,"delta":{"content":"lo"},"finish_reason":"stop"}]}\n\n',
        "data: [DONE]\n\n",
    ]

    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Iterator[bytes]:
            for chunk in chunks:
                yield chunk.encode("utf-8")

        async def aclose(self) -> None:
            return None

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncStream(),
        )

    client = _make_client(handler)

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Say hello", "stream": True},
    ) as response:
        body = response.read().decode("utf-8")
        events = _parse_sse_events(body)

    assert response.status_code == 200
    assert "event: response.created" in body
    assert "event: response.output_text.delta" in body
    assert '"delta": "Hel"' in body
    assert '"delta": "lo"' in body
    assert "event: response.completed" in body
    assert "[DONE]" not in body
    assert events[0][0] == "response.created"
    assert [event[1]["sequence_number"] for event in events if isinstance(event[1], dict)] == list(
        range(len([event for event in events if isinstance(event[1], dict)]))
    )
    assert events[0][1]["response"]["parallel_tool_calls"] is True
    assert events[0][1]["response"]["text"] == {"format": {"type": "text"}}
    message_added = next(event[1] for event in events if event[0] == "response.output_item.added")
    message_id = message_added["item"]["id"]
    assert message_added["item"]["phase"] == "final_answer"
    content_part_added = next(event[1] for event in events if event[0] == "response.content_part.added")
    assert content_part_added["item_id"] == message_id
    assert content_part_added["part"]["logprobs"] == []
    first_delta = next(event[1] for event in events if event[0] == "response.output_text.delta")
    assert first_delta["item_id"] == message_id
    assert first_delta["logprobs"] == []
    text_done = next(event[1] for event in events if event[0] == "response.output_text.done")
    assert text_done["item_id"] == message_id
    assert text_done["logprobs"] == []
    content_part_done = next(event[1] for event in events if event[0] == "response.content_part.done")
    assert content_part_done["item_id"] == message_id
    assert content_part_done["part"]["logprobs"] == []
    item_done = next(event[1] for event in events if event[0] == "response.output_item.done")
    assert item_done["item"]["id"] == message_id
    assert events[-1][0] == "response.completed"
    assert events[-1][1]["response"]["status"] == "completed"
    assert events[-1][1]["response"]["completed_at"] >= events[-1][1]["response"]["created_at"]
    assert events[-1][1]["response"]["output"][0]["id"] == message_id


def test_streaming_emits_keepalive_while_waiting_for_idle_upstream(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "STREAM_KEEPALIVE_SECONDS", 0.01)
    chunks = [
        'data: {"id":"chatcmpl-idle","object":"chat.completion.chunk","created":1714444444,"model":"mimo-v2.5-pro","choices":[{"index":0,"delta":{"role":"assistant","content":"Done"},"finish_reason":"stop"}]}\n\n',
        "data: [DONE]\n\n",
    ]

    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Iterator[bytes]:
            import asyncio

            await asyncio.sleep(0.04)
            for chunk in chunks:
                yield chunk.encode("utf-8")

        async def aclose(self) -> None:
            return None

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncStream(),
        )

    client = _make_client(handler)

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Say hello", "stream": True},
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert body.count("event: response.in_progress") >= 2
    assert "event: response.completed" in body


def test_streaming_response_starts_before_slow_upstream_connect(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "STREAM_KEEPALIVE_SECONDS", 0.01)
    chunks = [
        'data: {"id":"chatcmpl-slow-open","object":"chat.completion.chunk","created":1714444444,"model":"mimo-v2.5","choices":[{"index":0,"delta":{"role":"assistant","content":"Done"},"finish_reason":"stop"}]}\n\n',
        "data: [DONE]\n\n",
    ]

    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Iterator[bytes]:
            for chunk in chunks:
                yield chunk.encode("utf-8")

        async def aclose(self) -> None:
            return None

    async def handler(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.15)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncStream(),
        )

    client = _make_client(handler)

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Say hello", "stream": True},
    ) as response:
        byte_iter = response.iter_bytes()
        first_bytes = next(byte_iter)
        body = first_bytes.decode("utf-8") + b"".join(byte_iter).decode("utf-8")

    assert response.status_code == 200
    assert "event: response.created" in body
    assert body.count("event: response.in_progress") >= 2
    assert "event: response.completed" in body


def test_streaming_generator_yields_created_before_opening_upstream(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "STREAM_KEEPALIVE_SECONDS", 0.01)

    class SlowUpstream:
        opened = False

        async def open_stream(self, _: dict[str, Any], __: str):
            self.opened = True
            await asyncio.sleep(0.2)
            raise AssertionError("upstream should not be opened before initial SSE")

    class NoopInflight:
        def register(self, _: str, __: Any) -> None:
            return None

        def unregister(self, _: str) -> None:
            return None

    async def scenario() -> tuple[bytes, float, bool]:
        upstream = SlowUpstream()
        prepared = main_module.PreparedChatRequest(
            upstream_payload={"model": "mimo-v2.5", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            conversation_messages=[],
            input_items=[],
        )
        generator = main_module.stream_response(
            payload={"model": "gpt-5-codex", "input": "hi", "stream": True},
            prepared=prepared,
            response_id="resp_test",
            upstream=upstream,
            upstream_token="token",
            store=object(),
            inflight=NoopInflight(),
            conversation_history=[],
        )
        started_at = time.perf_counter()
        first_event = await generator.__anext__()
        elapsed = time.perf_counter() - started_at
        await generator.aclose()
        return first_event, elapsed, upstream.opened

    first_event, elapsed, opened = asyncio.run(scenario())

    assert b"event: response.created" in first_event
    assert elapsed < 0.05
    assert opened is False


def test_streaming_tool_call_events_preserve_function_call_ids() -> None:
    upstream_events = [
        {
            "id": "chatcmpl-tool-1",
            "object": "chat.completion.chunk",
            "created": 1_714_444_444,
            "model": "deepseek-chat",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_123",
                                "type": "function",
                                "function": {
                                    "name": "shell_command",
                                    "arguments": '{"command":"Get-',
                                },
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-tool-1",
            "object": "chat.completion.chunk",
            "created": 1_714_444_444,
            "model": "deepseek-chat",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {
                                    "arguments": 'ChildItem"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        },
    ]
    chunks = [f"data: {json.dumps(event)}\n\n" for event in upstream_events]
    chunks.append("data: [DONE]\n\n")

    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Iterator[bytes]:
            for chunk in chunks:
                yield chunk.encode("utf-8")

        async def aclose(self) -> None:
            return None

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncStream(),
        )

    client = _make_client(handler)

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "List files",
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    ) as response:
        body = response.read().decode("utf-8")
        events = _parse_sse_events(body)

    assert response.status_code == 200
    payloads = [event[1] for event in events if isinstance(event[1], dict)]
    assert [payload["sequence_number"] for payload in payloads] == list(range(len(payloads)))

    tool_added = next(event[1] for event in events if event[0] == "response.output_item.added")
    tool_id = tool_added["item"]["id"]
    assert tool_added["item"] == {
        "id": tool_id,
        "type": "function_call",
        "status": "in_progress",
        "name": "shell_command",
        "arguments": "",
        "call_id": tool_id,
    }

    argument_deltas = [event[1] for event in events if event[0] == "response.function_call_arguments.delta"]
    assert "".join(delta["delta"] for delta in argument_deltas) == '{"command":"Get-ChildItem"}'
    assert all(delta["item_id"] == tool_id for delta in argument_deltas)

    arguments_done = next(event[1] for event in events if event[0] == "response.function_call_arguments.done")
    assert arguments_done["item_id"] == tool_id
    assert arguments_done["arguments"] == '{"command":"Get-ChildItem"}'

    tool_done = next(event[1] for event in events if event[0] == "response.output_item.done")
    assert tool_done["item"] == {
        "id": tool_id,
        "type": "function_call",
        "status": "completed",
        "name": "shell_command",
        "arguments": '{"command":"Get-ChildItem"}',
        "call_id": tool_id,
    }
    assert events[-1][1]["response"]["output"] == [
        {
            "id": tool_id,
            "type": "function_call",
            "status": "completed",
            "name": "shell_command",
            "arguments": '{"command":"Get-ChildItem"}',
            "call_id": tool_id,
        }
    ]


def test_streaming_native_tool_intent_text_is_retried_as_function_call() -> None:
    requests: list[dict[str, Any]] = []
    first_chunks = [
        'data: {"id":"chatcmpl-intent-stream","object":"chat.completion.chunk","created":1714444444,"model":"mimo-v2.5-pro","choices":[{"index":0,"delta":{"role":"assistant","content":"继续写入完整的消息同步看板 HTML 文件。"},"finish_reason":"stop"}]}\n\n',
        "data: [DONE]\n\n",
    ]
    second_chunks = [
        'data: {"id":"chatcmpl-retry-stream","object":"chat.completion.chunk","created":1714444445,"model":"mimo-v2.5-pro","choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_retry_stream","type":"function","function":{"name":"shell","arguments":"{\\"command\\":[\\"powershell.exe\\",\\"-NoProfile\\",\\"-Command\\",\\"Write-Output ok\\"]}"}}]},"finish_reason":"tool_calls"}]}\n\n',
        "data: [DONE]\n\n",
    ]
    stream_chunks = iter([first_chunks, second_chunks])

    class AsyncStream(httpx.AsyncByteStream):
        def __init__(self, chunks: list[str]) -> None:
            self._chunks = chunks

        async def __aiter__(self) -> Iterator[bytes]:
            for chunk in self._chunks:
                yield chunk.encode("utf-8")

        async def aclose(self) -> None:
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncStream(next(stream_chunks)),
        )

    client = _make_client(
        handler,
        settings_overrides={
            "upstream_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "upstream_model": "mimo-v2.5-pro",
        },
    )

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "继续",
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                        "required": ["command"],
                    },
                }
            ],
        },
    ) as response:
        body = response.read().decode("utf-8")
        events = _parse_sse_events(body)

    assert response.status_code == 200
    assert len(requests) == 2
    assert "did not call a tool" in requests[1]["messages"][-1]["content"]
    assert "继续写入完整" not in body
    function_done = next(
        event[1] for event in events
        if event[0] == "response.output_item.done" and event[1]["item"]["type"] == "function_call"
    )
    assert function_done["item"]["name"] == "shell"
    assert events[-1][0] == "response.completed"
    assert events[-1][1]["response"]["output"][0]["type"] == "function_call"


def test_streaming_usage_only_tail_chunk_does_not_emit_response_failed() -> None:
    upstream_events = [
        {
            "id": "chatcmpl-usage-tail-1",
            "object": "chat.completion.chunk",
            "created": 1_714_444_444,
            "model": "mimo-v2.5-pro",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "Hello"},
                    "finish_reason": None,
                }
            ],
            "usage": None,
        },
        {
            "id": "chatcmpl-usage-tail-1",
            "object": "chat.completion.chunk",
            "created": 1_714_444_445,
            "model": "mimo-v2.5-pro",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": " there"},
                    "finish_reason": "stop",
                }
            ],
            "usage": None,
        },
        {
            "id": "chatcmpl-usage-tail-1",
            "object": "chat.completion.chunk",
            "created": 1_714_444_446,
            "model": "mimo-v2.5-pro",
            "choices": [],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 5,
                "total_tokens": 16,
            },
        },
    ]
    chunks = [f"data: {json.dumps(event)}\n\n" for event in upstream_events]
    chunks.append("data: [DONE]\n\n")

    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Iterator[bytes]:
            for chunk in chunks:
                yield chunk.encode("utf-8")

        async def aclose(self) -> None:
            return None

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncStream(),
        )

    client = _make_client(handler)

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Say hello", "stream": True},
    ) as response:
        body = response.read().decode("utf-8")
        events = _parse_sse_events(body)

    assert response.status_code == 200
    assert "response.failed" not in body
    assert events[-1][0] == "response.completed"
    assert events[-1][1]["response"]["output_text"] == "Hello there"
    assert events[-1][1]["response"]["usage"] == {
        "input_tokens": 11,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 5,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 16,
    }


def test_json_response_coerces_stringified_shell_command_array() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-shell-coerce",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_shell_1",
                                    "type": "function",
                                    "function": {
                                        "name": "shell",
                                        "arguments": '{"command":"[\\"powershell.exe\\",\\"-Command\\",\\"Write-Output hi\\"]"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 4,
                    "total_tokens": 15,
                },
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "run shell",
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "array",
                                "items": {"type": "string"},
                            }
                        },
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["output"][0]["name"] == "shell"
    assert payload["output"][0]["arguments"] == json.dumps(
        {"command": ["powershell.exe", "-Command", "Write-Output hi"]},
        ensure_ascii=False,
    )


def test_json_response_defaults_shell_command_to_array_when_schema_is_loose() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-shell-loose-schema",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_shell_loose",
                                    "type": "function",
                                    "function": {
                                        "name": "shell",
                                        "arguments": '{"command":"[\\"powershell.exe\\",\\"-Command\\",\\"Write-Output hi\\"]"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "run shell",
            "tools": [{"type": "function", "name": "shell", "parameters": {"type": "object"}}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["output"][0]["name"] == "shell"
    assert json.loads(payload["output"][0]["arguments"]) == {
        "command": ["powershell.exe", "-Command", "Write-Output hi"]
    }


def test_json_response_coerces_powershell_command_string_to_shell_array() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-shell-powershell-string",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_shell_string",
                                    "type": "function",
                                    "function": {
                                        "name": "shell",
                                        "arguments": '{"command":"powershell.exe -Command \\"Get-ChildItem E:\\\\\\\\个人文件\\\\\\\\AI\\\\\\\\test2\\""}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "run shell",
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    arguments = json.loads(payload["output"][0]["arguments"])
    assert arguments["command"] == [
        "powershell.exe",
        "-Command",
        "Get-ChildItem E:\\个人文件\\AI\\test2",
    ]


def test_streaming_apply_patch_new_file_is_translated_to_shell_write() -> None:
    upstream_events = [
        {
            "id": "chatcmpl-apply-patch-1",
            "object": "chat.completion.chunk",
            "created": 1_714_444_444,
            "model": "deepseek-chat",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_patch_1",
                                "type": "function",
                                "function": {
                                    "name": "apply_patch",
                                    "arguments": '{"json":{"mode":"new_file","path":"E:\\\\workspace\\\\particles.html","content":"<html>ok</html>"}}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
    ]
    chunks = [f"data: {json.dumps(event)}\n\n" for event in upstream_events]
    chunks.append("data: [DONE]\n\n")

    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Iterator[bytes]:
            for chunk in chunks:
                yield chunk.encode("utf-8")

        async def aclose(self) -> None:
            return None

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncStream(),
        )

    client = _make_client(handler)

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Create file",
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "array",
                                "items": {"type": "string"},
                            }
                        },
                        "required": ["command"],
                    },
                }
            ],
        },
    ) as response:
        body = response.read().decode("utf-8")
        events = _parse_sse_events(body)

    assert response.status_code == 200
    item_done = next(event[1] for event in events if event[0] == "response.output_item.done")
    assert item_done["item"]["name"] == "shell"
    arguments = json.loads(item_done["item"]["arguments"])
    assert arguments["command"][0:2] == ["powershell.exe", "-Command"]
    assert "particles.html" in arguments["command"][2]
    assert "WriteAllText" in arguments["command"][2]
    final_response = events[-1][1]["response"]
    assert final_response["output"][0]["name"] == "shell"


def test_streaming_upstream_http_error_is_returned_as_response_failed_event() -> None:
    error_body = json.dumps({"error": {"message": "Upstream rejected the request.", "type": "invalid_request_error"}})

    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Iterator[bytes]:
            yield error_body.encode("utf-8")

        async def aclose(self) -> None:
            return None

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            headers={"content-type": "application/json"},
            stream=AsyncStream(),
        )

    client = _make_client(handler)

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Say hello", "stream": True},
    ) as response:
        body = response.read().decode("utf-8")
        events = _parse_sse_events(body)

    assert response.status_code == 200
    assert "event: response.created" in body
    failed = next(event[1] for event in events if event[0] == "response.failed")
    assert failed["error"] == {
        "message": "Upstream rejected the request.",
        "type": "invalid_request_error",
    }


def test_streaming_multiline_upstream_sse_data_is_decoded() -> None:
    chunks = [
        'event: completion.chunk\n',
        'data: {"id":"chatcmpl-multiline","object":"chat.completion.chunk",\n',
        'data: "created":1714444444,"model":"deepseek-chat","choices":[{"index":0,"delta":{"role":"assistant","content":"Hel"},"finish_reason":null}]}\n\n',
        'data: {"id":"chatcmpl-multiline","object":"chat.completion.chunk","created":1714444444,"model":"deepseek-chat","choices":[{"index":0,"delta":{"content":"lo"},"finish_reason":"stop"}]}\n\n',
        "data: [DONE]\n\n",
    ]

    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Iterator[bytes]:
            for chunk in chunks:
                yield chunk.encode("utf-8")

        async def aclose(self) -> None:
            return None

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncStream(),
        )

    client = _make_client(handler)

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Say hello", "stream": True},
    ) as response:
        body = response.read().decode("utf-8")
        events = _parse_sse_events(body)

    assert response.status_code == 200
    assert events[-1][0] == "response.completed"
    assert events[-1][1]["response"]["output_text"] == "Hello"


def test_streaming_upstream_error_event_is_failed_and_stored() -> None:
    chunks = [
        "event: error\n",
        'data: {"error":{"message":"Upstream stream rate limited.","type":"rate_limit_error"}}\n\n',
    ]

    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Iterator[bytes]:
            for chunk in chunks:
                yield chunk.encode("utf-8")

        async def aclose(self) -> None:
            return None

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncStream(),
        )

    client = _make_client(handler)

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Say hello", "stream": True, "store": True},
    ) as response:
        body = response.read().decode("utf-8")
        events = _parse_sse_events(body)

    assert response.status_code == 200
    created = next(event[1]["response"] for event in events if event[0] == "response.created")
    failed = next(event[1] for event in events if event[0] == "response.failed")
    assert failed["error"] == {
        "message": "Upstream stream rate limited.",
        "type": "rate_limit_error",
    }

    stored = client.get(f"/v1/responses/{created['id']}", headers={"Authorization": "Bearer proxy-secret"})
    assert stored.status_code == 200
    assert stored.json()["status"] == "failed"
    assert stored.json()["error"] == failed["error"]


def test_previous_response_id_reuses_conversation_history() -> None:
    requests: list[dict[str, Any]] = []

    responses = iter(
        [
            {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
            {
                "id": "chatcmpl-2",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "I am still here"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 4,
                    "total_tokens": 24,
                },
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=next(responses))

    client = _make_client(handler)

    first = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Hi"},
    )
    first_id = first.json()["id"]

    second = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "previous_response_id": first_id,
            "input": "What did I just say?",
        },
    )

    assert second.status_code == 200
    assert requests[1]["messages"] == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
        {"role": "user", "content": "What did I just say?"},
    ]


def test_previous_response_id_hoists_developer_messages_before_history() -> None:
    requests: list[dict[str, Any]] = []

    responses = iter(
        [
            {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
            {
                "id": "chatcmpl-2",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22},
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=next(responses))

    client = _make_client(handler)

    first = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Hi"},
    )
    second = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "previous_response_id": first.json()["id"],
            "input": [
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "Use concise answers."}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Continue"}],
                },
            ],
        },
    )

    assert second.status_code == 200
    assert requests[1]["messages"] == [
        {"role": "system", "content": "Use concise answers."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
        {"role": "user", "content": "Continue"},
    ]


def test_previous_response_id_deduplicates_resent_tool_call_and_restores_reasoning_content() -> None:
    requests: list[dict[str, Any]] = []

    responses = iter(
        [
            {
                "id": "chatcmpl-tool-1",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "hidden reasoning",
                            "tool_calls": [
                                {
                                    "id": "call_old",
                                    "type": "function",
                                    "function": {
                                        "name": "shell_command",
                                        "arguments": '{"command":"dir"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
            {
                "id": "chatcmpl-tool-2",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24},
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=next(responses))

    client = _make_client(handler)

    first = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "List files",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                    },
                }
            ],
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "previous_response_id": first.json()["id"],
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_new",
                    "name": "shell_command",
                    "arguments": {"command": "dir"},
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_new",
                    "output": "file list",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "continue"}],
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                    },
                }
            ],
        },
    )

    assert second.status_code == 200
    conversation_messages = [message for message in requests[1]["messages"] if message["role"] != "system"]
    assert conversation_messages == [
        {"role": "user", "content": "List files"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "hidden reasoning",
            "tool_calls": [
                {
                    "id": "call_new",
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "arguments": '{"command": "dir"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_new",
            "content": "file list",
        },
        {"role": "user", "content": "continue"},
    ]


def test_mimo_tool_call_history_gets_reasoning_content_when_upstream_omits_it() -> None:
    requests: list[dict[str, Any]] = []

    responses = iter(
        [
            {
                "id": "chatcmpl-mimo-tool-1",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_old",
                                    "type": "function",
                                    "function": {
                                        "name": "shell_command",
                                        "arguments": '{"command":"dir"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
            {
                "id": "chatcmpl-mimo-tool-2",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24},
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=next(responses))

    client = _make_client(
        handler,
        settings_overrides={
            "upstream_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "upstream_model": "mimo-v2.5-pro",
        },
    )

    first = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "List files",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                    },
                }
            ],
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "previous_response_id": first.json()["id"],
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_new",
                    "name": "shell_command",
                    "arguments": {"command": "dir"},
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_new",
                    "output": "file list",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "continue"}],
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                    },
                }
            ],
        },
    )

    assert second.status_code == 200
    replayed_assistant_messages = [
        message for message in requests[1]["messages"] if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    assert replayed_assistant_messages
    assert replayed_assistant_messages[0]["reasoning_content"].startswith("[Responses Proxy")


def test_previous_response_history_compacts_oversized_assistant_text() -> None:
    requests: list[dict[str, Any]] = []
    large_html = "<!doctype html>\n" + ("<section>large generated page</section>\n" * 600)
    responses = iter(
        [
            {
                "id": "chatcmpl-large-assistant-text",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": large_html},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            {
                "id": "chatcmpl-after-large-assistant-text",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=next(responses))

    client = _make_client(
        handler,
        settings_overrides={
            "upstream_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "upstream_model": "mimo-v2.5-pro",
        },
    )

    first = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Create a large HTML page."},
    )
    second = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "previous_response_id": first.json()["id"],
            "input": "Continue.",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    replayed_assistant = next(
        message for message in requests[1]["messages"] if message.get("role") == "assistant"
    )
    assert len(replayed_assistant["content"]) < 2_500
    assert "Responses Proxy compacted oversized assistant content" in replayed_assistant["content"]
    assert "<section>large generated page</section>\n" * 40 not in replayed_assistant["content"]


def test_multiple_function_call_items_are_grouped_for_chat_providers() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-multi-tools",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Run both checks"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_ls",
                    "name": "shell_command",
                    "arguments": {"command": "dir"},
                },
                {
                    "type": "function_call",
                    "call_id": "call_pwd",
                    "name": "shell_command",
                    "arguments": {"command": "Get-Location"},
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_ls",
                    "output": "files",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_pwd",
                    "output": "path",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "continue"}],
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    conversation_messages = [message for message in requests[0]["messages"] if message["role"] != "system"]
    assert conversation_messages == [
        {"role": "user", "content": "Run both checks"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": (
                "[Responses Proxy synthesized reasoning_content because the upstream model returned a tool call "
                "without reasoning_content. This keeps MiMo/DeepSeek thinking-mode tool-call history replayable.]"
            ),
            "tool_calls": [
                {
                    "id": "call_ls",
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "arguments": '{"command": "dir"}',
                    },
                },
                {
                    "id": "call_pwd",
                    "type": "function",
                    "function": {
                        "name": "shell_command",
                        "arguments": '{"command": "Get-Location"}',
                    },
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_ls", "content": "files"},
        {"role": "tool", "tool_call_id": "call_pwd", "content": "path"},
        {"role": "user", "content": "continue"},
    ]


def test_previous_response_id_prunes_dangling_tool_call_when_user_interrupts() -> None:
    requests: list[dict[str, Any]] = []

    responses = iter(
        [
            {
                "id": "chatcmpl-tool-1",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "hidden reasoning",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "shell_command",
                                        "arguments": '{"command":"dir"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
            {
                "id": "chatcmpl-tool-2",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "I can continue."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24},
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=next(responses))

    client = _make_client(handler)

    first = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "List files",
            "tools": [{"type": "function", "name": "shell_command", "parameters": {"type": "object"}}],
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "previous_response_id": first.json()["id"],
            "input": "怎么停了",
            "tools": [{"type": "function", "name": "shell_command", "parameters": {"type": "object"}}],
        },
    )

    assert second.status_code == 200
    conversation_messages = [message for message in requests[1]["messages"] if message["role"] != "system"]
    assert conversation_messages == [
        {"role": "user", "content": "List files"},
        {"role": "user", "content": "怎么停了"},
    ]


def test_response_can_be_retrieved_cancelled_and_deleted() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-state-1",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Stored"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 1,
                    "total_tokens": 4,
                },
            },
        )

    client = _make_client(handler)

    created = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Store this"},
    )
    response_id = created.json()["id"]

    retrieved = client.get(f"/v1/responses/{response_id}", headers={"Authorization": "Bearer proxy-secret"})
    cancelled = client.post(f"/v1/responses/{response_id}/cancel", headers={"Authorization": "Bearer proxy-secret"})
    deleted = client.delete(f"/v1/responses/{response_id}", headers={"Authorization": "Bearer proxy-secret"})
    missing = client.get(f"/v1/responses/{response_id}", headers={"Authorization": "Bearer proxy-secret"})

    assert retrieved.status_code == 200
    assert retrieved.json()["output_text"] == "Stored"
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "completed"
    assert deleted.status_code == 200
    assert deleted.json() == {"id": response_id, "object": "response", "deleted": True}
    assert missing.status_code == 404


def test_unsupported_protocol_fields_are_reported_in_compatibility_metadata() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-protocol-contract",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Contract checked"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Say hello",
            "include": ["reasoning.encrypted_content"],
            "prompt": {"id": "pmpt_123"},
            "service_tier": "auto",
            "max_tool_calls": 1,
            "top_logprobs": 2,
            "metadata": {"owner": "test"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    compatibility = payload["metadata"]["response_proxy"]["compatibility"]
    assert compatibility["ignored_fields"] == [
        "include",
        "max_tool_calls",
        "prompt",
        "service_tier",
        "top_logprobs",
    ]
    assert "unsupported_fields" not in compatibility
    assert "include" not in recorded["json"]
    assert "prompt" not in recorded["json"]
    assert "service_tier" not in payload
    assert payload["metadata"]["owner"] == "test"


def test_strict_protocol_mode_rejects_unsupported_fields() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("Upstream should not be called when strict protocol validation fails.")

    client = _make_client(handler, {"strict_protocol": True})

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "Say hello",
            "include": ["reasoning.encrypted_content"],
        },
    )

    assert response.status_code == 400
    assert "Unsupported Responses API fields" in response.json()["error"]["message"]


def test_input_items_are_persisted_and_listed() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-input-items",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Stored input items"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
            },
        )

    client = _make_client(handler)

    created = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": [
                {"type": "message", "role": "user", "content": "Hi"},
                {"type": "message", "role": "assistant", "content": "Hello"},
                {"type": "message", "role": "user", "content": "Continue"},
            ],
        },
    )
    response_id = created.json()["id"]

    listed = client.get(f"/v1/responses/{response_id}/input_items", headers={"Authorization": "Bearer proxy-secret"})

    assert listed.status_code == 200
    payload = listed.json()
    assert payload["object"] == "list"
    assert [item["role"] for item in payload["data"]] == ["user", "assistant", "user"]
    assert payload["first_id"] == payload["data"][0]["id"]
    assert payload["last_id"] == payload["data"][-1]["id"]
    assert payload["has_more"] is False


def test_response_state_persists_across_app_instances(tmp_path: Any) -> None:
    store_path = tmp_path / "responses.sqlite3"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-persist",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Persisted"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            },
        )

    first_client = _make_client(handler, {"state_store_path": str(store_path)})
    created = first_client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Store this"},
    )
    response_id = created.json()["id"]

    second_client = _make_client(handler, {"state_store_path": str(store_path)})
    retrieved = second_client.get(f"/v1/responses/{response_id}", headers={"Authorization": "Bearer proxy-secret"})

    assert retrieved.status_code == 200
    assert retrieved.json()["output_text"] == "Persisted"


def test_background_response_can_be_cancelled_before_completion() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        import asyncio

        await asyncio.sleep(30)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-background",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Too late"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    client = _make_client(handler)

    created = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Slow task", "background": True},
    )
    response_id = created.json()["id"]
    cancelled = client.post(f"/v1/responses/{response_id}/cancel", headers={"Authorization": "Bearer proxy-secret"})

    assert created.status_code == 200
    assert created.json()["status"] in {"queued", "in_progress"}
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_multimodal_image_input_is_forwarded_as_chat_content_part() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-image-1",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "vision-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Image received"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 2,
                    "total_tokens": 11,
                },
            },
        )

    client = _make_client(handler, {"upstream_supports_image_input": True})

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Describe this image."},
                        {"type": "input_image", "image_url": "https://example.test/cat.png"},
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert recorded["json"]["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image."},
                {"type": "image_url", "image_url": {"url": "https://example.test/cat.png"}},
            ],
        }
    ]


def test_image_input_returns_clear_error_when_upstream_vision_is_disabled() -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500, json={"error": {"message": "should not reach upstream"}})

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Describe this image."},
                        {"type": "input_image", "image_url": "https://example.test/cat.png"},
                    ],
                }
            ],
        },
    )

    assert response.status_code == 400
    assert called is False
    message = response.json()["error"]["message"]
    assert "不支持图片输入" in message
    assert "upstream_supports_image_input" in message


def test_upstream_image_unsupported_error_is_normalized_to_clear_client_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"error": {"message": "No endpoints found that support image input"}},
        )

    client = _make_client(handler, {"upstream_supports_image_input": True})

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Describe this image."},
                        {"type": "input_image", "image_url": "https://example.test/cat.png"},
                    ],
                }
            ],
        },
    )

    assert response.status_code == 400
    message = response.json()["error"]["message"]
    assert "不支持图片输入" in message
    assert "No endpoints found" not in message


def test_inline_text_file_input_is_decoded_into_chat_context() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-inline-file",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Read file"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Summarize the file."},
                        {
                            "type": "input_file",
                            "filename": "notes.txt",
                            "mime_type": "text/plain",
                            "file_data": "VGhpcyBpcyBhIHByb3h5IG5vdGUu",
                        },
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert recorded["json"]["messages"] == [
        {
            "role": "user",
            "content": "Summarize the file.\n[input_file notes.txt]\nThis is a proxy note.",
        }
    ]


def test_reasoning_content_is_returned_as_reasoning_output_item() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-reasoning",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-reasoner",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "reasoning_content": "I checked the constraints.",
                            "content": "Final answer",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 6,
                    "completion_tokens_details": {"reasoning_tokens": 3},
                    "total_tokens": 14,
                },
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Think briefly"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["output"][0]["type"] == "reasoning"
    assert payload["output"][0]["summary"][0]["text"] == "I checked the constraints."
    assert payload["output"][1]["type"] == "message"
    assert payload["usage"]["output_tokens_details"]["reasoning_tokens"] == 3


def test_reasoning_alias_field_is_returned_as_reasoning_output_item() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-reasoning-alias",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "reasoning": "I formed a short plan.",
                            "content": "Final answer",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 14},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Think briefly"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["output"][0]["type"] == "reasoning"
    assert payload["output"][0]["summary"][0]["text"] == "I formed a short plan."
    assert payload["output"][1]["type"] == "message"


def test_streaming_reasoning_content_emits_reasoning_events() -> None:
    upstream_events = [
        {
            "id": "chatcmpl-stream-reasoning",
            "object": "chat.completion.chunk",
            "created": 1_714_444_444,
            "model": "deepseek-reasoner",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "reasoning_content": "I checked "},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-stream-reasoning",
            "object": "chat.completion.chunk",
            "created": 1_714_444_444,
            "model": "deepseek-reasoner",
            "choices": [
                {
                    "index": 0,
                    "delta": {"reasoning_content": "the constraints.", "content": "Final"},
                    "finish_reason": "stop",
                }
            ],
        },
    ]
    chunks = [f"data: {json.dumps(event)}\n\n" for event in upstream_events]
    chunks.append("data: [DONE]\n\n")

    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> Iterator[bytes]:
            for chunk in chunks:
                yield chunk.encode("utf-8")

        async def aclose(self) -> None:
            return None

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncStream(),
        )

    client = _make_client(handler)

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Think briefly", "stream": True},
    ) as response:
        body = response.read().decode("utf-8")
        events = _parse_sse_events(body)

    assert response.status_code == 200
    assert "response.reasoning_summary_text.delta" in body
    assert "response.reasoning_summary_text.done" in body
    reasoning_added = next(
        event[1] for event in events
        if event[0] == "response.output_item.added" and event[1]["item"]["type"] == "reasoning"
    )
    message_added = next(
        event[1] for event in events
        if event[0] == "response.output_item.added" and event[1]["item"]["type"] == "message"
    )
    text_delta = next(event[1] for event in events if event[0] == "response.output_text.delta")
    assert reasoning_added["output_index"] == 0
    assert message_added["output_index"] == 1
    assert text_delta["output_index"] == 1
    final_response = events[-1][1]["response"]
    assert final_response["output"][0]["type"] == "reasoning"
    assert final_response["output"][0]["summary"][0]["text"] == "I checked the constraints."
    assert final_response["output"][1]["type"] == "message"


def test_explanatory_tool_call_text_is_not_replaced_by_malformed_error() -> None:
    explanatory_text = (
        "这个报错的意思是：上游模型没有输出合法的 `<tool_call>{...}</tool_call>` 格式，"
        "所以代理没有执行工具。"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-explain-tool-call",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": explanatory_text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "报错是什么意思，中文回复我",
            "tools": [{"type": "function", "name": "shell_command", "parameters": {"type": "object"}}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["output_text"] == explanatory_text
    assert "could not parse malformed" not in payload["output_text"]


def test_file_search_injects_local_file_matches(tmp_path: Any) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "guide.md").write_text("Alpha proxy supports local file search.\nSecond line.", encoding="utf-8")
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-file-search",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Found it"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 2,
                    "total_tokens": 11,
                },
            },
        )

    client = _make_client(handler, {"file_search_paths": [str(docs_dir)]})

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Alpha proxy", "tools": [{"type": "file_search"}]},
    )

    assert response.status_code == 200
    assert recorded["json"]["messages"][0]["role"] == "system"
    assert "Local file search results" in recorded["json"]["messages"][0]["content"]
    assert "guide.md" in recorded["json"]["messages"][0]["content"]
    payload = response.json()
    assert payload["output"][0]["type"] == "file_search_call"
    assert payload["output"][0]["status"] == "completed"
    assert "guide.md" in payload["output"][0]["results"][0]["filename"]
    message = payload["output"][1]
    assert message["type"] == "message"
    assert message["content"][0]["annotations"][0]["type"] == "file_citation"


def test_web_search_injects_searxng_results() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "search.example":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Proxy docs",
                            "url": "https://example.test/proxy",
                            "content": "Responses proxy web search result.",
                        }
                    ]
                },
            )
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-web-search",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Search context used"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 3,
                    "total_tokens": 12,
                },
            },
        )

    client = _make_client(
        handler,
        {
            "web_search_backend": "searxng",
            "web_search_searxng_url": "https://search.example/search",
        },
    )

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Responses proxy", "tools": [{"type": "web_search"}]},
    )

    assert response.status_code == 200
    assert recorded["json"]["messages"][0]["role"] == "system"
    assert "Local web search results" in recorded["json"]["messages"][0]["content"]
    assert "https://example.test/proxy" in recorded["json"]["messages"][0]["content"]
    payload = response.json()
    assert payload["output"][0]["type"] == "web_search_call"
    assert payload["output"][0]["status"] == "completed"
    assert payload["output"][0]["results"][0]["url"] == "https://example.test/proxy"
    message = payload["output"][1]
    assert message["content"][0]["annotations"][0] == {
        "type": "url_citation",
        "url": "https://example.test/proxy",
        "title": "Proxy docs",
        "start_index": 0,
        "end_index": len("Search context used"),
    }


def test_web_search_uses_last_user_message_as_query() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "search.example":
            recorded["search_query"] = request.url.params.get("q")
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "User query result",
                            "url": "https://example.test/user-query",
                            "content": "Only the final user request should be searched.",
                        }
                    ]
                },
            )
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-web-search-query",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Search context used"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 3,
                    "total_tokens": 12,
                },
            },
        )

    client = _make_client(
        handler,
        {
            "web_search_backend": "searxng",
            "web_search_searxng_url": "https://search.example/search",
        },
    )

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": [
                {
                    "type": "message",
                    "role": "developer",
                    "content": "<permissions instructions>Do not search this internal context.</permissions instructions>",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "What is Responses Proxy?"}],
                },
            ],
            "tools": [{"type": "web_search"}],
        },
    )

    assert response.status_code == 200
    assert recorded["search_query"] == "What is Responses Proxy?"
    assert "<permissions instructions>" not in recorded["json"]["messages"][0]["content"]


def test_web_search_429_degrades_without_failing_response() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "search.example":
            return httpx.Response(429, json={"error": "too many requests"})
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-web-search-rate-limited",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Answered without search"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 3,
                    "total_tokens": 12,
                },
            },
        )

    client = _make_client(
        handler,
        {
            "web_search_backend": "searxng",
            "web_search_searxng_url": "https://search.example/search",
        },
    )

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "Responses proxy", "tools": [{"type": "web_search"}]},
    )

    assert response.status_code == 200
    assert response.json()["output_text"] == "Answered without search"
    assert "Search backend unavailable" in recorded["json"]["messages"][0]["content"]
    assert "HTTP 429" in recorded["json"]["messages"][0]["content"]


def test_web_search_falls_back_to_searxng_html_results() -> None:
    recorded: dict[str, Any] = {}
    search_requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "search.example":
            search_requests.append(
                {
                    "query": request.url.params.get("q"),
                    "format": request.url.params.get("format"),
                    "accept": request.headers.get("accept"),
                    "user_agent": request.headers.get("user-agent"),
                }
            )
            if request.url.params.get("format") == "json":
                return httpx.Response(429, json={"error": "too many requests"})
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="""
                <html>
                  <body>
                    <article class="result">
                      <h3><a href="https://weather.example/xining">西宁天气预报</a></h3>
                      <p class="content">西宁今日多云，气温 8 到 21 摄氏度。</p>
                    </article>
                  </body>
                </html>
                """,
            )
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-web-search-html",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Used HTML fallback"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 3,
                    "total_tokens": 12,
                },
            },
        )

    client = _make_client(
        handler,
        {
            "web_search_backend": "searxng",
            "web_search_searxng_url": "https://search.example/search",
        },
    )

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={"model": "gpt-5-codex", "input": "西宁天气", "tools": [{"type": "web_search"}]},
    )

    assert response.status_code == 200
    assert search_requests[0]["format"] == "json"
    assert search_requests[1]["format"] is None
    assert "Mozilla/5.0" in search_requests[1]["user_agent"]
    assert "text/html" in search_requests[1]["accept"]
    assert "西宁天气预报" in recorded["json"]["messages"][0]["content"]
    assert "https://weather.example/xining" in recorded["json"]["messages"][0]["content"]
    assert "8 到 21 摄氏度" in recorded["json"]["messages"][0]["content"]


def test_computer_call_output_is_accepted_as_context() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-computer-output",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Observed"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 2,
                    "total_tokens": 11,
                },
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": [
                {"type": "message", "role": "user", "content": "Inspect the screen."},
                {
                    "type": "computer_call_output",
                    "call_id": "cu_1",
                    "output": {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                },
            ],
            "tools": [{"type": "computer_use"}],
        },
    )

    assert response.status_code == 200
    assert recorded["json"]["messages"][0]["role"] == "system"
    assert "Local computer use context" in recorded["json"]["messages"][0]["content"]
    assert recorded["json"]["messages"][2] == {
        "role": "user",
        "content": "Computer call output cu_1: image_url=data:image/png;base64,AAAA",
    }
    payload = response.json()
    assert payload["output"][0]["type"] == "computer_call"
    assert payload["output"][0]["status"] == "completed"
    assert payload["output"][0]["received_call_outputs"] == 1
    assert payload["output"][1]["type"] == "message"


def test_prompt_cache_key_reuses_augmented_history_with_reasoning_content() -> None:
    requests: list[dict[str, Any]] = []

    responses = iter(
        [
            {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Hello",
                            "reasoning_content": "hidden reasoning",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
            {
                "id": "chatcmpl-2",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Second turn"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 4,
                    "total_tokens": 24,
                },
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=next(responses))

    client = _make_client(handler)

    first = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "prompt_cache_key": "thread-1",
            "input": "Hi",
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "prompt_cache_key": "thread-1",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Hi"}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Hello"}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "What did I just say?"}],
                },
            ],
        },
    )

    assert second.status_code == 200
    assert requests[1]["messages"] == [
        {"role": "user", "content": "Hi"},
        {
            "role": "assistant",
            "content": "Hello",
            "reasoning_content": "hidden reasoning",
        },
        {"role": "user", "content": "What did I just say?"},
    ]


def test_prompt_cache_key_restores_reasoning_content_when_developer_message_precedes_history() -> None:
    requests: list[dict[str, Any]] = []

    responses = iter(
        [
            {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-reasoner",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Hello",
                            "reasoning_content": "hidden reasoning",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
            {
                "id": "chatcmpl-2",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "deepseek-reasoner",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Weather response"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 4,
                    "total_tokens": 24,
                },
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read().decode("utf-8"))
        requests.append(payload)
        second_request = len(requests) == 2
        if second_request:
            assistant_messages = [message for message in payload["messages"] if message["role"] == "assistant"]
            if not assistant_messages or assistant_messages[0].get("reasoning_content") != "hidden reasoning":
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "The `reasoning_content` in the thinking mode must be passed back to the API.",
                            "type": "invalid_request_error",
                        }
                    },
                )
        return httpx.Response(200, json=next(responses))

    client = _make_client(handler)

    first = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "prompt_cache_key": "thread-1",
            "input": "Hi",
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "prompt_cache_key": "thread-1",
            "input": [
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "Use web search when needed."}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Hi"}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Hello"}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "查一下今天的天气"}],
                },
            ],
        },
    )

    assert second.status_code == 200
    assert requests[1]["messages"] == [
        {"role": "system", "content": "Use web search when needed."},
        {"role": "user", "content": "Hi"},
        {
            "role": "assistant",
            "content": "Hello",
            "reasoning_content": "hidden reasoning",
        },
        {"role": "user", "content": "查一下今天的天气"},
    ]


def test_prompt_cache_key_reuses_history_even_when_store_is_false() -> None:
    requests: list[dict[str, Any]] = []

    responses = iter(
        [
            {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Hello",
                            "reasoning_content": "hidden reasoning",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
            {
                "id": "chatcmpl-2",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Second turn"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 4,
                    "total_tokens": 24,
                },
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=next(responses))

    client = _make_client(handler)

    first = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "store": False,
            "prompt_cache_key": "thread-1",
            "input": "Hi",
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "store": False,
            "prompt_cache_key": "thread-1",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Hi"}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Hello"}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "What did I just say?"}],
                },
            ],
        },
    )

    assert second.status_code == 200
    assert requests[1]["messages"] == [
        {"role": "user", "content": "Hi"},
        {
            "role": "assistant",
            "content": "Hello",
            "reasoning_content": "hidden reasoning",
        },
        {"role": "user", "content": "What did I just say?"},
    ]


def test_prompt_cache_key_preserves_reasoning_content_for_resent_tool_calls() -> None:
    requests: list[dict[str, Any]] = []

    responses = iter(
        [
            {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "hidden reasoning",
                            "tool_calls": [
                                {
                                    "id": "call_old",
                                    "type": "function",
                                    "function": {
                                        "name": "update_plan",
                                        "arguments": '{"step":"old"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
            {
                "id": "chatcmpl-2",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 4,
                    "total_tokens": 24,
                },
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=next(responses))

    client = _make_client(handler)

    first = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "store": False,
            "prompt_cache_key": "thread-1",
            "input": "Build page",
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "store": False,
            "prompt_cache_key": "thread-1",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Build page"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_new",
                    "name": "update_plan",
                    "arguments": {"step": "new"},
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_new",
                    "output": "Plan updated",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "continue"}],
                },
            ],
        },
    )

    assert second.status_code == 200
    assert requests[1]["messages"] == [
        {"role": "user", "content": "Build page"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "hidden reasoning",
            "tool_calls": [
                {
                    "id": "call_new",
                    "type": "function",
                    "function": {
                        "name": "update_plan",
                        "arguments": '{"step": "new"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_new",
            "content": "Plan updated",
        },
        {"role": "user", "content": "continue"},
    ]


def test_prompt_cache_key_matches_tool_call_turns_even_if_assistant_text_differs() -> None:
    requests: list[dict[str, Any]] = []

    responses = iter(
        [
            {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "我来创建页面。",
                            "reasoning_content": "hidden reasoning",
                            "tool_calls": [
                                {
                                    "id": "call_old",
                                    "type": "function",
                                    "function": {
                                        "name": "update_plan",
                                        "arguments": '{"step":"old"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
            {
                "id": "chatcmpl-2",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 4,
                    "total_tokens": 24,
                },
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=next(responses))

    client = _make_client(handler)

    first = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "store": False,
            "prompt_cache_key": "thread-1",
            "input": "Build page",
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "store": False,
            "prompt_cache_key": "thread-1",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Build page"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_new",
                    "name": "update_plan",
                    "arguments": {"step": "new"},
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_new",
                    "output": "Plan updated",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "continue"}],
                },
            ],
        },
    )

    assert second.status_code == 200
    assert requests[1]["messages"] == [
        {"role": "user", "content": "Build page"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "hidden reasoning",
            "tool_calls": [
                {
                    "id": "call_new",
                    "type": "function",
                    "function": {
                        "name": "update_plan",
                        "arguments": '{"step": "new"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_new",
            "content": "Plan updated",
        },
        {"role": "user", "content": "continue"},
    ]


def test_empty_native_tool_turn_is_retried_as_function_call() -> None:
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            {
                "id": "chatcmpl-empty-tool-turn",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": ""},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            },
            {
                "id": "chatcmpl-empty-retry-tool",
                "object": "chat.completion",
                "created": 1_714_444_445,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_empty_retry",
                                    "type": "function",
                                    "function": {
                                        "name": "shell",
                                        "arguments": '{"command":["powershell.exe","-NoProfile","-Command","Write-Output ok"]}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=next(responses))

    client = _make_client(
        handler,
        settings_overrides={
            "upstream_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "upstream_model": "mimo-v2.5-pro",
        },
    )

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "继续完成项目",
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    assert len(requests) == 2
    assert "empty" in requests[1]["messages"][-1]["content"].lower()
    payload = response.json()
    assert payload["output_text"] == ""
    assert payload["output"][0]["type"] == "function_call"
    assert payload["output"][0]["name"] == "shell"


def test_prompt_tool_mode_does_not_forward_tool_choice_without_native_tools() -> None:
    recorded: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["json"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-prompt-tool-choice",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(
        handler,
        settings_overrides={
            "upstream_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "upstream_model": "mimo-v2.5",
            "tool_call_mode": "prompt",
        },
    )

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "列出文件",
            "tool_choice": "required",
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    assert "tools" not in recorded["json"]
    assert "tool_choice" not in recorded["json"]
    assert "fallback marker" in recorded["json"]["messages"][0]["content"]


def test_compacted_history_placeholder_shell_command_is_blocked() -> None:
    placeholder = (
        "[Responses Proxy compacted oversized shell command history placeholder; "
        "original_argument_chars=15000. Do not repeat this placeholder command; "
        "use the following tool output as the execution result. Responses Proxy history placeholder.]"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-placeholder-copy",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_placeholder",
                                    "type": "function",
                                    "function": {
                                        "name": "shell",
                                        "arguments": json.dumps(
                                            {
                                                "command": [
                                                    "powershell.exe",
                                                    "-NoProfile",
                                                    "-Command",
                                                    f"Write-Output '{placeholder}'",
                                                ]
                                            }
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(
        handler,
        settings_overrides={
            "upstream_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "upstream_model": "mimo-v2.5-pro",
        },
    )

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "继续",
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    item = response.json()["output"][0]
    arguments = json.loads(item["arguments"])
    command_text = " ".join(arguments["command"])
    assert "blocked execution of a compacted history placeholder" in command_text
    assert "Write-Output" not in command_text


def test_compacted_long_tool_output_preserves_tail_error() -> None:
    prefix = "line\n" * 900
    tail_error = "FAILED tests/test_app.py::test_page - AssertionError: generated file missing"
    output = translator_module.compact_tool_output_for_upstream(prefix + tail_error)

    assert "compacted middle of tool output" in output
    assert "line\nline" in output
    assert tail_error in output


def test_stream_empty_native_tool_turn_is_retried_as_function_call() -> None:
    requests: list[dict[str, Any]] = []
    upstream_event_batches = iter(
        [
            [
                {
                    "id": "chatcmpl-stream-empty",
                    "object": "chat.completion.chunk",
                    "created": 1_714_444_444,
                    "model": "mimo-v2.5-pro",
                    "choices": [
                        {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None},
                    ],
                },
                {
                    "id": "chatcmpl-stream-empty",
                    "object": "chat.completion.chunk",
                    "created": 1_714_444_444,
                    "model": "mimo-v2.5-pro",
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"},
                    ],
                },
            ],
            [
                {
                    "id": "chatcmpl-stream-tool",
                    "object": "chat.completion.chunk",
                    "created": 1_714_444_445,
                    "model": "mimo-v2.5-pro",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_stream_retry",
                                        "type": "function",
                                        "function": {
                                            "name": "shell",
                                            "arguments": '{"command":["powershell.exe","-NoProfile","-Command","Write-Output ok"]}',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            ],
        ]
    )

    class AsyncStream(httpx.AsyncByteStream):
        def __init__(self, events: list[dict[str, Any]]) -> None:
            self._chunks = [f"data: {json.dumps(event)}\n\n".encode("utf-8") for event in events]
            self._chunks.append(b"data: [DONE]\n\n")

        async def __aiter__(self) -> Iterator[bytes]:
            for chunk in self._chunks:
                yield chunk

        async def aclose(self) -> None:
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncStream(next(upstream_event_batches)),
        )

    client = _make_client(
        handler,
        settings_overrides={
            "upstream_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "upstream_model": "mimo-v2.5-pro",
        },
    )

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "继续完成项目",
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "name": "shell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                        "required": ["command"],
                    },
                }
            ],
        },
    ) as response:
        body = response.read().decode("utf-8")
        events = _parse_sse_events(body)

    assert response.status_code == 200
    assert len(requests) == 2
    assert "empty" in requests[1]["messages"][-1]["content"].lower()
    assert "response.function_call_arguments.done" in body
    final_response = events[-1][1]["response"]
    assert final_response["output_text"] == ""
    assert final_response["output"][0]["type"] == "function_call"
    assert final_response["output"][0]["name"] == "shell"


def test_unclosed_xml_apply_patch_parameters_are_parsed_as_write_file() -> None:
    tool_text = (
        "<tool_call>\n"
        "<function=apply_patch>\n"
        "<parameter=file_path>E:\\个人文件\\AI\\test\\api-relay.html\n"
        "<parameter=new_content><!DOCTYPE html>\n"
        "<html lang=\"zh-CN\"><body>API 中转站</body></html>\n"
        "</function>\n"
        "</tool_call>"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-unclosed-xml-params",
                "object": "chat.completion",
                "created": 1_714_444_444,
                "model": "mimo-v2.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": tool_text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_client(handler)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer proxy-secret"},
        json={
            "model": "gpt-5-codex",
            "input": "创建 HTML 页面",
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "<tool_call>" not in json.dumps(payload, ensure_ascii=False)
    item = payload["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "shell_command"
    arguments = json.loads(item["arguments"])
    command = arguments["command"]
    decoded_payloads = [
        base64.b64decode(value).decode("utf-8")
        for value in re.findall(r"FromBase64String\('([^']+)'\)", command)
    ]
    assert any("api-relay.html" in value for value in decoded_payloads)
    assert any("API 中转站" in value for value in decoded_payloads)
    assert "FromBase64String" in command or "Copy-Item" in command


def _parse_sse_events(body: str) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    for chunk in body.strip().split("\n\n"):
        if not chunk.strip():
            continue
        event_name = ""
        data_value = ""
        for line in chunk.splitlines():
            if line.startswith("event: "):
                event_name = line[len("event: ") :]
            elif line.startswith("data: "):
                data_value = line[len("data: ") :]
        if not event_name:
            continue
        parsed_data: Any = data_value
        if data_value.startswith("{"):
            parsed_data = json.loads(data_value)
        events.append((event_name, parsed_data))
    return events
