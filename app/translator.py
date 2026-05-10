from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import base64
import json
import time
import uuid
from typing import Any

from app.config import Settings

IGNORED_HOSTED_TOOL_TYPES = {
    "web_search",
    "web_search_preview",
    "file_search",
    "computer_use",
    "computer_use_preview",
    "code_interpreter",
    "image_generation",
}


class UnsupportedFeatureError(ValueError):
    """Raised when a Responses API feature cannot be mapped to chat/completions."""


@dataclass
class PreparedChatRequest:
    upstream_payload: dict[str, Any]
    conversation_messages: list[dict[str, Any]]


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def build_error(message: str, error_type: str = "invalid_request_error") -> dict[str, Any]:
    return {"error": {"message": message, "type": error_type}}


def prepare_chat_request(
    payload: dict[str, Any],
    settings: Settings,
    conversation_history: list[dict[str, Any]] | None = None,
    hosted_tool_messages: list[dict[str, Any]] | None = None,
) -> PreparedChatRequest:
    history = deepcopy(conversation_history or [])
    instructions = payload.get("instructions")
    input_messages = convert_input_to_messages(payload.get("input"))
    if payload.get("previous_response_id"):
        conversation_messages = history + deepcopy(input_messages)
    else:
        conversation_messages = merge_conversation_history(history, input_messages)

    messages: list[dict[str, Any]] = []
    if instructions:
        messages.append({"role": "system", "content": extract_text(instructions, source="instructions")})
    messages.extend(deepcopy(hosted_tool_messages or []))
    messages.extend(deepcopy(conversation_messages))

    if not messages:
        raise UnsupportedFeatureError(
            "The proxy needs a text `input` or a valid `previous_response_id` conversation history."
        )

    upstream_payload: dict[str, Any] = {
        "model": settings.upstream_model or payload.get("model") or "deepseek-chat",
        "messages": messages,
        "stream": bool(payload.get("stream")),
    }

    for field_name in (
        "temperature",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "stop",
        "user",
    ):
        if field_name in payload:
            upstream_payload[field_name] = payload[field_name]

    max_output_tokens = payload.get("max_output_tokens")
    if max_output_tokens is not None:
        upstream_payload["max_tokens"] = max_output_tokens

    tools = payload.get("tools")
    if tools:
        compatible_tools = flatten_tools(tools)
        if compatible_tools:
            upstream_payload["tools"] = compatible_tools

    tool_choice = payload.get("tool_choice")
    if tool_choice is not None and upstream_payload.get("tools"):
        converted_tool_choice = convert_tool_choice(tool_choice)
        if converted_tool_choice is not None:
            upstream_payload["tool_choice"] = converted_tool_choice

    response_format = convert_response_format(payload)
    if response_format is not None:
        upstream_payload["response_format"] = response_format

    return PreparedChatRequest(
        upstream_payload=upstream_payload,
        conversation_messages=conversation_messages,
    )


def convert_input_to_messages(input_value: Any) -> list[dict[str, Any]]:
    if input_value is None:
        return []
    if isinstance(input_value, str):
        return [{"role": "user", "content": input_value}]
    if isinstance(input_value, dict):
        items = [input_value]
    elif isinstance(input_value, list):
        items = input_value
    else:
        raise UnsupportedFeatureError("`input` must be a string, object, or array.")

    messages: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise UnsupportedFeatureError("Each `input` item must be an object.")

        item_type = item.get("type")
        if item_type == "function_call_output":
            tool_call_id = item.get("call_id") or item.get("id")
            if not tool_call_id:
                raise UnsupportedFeatureError("`function_call_output` items need `call_id` or `id`.")
            if "output" in item:
                content = stringify_value(item["output"])
            else:
                content = extract_text(item.get("content"), source="function_call_output")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": content,
                }
            )
            continue

        if item_type == "computer_call_output":
            messages.append(
                {
                    "role": "user",
                    "content": describe_computer_call_output(item),
                }
            )
            continue

        if item_type == "function_call":
            tool_call_id = item.get("call_id") or item.get("id") or make_id("call")
            name = item.get("name")
            if not name:
                raise UnsupportedFeatureError("`function_call` items need a function `name`.")
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": tool_call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": stringify_value(item.get("arguments", {})),
                            },
                        }
                    ],
                }
            )
            continue

        if item_type == "reasoning":
            continue

        role = item.get("role")
        if item_type == "message" or role:
            if role not in {"system", "developer", "user", "assistant", "tool"}:
                raise UnsupportedFeatureError(f"Unsupported role `{role}`.")
            upstream_role = "system" if role == "developer" else role
            message: dict[str, Any] = {
                "role": upstream_role,
                "content": extract_message_content(item.get("content"), source=f"{role} message"),
            }
            if role == "assistant" and item.get("tool_calls"):
                message["tool_calls"] = item["tool_calls"]
            if role == "tool":
                tool_call_id = item.get("tool_call_id") or item.get("call_id")
                if tool_call_id:
                    message["tool_call_id"] = tool_call_id
            messages.append(message)
            continue

        raise UnsupportedFeatureError(f"Unsupported input item type `{item_type}`.")

    return messages


def extract_message_content(value: Any, source: str) -> str | list[dict[str, Any]]:
    if isinstance(value, list):
        parts = [convert_content_part(part, source) for part in value]
        if all(part["type"] == "text" for part in parts):
            return "".join(str(part.get("text", "")) for part in parts)
        return parts
    return extract_text(value, source=source)


def convert_content_part(part: Any, source: str) -> dict[str, Any]:
    if isinstance(part, str):
        return {"type": "text", "text": part}
    if not isinstance(part, dict):
        raise UnsupportedFeatureError(f"{source} contains a non-object content part.")
    part_type = part.get("type")
    if part_type in {"input_text", "output_text", "text"}:
        return {"type": "text", "text": str(part.get("text", ""))}
    if part_type == "refusal":
        return {"type": "text", "text": str(part.get("refusal", ""))}
    if part_type in {"input_image", "image_url"}:
        image_url = part.get("image_url") or part.get("url")
        if isinstance(image_url, str):
            return {"type": "image_url", "image_url": {"url": image_url}}
        if isinstance(image_url, dict):
            return {"type": "image_url", "image_url": deepcopy(image_url)}
        return {"type": "text", "text": describe_file_like_part("input_image", part)}
    if part_type in {"input_audio", "audio"}:
        audio = part.get("input_audio") or {
            key: part[key]
            for key in ("data", "format")
            if key in part
        }
        if audio:
            return {"type": "input_audio", "input_audio": deepcopy(audio)}
        return {"type": "text", "text": describe_file_like_part("input_audio", part)}
    if part_type in {"input_file", "file"}:
        return {"type": "text", "text": describe_file_like_part("input_file", part)}
    raise UnsupportedFeatureError(f"Unsupported content part type `{part_type}` in {source}.")


def describe_file_like_part(label: str, part: dict[str, Any]) -> str:
    fields = []
    for key in ("filename", "file_id", "mime_type", "url"):
        if part.get(key):
            fields.append(f"{key}={part[key]}")
    if part.get("file_data"):
        fields.append("file_data=<inline>")
    return f"[{label}: {', '.join(fields) if fields else 'metadata unavailable'}]"


def describe_computer_call_output(item: dict[str, Any]) -> str:
    call_id = item.get("call_id") or item.get("id") or "unknown"
    output = item.get("output")
    if isinstance(output, dict):
        output_type = output.get("type")
        if output_type in {"input_image", "image_url"}:
            image_url = output.get("image_url") or output.get("url")
            return f"Computer call output {call_id}: image_url={image_url}"
        return f"Computer call output {call_id}: {stringify_value(output)}"
    return f"Computer call output {call_id}: {stringify_value(output)}"


def extract_text(value: Any, source: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "text" in value:
            return str(value["text"])
        if "content" in value:
            return extract_text(value["content"], source=source)
        raise UnsupportedFeatureError(f"{source} must contain text content.")
    if not isinstance(value, list):
        raise UnsupportedFeatureError(f"{source} must be text or a list of text parts.")

    parts: list[str] = []
    for part in value:
        if isinstance(part, str):
            parts.append(part)
            continue
        if not isinstance(part, dict):
            raise UnsupportedFeatureError(f"{source} contains a non-object content part.")
        part_type = part.get("type")
        if part_type in {"input_text", "output_text", "text"}:
            parts.append(str(part.get("text", "")))
            continue
        if part_type == "refusal":
            parts.append(str(part.get("refusal", "")))
            continue
        if part_type in {"input_image", "image_url", "input_audio", "audio"}:
            raise UnsupportedFeatureError(
                f"{source} uses `{part_type}`, but this proxy only supports text content."
            )
        raise UnsupportedFeatureError(f"Unsupported content part type `{part_type}` in {source}.")
    return "".join(parts)


def flatten_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compatible_tools: list[dict[str, Any]] = []
    for tool in tools:
        compatible_tools.extend(convert_tool(tool))
    return compatible_tools


def convert_tool(tool: dict[str, Any]) -> list[dict[str, Any]]:
    tool_type = tool.get("type")
    if tool_type in IGNORED_HOSTED_TOOL_TYPES:
        return []
    if tool_type == "namespace":
        return flatten_namespace_tools(tool)
    if tool_type != "function":
        raise UnsupportedFeatureError(
            f"Unsupported tool type `{tool_type}`. Only `function` tools are supported."
        )
    if "function" in tool:
        return [deepcopy(tool)]
    name = tool.get("name")
    if not name:
        raise UnsupportedFeatureError("Function tools need a `name`.")
    function: dict[str, Any] = {
        "name": name,
        "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
    }
    if tool.get("description"):
        function["description"] = tool["description"]
    if "strict" in tool:
        function["strict"] = tool["strict"]
    return [{"type": "function", "function": function}]


def flatten_namespace_tools(tool: dict[str, Any]) -> list[dict[str, Any]]:
    nested_tools = tool.get("tools")
    if nested_tools is None:
        for alternate_key in ("items", "children"):
            if alternate_key in tool:
                nested_tools = tool[alternate_key]
                break
    if nested_tools is None:
        return []
    if not isinstance(nested_tools, list):
        raise UnsupportedFeatureError("`namespace` tools must contain a list of nested tools.")
    compatible_tools: list[dict[str, Any]] = []
    for nested_tool in nested_tools:
        if not isinstance(nested_tool, dict):
            raise UnsupportedFeatureError("Nested namespace tools must be objects.")
        compatible_tools.extend(convert_tool(nested_tool))
    return compatible_tools


def convert_tool_choice(value: Any) -> Any | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        raise UnsupportedFeatureError("`tool_choice` must be a string or object.")
    if "function" in value:
        return value
    if value.get("type") == "namespace":
        return None
    if value.get("type") in IGNORED_HOSTED_TOOL_TYPES:
        return None
    if value.get("type") != "function":
        return value
    name = value.get("name")
    if not name:
        raise UnsupportedFeatureError("Function `tool_choice` objects need a `name`.")
    return {"type": "function", "function": {"name": name}}


def convert_response_format(payload: dict[str, Any]) -> dict[str, Any] | None:
    if "response_format" in payload:
        return payload["response_format"]

    text_config = payload.get("text")
    if not isinstance(text_config, dict):
        return None
    format_config = text_config.get("format")
    if not isinstance(format_config, dict):
        return None

    format_type = format_config.get("type")
    if format_type == "json_object":
        return {"type": "json_object"}

    if format_type == "json_schema":
        schema = format_config.get("schema") or {}
        name = format_config.get("name") or "response"
        strict = bool(format_config.get("strict", False))
        return {
            "type": "json_schema",
            "json_schema": {
                "name": name,
                "strict": strict,
                "schema": schema,
            },
        }

    raise UnsupportedFeatureError(f"Unsupported text format type `{format_type}`.")


def build_response_from_upstream(
    payload: dict[str, Any],
    upstream_response: dict[str, Any],
    response_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    choice = first_choice(upstream_response)
    message = choice.get("message") or {}
    assistant_text = normalize_assistant_text(message.get("content"))
    reasoning_content = normalize_assistant_text(message.get("reasoning_content"))
    tool_calls = normalize_tool_calls(message.get("tool_calls"), payload=payload)
    finish_reason = choice.get("finish_reason")
    usage = convert_usage(upstream_response.get("usage"))
    response = build_response_object(
        payload=payload,
        response_id=response_id,
        assistant_text=assistant_text,
        tool_calls=tool_calls,
        usage=usage,
        finish_reason=finish_reason,
        created_at=upstream_response.get("created") or int(time.time()),
    )
    return response, build_history_output(assistant_text, tool_calls, reasoning_content=reasoning_content)


def build_response_object(
    payload: dict[str, Any],
    response_id: str,
    assistant_text: str,
    tool_calls: list[dict[str, Any]],
    usage: dict[str, int] | None,
    finish_reason: str | None,
    created_at: int,
    message_id: str | None = None,
) -> dict[str, Any]:
    output_items = build_output_items(assistant_text, tool_calls, message_id=message_id)
    status = "completed"
    incomplete_details = None
    if finish_reason == "length":
        status = "incomplete"
        incomplete_details = {"reason": "max_output_tokens"}

    return build_response_envelope(
        payload=payload,
        response_id=response_id,
        created_at=created_at,
        completed_at=int(time.time()),
        status=status,
        output=output_items,
        output_text=assistant_text,
        usage=usage,
        incomplete_details=incomplete_details,
    )


def build_response_envelope(
    payload: dict[str, Any],
    response_id: str,
    created_at: int,
    completed_at: int | None,
    status: str,
    output: list[dict[str, Any]],
    output_text: str,
    usage: dict[str, Any] | None,
    incomplete_details: dict[str, Any] | None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "response",
        "background": False,
        "created_at": created_at,
        "completed_at": completed_at,
        "status": status,
        "error": error,
        "frequency_penalty": payload.get("frequency_penalty", 0.0),
        "incomplete_details": incomplete_details,
        "instructions": payload.get("instructions"),
        "max_output_tokens": payload.get("max_output_tokens"),
        "model": payload.get("model"),
        "output": output,
        "output_text": output_text,
        "parallel_tool_calls": payload.get("parallel_tool_calls", True),
        "previous_response_id": payload.get("previous_response_id"),
        "presence_penalty": payload.get("presence_penalty", 0.0),
        "reasoning": deepcopy(payload.get("reasoning")) if isinstance(payload.get("reasoning"), dict) else None,
        "store": payload.get("store", True),
        "temperature": payload.get("temperature", 1.0),
        "text": normalize_text_payload(payload),
        "tool_choice": deepcopy(payload.get("tool_choice", "auto")),
        "tools": deepcopy(payload.get("tools", [])),
        "top_logprobs": payload.get("top_logprobs", 0),
        "top_p": payload.get("top_p", 1.0),
        "truncation": payload.get("truncation", "disabled"),
        "usage": usage,
        "user": payload.get("user"),
        "metadata": deepcopy(payload.get("metadata") or {}),
    }


def build_output_items(
    assistant_text: str,
    tool_calls: list[dict[str, Any]],
    message_id: str | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if assistant_text or not tool_calls:
        output.append(
            {
                "id": message_id or make_id("msg"),
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": assistant_text,
                        "annotations": [],
                    }
                ],
            }
        )
    for tool_call in tool_calls:
        output.append(
            {
                "id": tool_call["id"],
                "type": "function_call",
                "status": "completed",
                "name": tool_call["function"]["name"],
                "arguments": tool_call["function"]["arguments"],
                "call_id": tool_call["id"],
            }
        )
    return output


def build_history_output(
    assistant_text: str,
    tool_calls: list[dict[str, Any]],
    reasoning_content: str = "",
) -> list[dict[str, Any]]:
    message: dict[str, Any] = {"role": "assistant", "content": assistant_text}
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    if tool_calls:
        message["tool_calls"] = deepcopy(tool_calls)
    return [message]


def merge_conversation_history(
    history: list[dict[str, Any]],
    input_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not history:
        return deepcopy(input_messages)
    if not input_messages:
        return deepcopy(history)

    overlap_length = 0
    max_overlap = min(len(history), len(input_messages))
    while overlap_length < max_overlap and messages_match_for_history(
        history[overlap_length],
        input_messages[overlap_length],
    ):
        overlap_length += 1

    if overlap_length == 0:
        return restore_reasoning_content(history, input_messages)

    merged_messages = [
        merge_history_message(history[index], input_messages[index])
        for index in range(overlap_length)
    ]
    merged_messages.extend(deepcopy(input_messages[overlap_length:]))
    return restore_reasoning_content(history, merged_messages)


def restore_reasoning_content(
    history: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    restored = deepcopy(messages)
    used_history_indexes: set[int] = set()
    for message in restored:
        if message.get("role") != "assistant" or message.get("reasoning_content"):
            continue
        for index, history_message in enumerate(history):
            if index in used_history_indexes:
                continue
            reasoning_content = history_message.get("reasoning_content")
            if not reasoning_content:
                continue
            if messages_match_for_history(history_message, message):
                message["reasoning_content"] = reasoning_content
                used_history_indexes.add(index)
                break
    return restored


def normalize_history_message(message: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(message)
    normalized.pop("reasoning_content", None)
    return normalized


def messages_match_for_history(history_message: dict[str, Any], input_message: dict[str, Any]) -> bool:
    if history_message.get("role") != input_message.get("role"):
        return False

    history_tool_calls = normalized_tool_call_signatures(history_message.get("tool_calls"))
    input_tool_calls = normalized_tool_call_signatures(input_message.get("tool_calls"))
    if history_tool_calls or input_tool_calls:
        return history_tool_calls == input_tool_calls

    if history_message.get("content", "") != input_message.get("content", ""):
        return False

    if history_tool_calls != input_tool_calls:
        return False

    return True


def normalized_tool_call_signatures(tool_calls: Any) -> list[tuple[str, str]]:
    if not isinstance(tool_calls, list):
        return []

    signatures: list[tuple[str, str]] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            signatures.append(("", ""))
            continue
        function = tool_call.get("function") or {}
        signatures.append((str(tool_call.get("type", "")), str(function.get("name", ""))))
    return signatures


def merge_history_message(history_message: dict[str, Any], input_message: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(input_message)
    reasoning_content = history_message.get("reasoning_content")
    if reasoning_content and not merged.get("reasoning_content"):
        merged["reasoning_content"] = reasoning_content
    return merged


def normalize_assistant_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(part.get("text", "") for part in value if isinstance(part, dict))
    return str(value)


def normalize_tool_calls(value: Any, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not value:
        return []
    tool_calls: list[dict[str, Any]] = []
    tool_schemas = build_tool_schema_index(payload or {})
    for item in value:
        if not isinstance(item, dict):
            continue
        tool_call_id = item.get("id") or make_id("call")
        function = item.get("function") or {}
        tool_name = str(function.get("name", ""))
        arguments = stringify_value(function.get("arguments", {}))
        normalized_name, normalized_arguments = normalize_tool_call_output(
            tool_name,
            arguments,
            tool_schemas,
        )
        tool_calls.append(
            {
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": normalized_name,
                    "arguments": normalized_arguments,
                },
            }
        )
    return tool_calls


def build_tool_schema_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_tools = payload.get("tools")
    if not isinstance(raw_tools, list):
        return {}

    try:
        compatible_tools = flatten_tools(raw_tools)
    except UnsupportedFeatureError:
        return {}

    tool_schemas: dict[str, dict[str, Any]] = {}
    for tool in compatible_tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") or {}
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        tool_schemas[name] = deepcopy(function)
    return tool_schemas


def normalize_tool_call_output(
    tool_name: str,
    arguments: str,
    tool_schemas: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    translated = translate_hallucinated_apply_patch(tool_name, arguments, tool_schemas)
    if translated is not None:
        return translated

    schema = (tool_schemas.get(tool_name) or {}).get("parameters")
    if not isinstance(schema, dict):
        return tool_name, arguments

    parsed_arguments = parse_json_value(arguments)
    if parsed_arguments is None:
        return tool_name, arguments

    coerced_arguments = coerce_value_to_schema(parsed_arguments, schema)
    if coerced_arguments == parsed_arguments:
        return tool_name, arguments
    return tool_name, json.dumps(coerced_arguments, ensure_ascii=False)


def parse_json_value(value: str) -> Any | None:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def coerce_value_to_schema(value: Any, schema: dict[str, Any] | None) -> Any:
    if not isinstance(schema, dict):
        return value

    schema_type = schema.get("type")
    if schema_type == "object":
        if isinstance(value, str):
            parsed = parse_json_value(value)
            if parsed is None:
                return value
            value = parsed
        if not isinstance(value, dict):
            return value

        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return value

        coerced: dict[str, Any] = {}
        for key, item_value in value.items():
            coerced[key] = coerce_value_to_schema(item_value, properties.get(key))
        return coerced

    if schema_type == "array":
        if isinstance(value, str):
            parsed = parse_json_value(value)
            if parsed is None:
                return value
            value = parsed
        if not isinstance(value, list):
            return value

        item_schema = schema.get("items")
        return [coerce_value_to_schema(item, item_schema) for item in value]

    if schema_type == "integer" and isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return value

    if schema_type == "number" and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value

    if schema_type == "boolean" and isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False

    return value


def translate_hallucinated_apply_patch(
    tool_name: str,
    arguments: str,
    tool_schemas: dict[str, dict[str, Any]],
) -> tuple[str, str] | None:
    if tool_name != "apply_patch":
        return None
    if "shell" not in tool_schemas:
        return None

    parsed_arguments = parse_json_value(arguments)
    if not isinstance(parsed_arguments, dict):
        return None

    patch_payload = parsed_arguments.get("json", parsed_arguments)
    if not isinstance(patch_payload, dict):
        return None

    mode = patch_payload.get("mode")
    path = patch_payload.get("path")
    content = patch_payload.get("content")
    if mode not in {"new_file", "write_file", "overwrite"}:
        return None
    if not isinstance(path, str) or not isinstance(content, str):
        return None

    shell_arguments = {
        "command": [
            "powershell.exe",
            "-Command",
            build_powershell_write_file_command(path, content),
        ]
    }
    return "shell", json.dumps(shell_arguments, ensure_ascii=False)


def build_powershell_write_file_command(path: str, content: str) -> str:
    escaped_path = path.replace("'", "''")
    encoded_content = base64.b64encode(content.encode("utf-8")).decode("ascii")
    return (
        f"$path = '{escaped_path}'; "
        "$dir = Split-Path -Parent $path; "
        "if ($dir -and -not (Test-Path -LiteralPath $dir)) { "
        "New-Item -ItemType Directory -Path $dir -Force | Out-Null "
        "}; "
        f"$content = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{encoded_content}')); "
        "[System.IO.File]::WriteAllText($path, $content, [System.Text.UTF8Encoding]::new($false)); "
        "Write-Output \"File written\""
    )


def convert_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    prompt_tokens = int(value.get("prompt_tokens", 0))
    completion_tokens = int(value.get("completion_tokens", 0))
    total_tokens = int(value.get("total_tokens", prompt_tokens + completion_tokens))
    return {
        "input_tokens": prompt_tokens,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": completion_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": total_tokens,
    }


def normalize_text_payload(payload: dict[str, Any]) -> dict[str, Any]:
    text_payload = payload.get("text")
    if isinstance(text_payload, dict):
        return deepcopy(text_payload)
    return {"format": {"type": "text"}}


def first_choice(upstream_response: dict[str, Any]) -> dict[str, Any]:
    choices = upstream_response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise UnsupportedFeatureError("Upstream response did not contain any choices.")
    return choices[0]


def stringify_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def format_sse(event: str, data: dict[str, Any] | str) -> bytes:
    if isinstance(data, str):
        encoded = data
    else:
        encoded = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {encoded}\n\n".encode("utf-8")


@dataclass
class StreamToolCall:
    id: str
    output_index: int
    name: str = ""
    arguments: str = ""


@dataclass
class StreamAccumulator:
    payload: dict[str, Any]
    response_id: str
    created_at: int = field(default_factory=lambda: int(time.time()))
    message_id: str = field(default_factory=lambda: make_id("msg"))
    text_started: bool = False
    text_chunks: list[str] = field(default_factory=list)
    reasoning_chunks: list[str] = field(default_factory=list)
    tool_calls: dict[int, StreamToolCall] = field(default_factory=dict)
    tool_call_order: list[int] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    sequence_number: int = 0

    def initial_events(self) -> list[bytes]:
        response_stub = build_response_envelope(
            payload=self.payload,
            response_id=self.response_id,
            created_at=self.created_at,
            completed_at=None,
            status="in_progress",
            output=[],
            output_text="",
            usage=None,
            incomplete_details=None,
        )
        return [
            self.emit(
                "response.created",
                {"response": response_stub},
            ),
            self.emit(
                "response.in_progress",
                {"response": response_stub},
            ),
        ]

    def consume_chunk(self, chunk: dict[str, Any]) -> list[bytes]:
        events: list[bytes] = []
        if isinstance(chunk.get("usage"), dict):
            self.usage = chunk["usage"]

        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return events

        choice = first_choice(chunk)
        delta = choice.get("delta") or {}
        if "role" in delta and delta["role"] == "assistant" and "content" not in delta:
            pass

        content = delta.get("content")
        reasoning_content = delta.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content:
            self.reasoning_chunks.append(reasoning_content)
        if isinstance(content, str) and content:
            if not self.text_started:
                self.text_started = True
                events.append(
                    self.emit(
                        "response.output_item.added",
                        {
                            "output_index": 0,
                            "item": self.build_stream_message_item(status="in_progress", text=""),
                        },
                    )
                )
                events.append(
                    self.emit(
                        "response.content_part.added",
                        {
                            "item_id": self.message_id,
                            "output_index": 0,
                            "content_index": 0,
                            "part": self.build_stream_text_part(""),
                        },
                    )
                )
            self.text_chunks.append(content)
            events.append(
                self.emit(
                    "response.output_text.delta",
                    {
                        "item_id": self.message_id,
                        "output_index": 0,
                        "content_index": 0,
                        "delta": content,
                        "logprobs": [],
                    },
                )
            )

        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            for raw_tool_call in tool_calls:
                if not isinstance(raw_tool_call, dict):
                    continue
                index = int(raw_tool_call.get("index", 0))
                tool_call = self.tool_calls.get(index)
                function = raw_tool_call.get("function") or {}
                if tool_call is None:
                    tool_output_index = len(self.tool_call_order) + (1 if self.text_started else 0)
                    tool_call = StreamToolCall(
                        id=raw_tool_call.get("id") or make_id("call"),
                        output_index=tool_output_index,
                        name=function.get("name", ""),
                    )
                    self.tool_calls[index] = tool_call
                    self.tool_call_order.append(index)
                    if raw_tool_call.get("id"):
                        tool_call.id = raw_tool_call["id"]
                    events.append(
                        self.emit(
                            "response.output_item.added",
                            {
                                "output_index": tool_output_index,
                                "item": self.build_stream_function_call_item(tool_call, status="in_progress"),
                            },
                        )
                    )
                elif function.get("name"):
                    tool_call.name += function["name"]
                if function.get("arguments"):
                    arguments_delta = function["arguments"]
                    tool_call.arguments += arguments_delta
                    events.append(
                        self.emit(
                            "response.function_call_arguments.delta",
                            {
                                "item_id": tool_call.id,
                                "output_index": tool_call.output_index,
                                "delta": arguments_delta,
                            },
                        )
                    )

        self.finish_reason = choice.get("finish_reason") or self.finish_reason
        return events

    def finalize(self) -> tuple[list[bytes], dict[str, Any], list[dict[str, Any]]]:
        events: list[bytes] = []
        assistant_text = "".join(self.text_chunks)
        reasoning_content = "".join(self.reasoning_chunks)
        tool_schemas = build_tool_schema_index(self.payload)
        normalized_tool_calls = []
        for index in self.tool_call_order:
            raw_tool_call = self.tool_calls[index]
            normalized_name, normalized_arguments = normalize_tool_call_output(
                raw_tool_call.name,
                raw_tool_call.arguments or "{}",
                tool_schemas,
            )
            normalized_tool_calls.append(
                {
                    "id": raw_tool_call.id,
                    "type": "function",
                    "function": {
                        "name": normalized_name,
                        "arguments": normalized_arguments,
                    },
                }
            )

        has_message_item = bool(assistant_text or not normalized_tool_calls)
        if has_message_item:
            stream_message_item = self.build_stream_message_item(status="completed", text=assistant_text)
            events.append(
                self.emit(
                    "response.output_text.done",
                    {
                        "item_id": self.message_id,
                        "output_index": 0,
                        "content_index": 0,
                        "text": assistant_text,
                        "logprobs": [],
                    },
                )
            )
            events.append(
                self.emit(
                    "response.content_part.done",
                    {
                        "item_id": self.message_id,
                        "output_index": 0,
                        "content_index": 0,
                        "part": self.build_stream_text_part(assistant_text),
                    },
                )
            )
            events.append(
                self.emit(
                    "response.output_item.done",
                    {
                        "output_index": 0,
                        "item": stream_message_item,
                    },
                )
            )

        for tool_index, tool_call in enumerate(normalized_tool_calls):
            output_index = tool_index + (1 if has_message_item else 0)
            events.append(
                self.emit(
                    "response.function_call_arguments.done",
                    {
                        "item_id": tool_call["id"],
                        "output_index": output_index,
                        "arguments": tool_call["function"]["arguments"],
                    },
                )
            )
            events.append(
                self.emit(
                    "response.output_item.done",
                    {
                        "output_index": output_index,
                        "item": self.build_stream_function_call_item(
                            StreamToolCall(
                                id=tool_call["id"],
                                output_index=output_index,
                                name=tool_call["function"]["name"],
                                arguments=tool_call["function"]["arguments"],
                            ),
                            status="completed",
                        ),
                    },
                )
            )

        response = build_response_object(
            payload=self.payload,
            response_id=self.response_id,
            assistant_text=assistant_text,
            tool_calls=normalized_tool_calls,
            usage=convert_usage(self.usage),
            finish_reason=self.finish_reason,
            created_at=self.created_at,
            message_id=self.message_id if has_message_item else None,
        )
        history_output = build_history_output(
            assistant_text,
            normalized_tool_calls,
            reasoning_content=reasoning_content,
        )
        return events, response, history_output

    def emit(self, event_type: str, payload: dict[str, Any]) -> bytes:
        body = {"type": event_type, **payload, "sequence_number": self.sequence_number}
        self.sequence_number += 1
        return format_sse(event_type, body)

    def build_stream_text_part(self, text: str) -> dict[str, Any]:
        return {
            "type": "output_text",
            "text": text,
            "annotations": [],
            "logprobs": [],
        }

    def build_stream_message_item(self, status: str, text: str) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if status == "completed":
            content.append(self.build_stream_text_part(text))
        return {
            "id": self.message_id,
            "type": "message",
            "status": status,
            "role": "assistant",
            "phase": "final_answer",
            "content": content,
        }

    def build_stream_function_call_item(self, tool_call: StreamToolCall, status: str) -> dict[str, Any]:
        return {
            "id": tool_call.id,
            "type": "function_call",
            "status": status,
            "name": tool_call.name,
            "arguments": tool_call.arguments,
            "call_id": tool_call.id,
        }

    def completed_event(self, response: dict[str, Any]) -> bytes:
        return self.emit("response.completed", {"response": response})

    def failed_event(self, error: dict[str, Any]) -> bytes:
        return self.emit("response.failed", {"error": error})
