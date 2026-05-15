from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import ast
import base64
import html
import json
from pathlib import Path
import platform
import re
import shlex
import subprocess
import tempfile
import time
import uuid
from typing import Any

from app.config import Settings
from app.protocol import ProtocolReport

IGNORED_HOSTED_TOOL_TYPES = {
    "web_search",
    "web_search_preview",
    "file_search",
    "computer_use",
    "computer_use_preview",
    "code_interpreter",
    "image_generation",
}

DOCUMENT_SINGLE_SCRIPT_HINT = (
    "For document processing, report generation, Office files, or project/file generation tasks, "
    "prefer a single reusable script that reads the required inputs, generates the outputs, and prints a concise summary. "
    "Do not issue repeated python -c, Get-Content, New-Item, or one-off inspection commands when one script can complete the workflow. "
    "Do not embed large base64 payloads or long inline python -c scripts in shell commands. "
    "Do not use apply_patch through fallback markers. For creating or replacing files, prefer the virtual write_file marker "
    "with path and content; the proxy will translate it to the available shell tool. "
    "Use at most one write_file call and at most one verification command for a single-file generation task. "
    "After a file write succeeds, do not read the full file or rewrite the same file; provide the final answer. "
    "Never call Get-Content -Raw, cat, or type on a complete generated file for verification. "
    "For generated HTML/CSS/JS, keep the file compact unless high fidelity is explicitly requested; avoid long comments and oversized sample data. "
    "For Chinese, emoji, or non-ASCII content, write actual UTF-8 text directly; do not emit literal \\uXXXX escape sequences."
)
NATIVE_DOCUMENT_SINGLE_SCRIPT_HINT = (
    "For document processing, report generation, Office files, or project/file generation tasks, "
    "prefer a single reusable script that reads the required inputs, generates the outputs, and prints a concise summary. "
    "Do not issue repeated python -c, Get-Content, New-Item, or one-off inspection commands when one script can complete the workflow. "
    "Do not embed large base64 payloads or long inline python -c scripts in shell commands. "
    "Do not call planning-only tools for single-file generation or direct file edits; write or edit the file first. "
    "For creating or replacing files, use the available function tool directly and write the complete file once. "
    "Use at most one file-write command and at most one verification command for a single-file generation task. "
    "After a file write succeeds, do not read the full file or rewrite the same file; provide the final answer. "
    "Never call Get-Content -Raw, cat, or type on a complete generated file for verification. "
    "For generated HTML/CSS/JS, keep the file compact unless high fidelity is explicitly requested; avoid long comments and oversized sample data. "
    "For Chinese, emoji, or non-ASCII content, write actual UTF-8 text directly; do not emit literal \\uXXXX escape sequences."
)
AGENT_TOOL_USE_HINT = (
    "You are serving an agent runtime through a Chat Completions compatibility layer. "
    "When tools are available and the user asks you to inspect files, run commands, edit files, "
    "read documents, or continue work after tool output, call the appropriate tool through the native function tool interface in the same turn. "
    "Do not stop after saying you will run or inspect something. "
    "After a tool call fails, immediately call a corrected tool if the user task is still actionable; do not answer with only an apology or explanation. "
    "Do not describe, print, or simulate tool calls in assistant text. "
    "Do not emit XML tags, JSON tool snippets, markdown fences, pseudo function tags, or fallback markers for tool calls. "
    "Use only the native function calling channel supplied by the API. "
    "Only provide a final natural-language answer when the task is actually complete."
    f" {NATIVE_DOCUMENT_SINGLE_SCRIPT_HINT}"
)
PROMPT_TOOL_USE_HINT = (
    "You are serving an agent runtime through a Chat Completions compatibility layer. "
    "The upstream API may ignore native tool parameters, so call tools only by outputting exactly one "
    'fallback marker like <tool_call>{"name":"tool_name","arguments":{"arg":"value"}}</tool_call> and no prose. '
    "For file creation or replacement, prefer "
    '<tool_call>{"name":"write_file","arguments":{"path":"target file path","content":"complete file content"}}</tool_call>; '
    "write_file is a virtual marker translated by the proxy to the available shell tool. "
    "Do not use XML-style <function=...> or <parameter=...> tool tags. "
    "If the user asks in Chinese or another non-English language, infer the intended action and still call the tool. "
    "Never ask the user to provide the exact shell command when the intent is clear. "
    "If the user message appears corrupted, garbled, reduced to question marks, or incomplete because of encoding, "
    "do not ask for clarification; choose a safe inspection action such as listing the current directory. "
    "For example, if the user asks to list/view the current directory, call shell_command with ls -la on Unix/macOS "
    "or Get-ChildItem on Windows if that is the available shell context. "
    "If the user asks to read a file, call a safe read command. If the user asks to write or edit files, call the "
    "available file or shell tool with the needed action. "
    "Use only the tool names listed below, plus the virtual write_file marker for file writes. "
    "Do not invent read, view, open_file, or other unlisted tool names. "
    "Do not describe what a tool is. Do not stop after saying you will run or inspect something. "
    "Only provide a final natural-language answer when the task is actually complete."
    f" {DOCUMENT_SINGLE_SCRIPT_HINT}"
)
TOOL_CALL_MARKER_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
TOOL_CALL_XML_RE = re.compile(
    r"<tool_call>\s*<function=([A-Za-z0-9_.-]+)>\s*(.*?)</function>\s*</tool_call>",
    re.DOTALL,
)
TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>\s*(.*?)</tool_call>", re.DOTALL)
TOOL_CALL_RELAXED_FUNCTION_RE = re.compile(
    r"\bfunction\s*[:=]\s*([A-Za-z0-9_.-]+)\s*(.*?)(?:</function>\s*)?$",
    re.DOTALL | re.IGNORECASE,
)
TOOL_CALL_FUNCTION_TAG_RE = re.compile(
    r"^<function=([A-Za-z0-9_.-]+)>\s*(.*?)(?:</function>\s*)?$",
    re.DOTALL,
)
TOOL_CALL_TAG_NAMED_RE = re.compile(
    r"^<([A-Za-z0-9_.-]+)>\s*(.*?)</\1>\s*$",
    re.DOTALL,
)
TOOL_CALL_TAG_NAMED_RELAXED_RE = re.compile(
    r"^<([A-Za-z0-9_.-]+)>\s*(.*?)(?:</\1>\s*)?$",
    re.DOTALL,
)
TOOL_CALL_XML_PARAMETER_RE = re.compile(
    r"<parameter=([A-Za-z0-9_.-]+)>(.*?)</parameter>",
    re.DOTALL,
)
TOOL_CALL_PARAMETER_START_RE = re.compile(
    r"<parameter=([A-Za-z0-9_.-]+)>",
    re.DOTALL,
)
TOOL_CALL_UNCLOSED_PARAMETER_RE = re.compile(
    r"<parameter=([A-Za-z0-9_.-]+)>(.*)$",
    re.DOTALL,
)
TOOL_CALL_SIMPLE_PARAMETER_RE = re.compile(
    r"<([A-Za-z0-9_.-]+)>(.*?)</\1>",
    re.DOTALL,
)
TOOL_CALL_SIMPLE_PARAMETER_START_RE = re.compile(
    r"<([A-Za-z0-9_.-]+)>",
    re.DOTALL,
)
TOOL_CALL_UNCLOSED_SIMPLE_PARAMETER_RE = re.compile(
    r"<([A-Za-z0-9_.-]+)>(.*)$",
    re.DOTALL,
)
MAX_INLINE_PYTHON_RUNNER_COMMAND_CHARS = 24_000
MAX_INLINE_POWERSHELL_WRITE_COMMAND_CHARS = 8_000
MAX_REPLAY_TOOL_ARGUMENT_CHARS = 2_000
MAX_REPLAY_TOOL_OUTPUT_CHARS = 2_500
MAX_REPLAY_ASSISTANT_CONTENT_CHARS = 2_000
MAX_FULL_FILE_READ_LINES = 80
PLANNING_ONLY_TOOL_NAMES = {"update_plan"}
COMPATIBILITY_MODEL_MARKERS = ("mimo", "xiaomi", "deepseek", "qwen", "glm", "kimi")
TOOL_PAYLOAD_DIR = Path(tempfile.gettempdir()) / "responses-proxy-tool-payloads"
SYNTHETIC_TOOL_CALL_REASONING_CONTENT = (
    "[Responses Proxy synthesized reasoning_content because the upstream model returned a tool call "
    "without reasoning_content. This keeps MiMo/DeepSeek thinking-mode tool-call history replayable.]"
)
TOOL_INTENT_RETRY_PROMPT = (
    "Your previous assistant message said it would continue or perform work, but it did not call a tool. "
    "The agent runtime cannot execute prose. Return exactly one native function_call now using one of the "
    "available execution tools. If a shell tool is available and the task needs file creation, file editing, "
    "inspection, or command execution, call shell with one concise command. Do not answer in prose."
)
EMPTY_TOOL_TURN_RETRY_PROMPT = (
    "Your previous assistant message was empty even though execution tools are available. "
    "The agent runtime treats an empty assistant message as the task stopping. Return exactly one native "
    "function_call now using an available execution tool, or return a concise final answer only if the task "
    "is truly complete. Do not answer with an empty message."
)
TOOL_INTENT_TEXT_LIMIT = 1_200
TOOL_INTENT_REPLAY_LIMIT = 800
TOOL_INTENT_DONE_MARKERS = (
    "done",
    "completed",
    "created",
    "successfully",
    "finished",
    "已完成",
    "完成了",
    "已创建",
    "已生成",
    "成功",
    "文件路径",
)
TOOL_INTENT_CODE_MARKERS = ("```", "<!doctype html", "<html", "</html>")
TOOL_INTENT_PATTERNS = (
    re.compile(
        r"\b(?:let me|i(?:'ll| will| need to| am going to)|i'm going to)\b"
        r".{0,120}\b(?:run|execute|write|create|modify|edit|read|inspect|open|list|save|call|use)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:continue writing|continue to write|write the full|write .* via powershell|write .* via python)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:我来|让我|接下来|继续|现在|准备|将|会|需要).{0,120}"
        r"(?:写入|写|创建|生成|修改|编辑|读取|查看|检查|运行|执行|调用|保存|处理|分析)",
        re.DOTALL,
    ),
)


class UnsupportedFeatureError(ValueError):
    """Raised when a Responses API feature cannot be mapped to chat/completions."""


@dataclass
class PreparedChatRequest:
    upstream_payload: dict[str, Any]
    conversation_messages: list[dict[str, Any]]
    input_items: list[dict[str, Any]]
    hosted_output_items: list[dict[str, Any]] = field(default_factory=list)
    hosted_annotations: list[dict[str, Any]] = field(default_factory=list)
    protocol_report: ProtocolReport | None = None


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def build_error(message: str, error_type: str = "invalid_request_error") -> dict[str, Any]:
    return {"error": {"message": message, "type": error_type}}


def prepare_chat_request(
    payload: dict[str, Any],
    settings: Settings,
    conversation_history: list[dict[str, Any]] | None = None,
    hosted_tool_messages: list[dict[str, Any]] | None = None,
    hosted_output_items: list[dict[str, Any]] | None = None,
    hosted_annotations: list[dict[str, Any]] | None = None,
    protocol_report: ProtocolReport | None = None,
) -> PreparedChatRequest:
    history = deepcopy(conversation_history or [])
    instructions = payload.get("instructions")
    if input_contains_image(payload.get("input")) and not settings.upstream_supports_image_input:
        raise UnsupportedFeatureError(
            "当前上游模型不支持图片输入。The active upstream preset is not marked as supporting image input. "
            "Switch to a vision-capable preset, or enable `upstream_supports_image_input` for this preset "
            "and restart the proxy."
        )
    input_messages = convert_input_to_messages(payload.get("input"))
    if payload.get("previous_response_id"):
        conversation_messages = append_conversation_history(history, input_messages)
    else:
        conversation_messages = merge_conversation_history(history, input_messages)
    conversation_messages = compact_chat_messages_for_upstream(conversation_messages)
    conversation_messages = sanitize_chat_message_sequence(conversation_messages)
    conversation_messages = ensure_tool_call_reasoning_content(conversation_messages, settings)
    conversation_messages = hoist_system_messages(conversation_messages)

    raw_tools = payload.get("tools")
    compatible_tools = flatten_tools(raw_tools) if isinstance(raw_tools, list) else []
    compatible_tools = filter_upstream_tools_for_model(compatible_tools, settings)
    prompt_tool_mode = should_use_prompt_tool_mode(settings)

    messages: list[dict[str, Any]] = []
    if instructions:
        messages.append({"role": "system", "content": extract_text(instructions, source="instructions")})
    if compatible_tools:
        messages.append(
            {
                "role": "system",
                "content": build_agent_tool_use_message(compatible_tools, prompt_mode=prompt_tool_mode),
            }
        )
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

    if compatible_tools and not prompt_tool_mode:
        upstream_payload["tools"] = compatible_tools

    tool_choice = payload.get("tool_choice")
    if upstream_payload.get("tools"):
        if tool_choice is None:
            upstream_payload["tool_choice"] = "auto"
        else:
            converted_tool_choice = convert_tool_choice(tool_choice)
            if converted_tool_choice is not None:
                upstream_payload["tool_choice"] = converted_tool_choice

    response_format = convert_response_format(payload)
    if response_format is not None:
        upstream_payload["response_format"] = response_format

    return PreparedChatRequest(
        upstream_payload=upstream_payload,
        conversation_messages=conversation_messages,
        input_items=build_input_items(payload.get("input")),
        hosted_output_items=deepcopy(hosted_output_items or []),
        hosted_annotations=deepcopy(hosted_annotations or []),
        protocol_report=protocol_report,
    )


def should_use_prompt_tool_mode(settings: Settings) -> bool:
    mode = getattr(settings, "tool_call_mode", "native").strip().lower()
    return mode in {"prompt", "text", "compat", "compatibility"}


def filter_upstream_tools_for_model(
    compatible_tools: list[dict[str, Any]], settings: Settings
) -> list[dict[str, Any]]:
    if not compatible_tools or not should_suppress_planning_tools(settings):
        return compatible_tools

    execution_tools = [
        tool for tool in compatible_tools if get_compatible_tool_name(tool) not in PLANNING_ONLY_TOOL_NAMES
    ]
    return execution_tools or compatible_tools


def should_suppress_planning_tools(settings: Settings) -> bool:
    marker = f"{settings.upstream_base_url} {settings.upstream_model}".lower()
    return any(model_marker in marker for model_marker in COMPATIBILITY_MODEL_MARKERS)


def get_compatible_tool_name(tool: dict[str, Any]) -> str:
    function = tool.get("function")
    if not isinstance(function, dict):
        return ""
    name = function.get("name")
    return str(name) if isinstance(name, str) else ""


def build_agent_tool_use_message(compatible_tools: list[dict[str, Any]], *, prompt_mode: bool) -> str:
    if not prompt_mode:
        tool_names = [
            str((tool.get("function") or {}).get("name"))
            for tool in compatible_tools
            if isinstance((tool.get("function") or {}).get("name"), str)
        ]
        tool_list = ", ".join(tool_names) if tool_names else "none"
        extra = (
            f"\nAvailable native function tools: {tool_list}."
            "\nDo not call, mention, or simulate unlisted tools such as apply_patch, read, write_file, or open_file "
            "unless that exact function name appears in the available native tool list."
            "\nIf only a shell tool is available, call shell directly. On Windows, avoid long PowerShell here-strings "
            "and avoid inline python -c payloads; use one concise shell command and let the proxy shorten large writes."
            "\nDo not call planning-only tools when an execution tool can make progress."
            "\nDo not verify generated files by reading the full file back; after one successful write, provide the final answer."
        )
        return AGENT_TOOL_USE_HINT + extra

    tool_lines = [
        "- write_file(path, content): virtual marker for file creation/replacement; use this for single-file output."
    ]
    for tool in compatible_tools:
        function = tool.get("function") or {}
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        tool_lines.append(format_compact_tool_prompt_line(function))
    if not tool_lines:
        return PROMPT_TOOL_USE_HINT
    return (
        PROMPT_TOOL_USE_HINT
        + f"\nCurrent proxy host platform: {platform.system()}."
        + "\nIf calling a shell tool on Windows, prefer PowerShell-compatible commands such as Get-ChildItem -Force."
        + "\nAvailable tools:\n"
        + "\n".join(tool_lines)
    )


def format_compact_tool_prompt_line(function: dict[str, Any]) -> str:
    name = str(function.get("name") or "tool")
    parameters = function.get("parameters")
    properties = parameters.get("properties") if isinstance(parameters, dict) else None
    if isinstance(properties, dict) and properties:
        parameter_names = list(properties.keys())
        visible_parameters = ", ".join(parameter_names[:8])
        if len(parameter_names) > 8:
            visible_parameters += ", ..."
    else:
        visible_parameters = "arguments"
    description = str(function.get("description") or "").strip()
    if description:
        description = " " + description[:180]
    return f"- {name}({visible_parameters}): available function.{description}"


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
    pending_function_calls: list[dict[str, Any]] = []

    def flush_pending_function_calls() -> None:
        if not pending_function_calls:
            return
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": deepcopy(pending_function_calls),
            }
        )
        pending_function_calls.clear()

    for item in items:
        if not isinstance(item, dict):
            raise UnsupportedFeatureError("Each `input` item must be an object.")

        item_type = item.get("type")
        if item_type == "function_call_output":
            flush_pending_function_calls()
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
            flush_pending_function_calls()
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
            pending_function_calls.append(
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": stringify_value(item.get("arguments", {})),
                    },
                }
            )
            continue

        if item_type == "reasoning":
            continue

        role = item.get("role")
        if item_type == "message" or role:
            flush_pending_function_calls()
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

    flush_pending_function_calls()
    return messages


def build_input_items(input_value: Any) -> list[dict[str, Any]]:
    if input_value is None:
        return []
    if isinstance(input_value, str):
        items = [{"type": "message", "role": "user", "content": input_value}]
    elif isinstance(input_value, dict):
        items = [input_value]
    elif isinstance(input_value, list):
        items = input_value
    else:
        return []

    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        stored = deepcopy(item)
        stored.setdefault("id", make_id("item"))
        if "type" not in stored and "role" in stored:
            stored["type"] = "message"
        normalized.append(stored)
    return normalized


def input_contains_image(value: Any) -> bool:
    if isinstance(value, list):
        return any(input_contains_image(item) for item in value)
    if not isinstance(value, dict):
        return False
    if value.get("type") == "computer_call_output":
        return False
    part_type = value.get("type")
    if part_type in {"input_image", "image_url"}:
        return True
    for key in ("content", "input"):
        if key in value and input_contains_image(value[key]):
            return True
    return False


def extract_message_content(value: Any, source: str) -> str | list[dict[str, Any]]:
    if isinstance(value, list):
        parts = [convert_content_part(part, source) for part in value]
        if all(part["type"] == "text" for part in parts):
            texts = [str(part.get("text", "")) for part in parts if str(part.get("text", ""))]
            if any(text.startswith("[input_file ") for text in texts):
                return "\n".join(texts)
            return "".join(texts)
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
        return {"type": "text", "text": describe_input_file_part(part)}
    raise UnsupportedFeatureError(f"Unsupported content part type `{part_type}` in {source}.")


def describe_input_file_part(part: dict[str, Any]) -> str:
    filename = str(part.get("filename") or part.get("file_id") or "inline-file")
    file_data = part.get("file_data")
    if isinstance(file_data, str):
        decoded = decode_inline_text_file(file_data)
        if decoded is not None:
            return f"[input_file {filename}]\n{decoded}"
    return describe_file_like_part("input_file", part)


def decode_inline_text_file(file_data: str) -> str | None:
    payload = file_data.strip()
    if "," in payload and payload.lower().startswith("data:"):
        payload = payload.split(",", 1)[1]
    try:
        raw = base64.b64decode(payload, validate=True)
    except ValueError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    return raw.decode("utf-8", errors="replace")


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
    return [normalize_function_tool(tool)]


def normalize_function_tool(tool: dict[str, Any]) -> dict[str, Any]:
    if "function" in tool:
        raw_function = tool.get("function") or {}
        if not isinstance(raw_function, dict):
            raise UnsupportedFeatureError("Function tools need a valid `function` object.")
        name = raw_function.get("name")
        description = raw_function.get("description")
        parameters = raw_function.get("parameters")
    else:
        name = tool.get("name")
        description = tool.get("description")
        parameters = tool.get("parameters")

    if not name:
        raise UnsupportedFeatureError("Function tools need a `name`.")
    function: dict[str, Any] = {
        "name": str(name),
        "parameters": parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}},
    }
    if description:
        function["description"] = str(description)
    return {"type": "function", "function": function}


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
    hosted_output_items: list[dict[str, Any]] | None = None,
    hosted_annotations: list[dict[str, Any]] | None = None,
    protocol_report: ProtocolReport | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    choice = first_choice(upstream_response)
    message = choice.get("message") or {}
    assistant_text = normalize_assistant_text(message.get("content"))
    reasoning_content = normalize_reasoning_content(message)
    tool_calls = normalize_tool_calls(message.get("tool_calls"), payload=payload)
    if not tool_calls:
        assistant_text, tool_calls = extract_text_tool_call_markers(assistant_text, payload)
    if should_suppress_tool_preamble_text(payload, assistant_text, tool_calls):
        assistant_text = ""
    finish_reason = choice.get("finish_reason")
    usage = convert_usage(upstream_response.get("usage"))
    response = build_response_object(
        payload=payload,
        response_id=response_id,
        assistant_text=assistant_text,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
        prefix_output_items=hosted_output_items,
        annotations=hosted_annotations,
        protocol_report=protocol_report,
        usage=usage,
        finish_reason=finish_reason,
        created_at=upstream_response.get("created") or int(time.time()),
    )
    return response, build_history_output(assistant_text, tool_calls, reasoning_content=reasoning_content)


def get_upstream_assistant_text(upstream_response: dict[str, Any]) -> str:
    choice = first_choice(upstream_response)
    message = choice.get("message") or {}
    return normalize_assistant_text(message.get("content"))


def should_retry_tool_intent_response(payload: dict[str, Any], upstream_response: dict[str, Any]) -> bool:
    choice = first_choice(upstream_response)
    message = choice.get("message") or {}
    assistant_text = normalize_assistant_text(message.get("content"))
    raw_tool_calls = message.get("tool_calls")
    if isinstance(raw_tool_calls, list) and raw_tool_calls:
        return False
    return should_retry_tool_intent_text(payload, assistant_text, tool_calls=[])


def should_retry_tool_intent_text(
    payload: dict[str, Any],
    assistant_text: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> bool:
    if tool_calls:
        return False
    if not build_tool_schema_index(payload):
        return False
    if not assistant_text.strip():
        return True
    return looks_like_tool_intent_text(assistant_text)


def should_suppress_tool_preamble_text(
    payload: dict[str, Any],
    assistant_text: str,
    tool_calls: list[dict[str, Any]],
) -> bool:
    if not tool_calls:
        return False
    if not build_tool_schema_index(payload):
        return False
    return looks_like_tool_intent_text(assistant_text)


def looks_like_tool_intent_text(assistant_text: str) -> bool:
    text = assistant_text.strip()
    if not text or len(text) > TOOL_INTENT_TEXT_LIMIT:
        return False

    lower_text = text.lower()
    if "<tool_call>" in lower_text:
        return False
    if any(marker in lower_text for marker in TOOL_INTENT_CODE_MARKERS):
        return False
    if any(marker in lower_text for marker in TOOL_INTENT_DONE_MARKERS):
        return False

    return any(pattern.search(text) for pattern in TOOL_INTENT_PATTERNS)


def build_tool_intent_retry_payload_for_response(
    upstream_payload: dict[str, Any],
    upstream_response: dict[str, Any],
) -> dict[str, Any]:
    return build_tool_intent_retry_payload(upstream_payload, get_upstream_assistant_text(upstream_response))


def build_tool_intent_retry_payload(upstream_payload: dict[str, Any], assistant_text: str) -> dict[str, Any]:
    retry_payload = deepcopy(upstream_payload)
    raw_messages = retry_payload.get("messages")
    messages = deepcopy(raw_messages) if isinstance(raw_messages, list) else []

    replay_text = assistant_text.strip()
    if replay_text:
        messages.append({"role": "assistant", "content": replay_text[:TOOL_INTENT_REPLAY_LIMIT]})
    prompt = TOOL_INTENT_RETRY_PROMPT if replay_text else EMPTY_TOOL_TURN_RETRY_PROMPT
    messages.append({"role": "user", "content": prompt})
    retry_payload["messages"] = messages
    return retry_payload


def build_response_object(
    payload: dict[str, Any],
    response_id: str,
    assistant_text: str,
    tool_calls: list[dict[str, Any]],
    usage: dict[str, int] | None,
    finish_reason: str | None,
    created_at: int,
    message_id: str | None = None,
    reasoning_content: str = "",
    prefix_output_items: list[dict[str, Any]] | None = None,
    annotations: list[dict[str, Any]] | None = None,
    protocol_report: ProtocolReport | None = None,
) -> dict[str, Any]:
    output_items = build_output_items(
        assistant_text,
        tool_calls,
        message_id=message_id,
        reasoning_content=reasoning_content,
        prefix_output_items=prefix_output_items,
        annotations=annotations,
    )
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
        protocol_report=protocol_report,
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
    protocol_report: ProtocolReport | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = build_metadata_payload(payload, protocol_report)
    return {
        "id": response_id,
        "object": "response",
        "background": bool(payload.get("background", False)),
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
        "top_logprobs": 0,
        "top_p": payload.get("top_p", 1.0),
        "truncation": "disabled",
        "usage": usage,
        "user": payload.get("user"),
        "metadata": metadata,
    }


def build_output_items(
    assistant_text: str,
    tool_calls: list[dict[str, Any]],
    message_id: str | None = None,
    reasoning_content: str = "",
    prefix_output_items: list[dict[str, Any]] | None = None,
    annotations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = deepcopy(prefix_output_items or [])
    tool_calls = dedupe_tool_calls_for_execution(tool_calls)
    if reasoning_content:
        output.append(build_reasoning_item(reasoning_content))
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
                        "annotations": build_output_annotations(assistant_text, annotations),
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


def build_reasoning_item(reasoning_content: str, item_id: str | None = None, status: str = "completed") -> dict[str, Any]:
    return {
        "id": item_id or make_id("rs"),
        "type": "reasoning",
        "status": status,
        "summary": [
            {
                "type": "summary_text",
                "text": reasoning_content,
            }
        ],
    }


def build_output_annotations(assistant_text: str, annotations: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for annotation in annotations or []:
        item = deepcopy(annotation)
        item.setdefault("start_index", 0)
        item.setdefault("end_index", len(assistant_text))
        normalized.append(item)
    return normalized


def build_metadata_payload(payload: dict[str, Any], protocol_report: ProtocolReport | None) -> dict[str, Any]:
    metadata = deepcopy(payload.get("metadata") or {})
    if protocol_report and protocol_report.has_compatibility_notes:
        metadata.setdefault("response_proxy", {}).update(protocol_report.to_metadata())
    return metadata


def build_history_output(
    assistant_text: str,
    tool_calls: list[dict[str, Any]],
    reasoning_content: str = "",
) -> list[dict[str, Any]]:
    message: dict[str, Any] = {"role": "assistant", "content": assistant_text}
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    tool_calls = dedupe_tool_calls_for_execution(tool_calls)
    if tool_calls:
        message["tool_calls"] = compact_tool_calls_for_upstream(tool_calls)
    return [message]


def dedupe_tool_calls_for_execution(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys: list[tuple[str, str] | None] = []
    latest_index_by_key: dict[tuple[str, str], int] = {}
    for index, tool_call in enumerate(tool_calls):
        key = tool_call_write_target_key(tool_call)
        keys.append(key)
        if key is not None:
            latest_index_by_key[key] = index
    if not latest_index_by_key:
        return tool_calls
    return [
        tool_call
        for index, tool_call in enumerate(tool_calls)
        if keys[index] is None or latest_index_by_key.get(keys[index]) == index
    ]


def tool_call_write_target_key(tool_call: dict[str, Any]) -> tuple[str, str] | None:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return None
    name = str(function.get("name") or "")
    if name not in {"shell", "shell_command"}:
        return None
    arguments = parse_json_value(str(function.get("arguments") or ""))
    if not isinstance(arguments, dict):
        return None
    command_text = shell_command_text(arguments.get("command"))
    if not command_text:
        return None
    target = extract_powershell_write_target(command_text)
    if target is None:
        return None
    return name, target.replace("/", "\\").strip().lower()


def shell_command_text(command: Any) -> str:
    command_list = command_as_string_list(command)
    if command_list is not None:
        command_index = find_powershell_command_index(command_list)
        if command_index is not None and command_index + 1 < len(command_list):
            return command_list[command_index + 1]
        return " ".join(command_list)
    if isinstance(command, str):
        return command
    return ""


def find_powershell_command_index(command: list[str]) -> int | None:
    if not command:
        return None
    executable = command[0].replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    if executable not in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        return None
    for index, item in enumerate(command[1:8], start=1):
        if item.lower() in {"-command", "-c"}:
            return index
    return None


def extract_powershell_write_target(command_text: str) -> str | None:
    if "Copy-Item" not in command_text and "WriteAllText" not in command_text:
        return None
    single_quoted = re.search(r"\$path\s*=\s*'((?:''|[^'])*)'", command_text)
    if single_quoted:
        return single_quoted.group(1).replace("''", "'")
    encoded_path = re.search(
        r"\$path\s*=\s*\[System\.Text\.Encoding\]::UTF8\.GetString"
        r"\(\[System\.Convert\]::FromBase64String\('([A-Za-z0-9+/=]+)'\)\)",
        command_text,
    )
    if encoded_path:
        return decode_base64_utf8(encoded_path.group(1))
    double_quoted = re.search(r'\$path\s*=\s*"((?:`"|[^"])*)"', command_text)
    if double_quoted:
        return double_quoted.group(1).replace('`"', '"')
    direct_single = re.search(r"WriteAllText\(\s*'((?:''|[^'])*)'", command_text, flags=re.IGNORECASE)
    if direct_single:
        return direct_single.group(1).replace("''", "'")
    direct_double = re.search(r'WriteAllText\(\s*"((?:`"|[^"])*)"', command_text, flags=re.IGNORECASE)
    if direct_double:
        return direct_double.group(1).replace('`"', '"')
    return None


def compact_chat_messages_for_upstream(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for message in messages:
        next_message = deepcopy(message)
        if next_message.get("role") == "assistant" and isinstance(next_message.get("content"), str):
            next_message["content"] = compact_assistant_content_for_upstream(next_message["content"])
        if next_message.get("role") == "assistant" and next_message.get("tool_calls"):
            next_message["tool_calls"] = compact_tool_calls_for_upstream(next_message.get("tool_calls"))
        if next_message.get("role") == "tool" and isinstance(next_message.get("content"), str):
            next_message["content"] = compact_tool_output_for_upstream(next_message["content"])
        compacted.append(next_message)
    return compacted


def compact_assistant_content_for_upstream(content: str) -> str:
    if len(content) <= MAX_REPLAY_ASSISTANT_CONTENT_CHARS:
        return content

    head_chars = 1_000
    tail_chars = 600
    return (
        "[Responses Proxy compacted oversized assistant content before upstream replay; "
        f"original_content_chars={len(content)}. Omitted prior generated text/code to keep the agent responsive.]\n"
        + content[:head_chars]
        + "\n...[compacted prior assistant content]...\n"
        + content[-tail_chars:]
    )


def compact_tool_calls_for_upstream(tool_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(tool_calls, list):
        return []
    compacted: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        next_tool_call = deepcopy(tool_call)
        function = next_tool_call.get("function")
        if isinstance(function, dict):
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                function["arguments"] = compact_tool_arguments_for_upstream(arguments)
        compacted.append(next_tool_call)
    return compacted


def compact_tool_arguments_for_upstream(arguments: str) -> str:
    if len(arguments) <= MAX_REPLAY_TOOL_ARGUMENT_CHARS:
        return arguments

    parsed = parse_json_value(arguments)
    if isinstance(parsed, dict):
        compacted = deepcopy(parsed)
        if "command" in compacted:
            compacted["command"] = build_compacted_history_shell_command(compacted["command"], len(arguments))
        else:
            compacted["_responses_proxy_compacted"] = True
            compacted["_responses_proxy_original_argument_chars"] = len(arguments)
        return json.dumps(compacted, ensure_ascii=False)

    return json.dumps(
        {
            "_responses_proxy_compacted": True,
            "_responses_proxy_original_argument_chars": len(arguments),
            "summary": arguments[:500],
        },
        ensure_ascii=False,
    )


def build_compacted_history_shell_command(command: Any, original_argument_chars: int) -> Any:
    message = (
        "[Responses Proxy compacted oversized shell command history placeholder; "
        f"original_argument_chars={original_argument_chars}. Do not repeat this placeholder command; "
        "use the following tool output as the execution result. Responses Proxy history placeholder.]"
    )
    if isinstance(command, list):
        return [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"Write-Output {powershell_single_quoted(message)}",
        ]
    return subprocess.list2cmdline(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"Write-Output {powershell_single_quoted(message)}",
        ]
    )


def compact_tool_output_for_upstream(content: str) -> str:
    if len(content) <= MAX_REPLAY_TOOL_OUTPUT_CHARS:
        return content
    if looks_like_tool_argument_parse_error(content):
        return (
            "[Responses Proxy compacted oversized tool output before replaying history; "
            f"original_output_chars={len(content)}]\n"
            "Tool execution did not run because the function arguments were malformed. "
            "The oversized command payload was omitted from replay history; retry with valid tool arguments."
        )
    head_chars = 700
    tail_chars = 1_200
    return (
        "[Responses Proxy compacted oversized tool output before replaying history; "
        f"original_output_chars={len(content)}]\n"
        + content[:head_chars]
        + "\n...[compacted middle of tool output]...\n"
        + content[-tail_chars:]
    )


def looks_like_tool_argument_parse_error(content: str) -> bool:
    lowered = content.lower()
    return (
        "failed to parse function arguments" in lowered
        or "invalid type:" in lowered and "expected" in lowered and "function" in lowered
    )


def append_conversation_history(
    history: list[dict[str, Any]],
    input_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not history:
        return deepcopy(input_messages)
    if not input_messages:
        return deepcopy(history)

    overlap_length = find_history_suffix_input_prefix_overlap(history, input_messages)
    if overlap_length == 0:
        merged = deepcopy(history) + deepcopy(input_messages)
    else:
        merged = deepcopy(history[:-overlap_length])
        for offset in range(overlap_length):
            merged.append(merge_history_message(history[-overlap_length + offset], input_messages[offset]))
        merged.extend(deepcopy(input_messages[overlap_length:]))
    return restore_reasoning_content(history, merged)


def find_history_suffix_input_prefix_overlap(
    history: list[dict[str, Any]],
    input_messages: list[dict[str, Any]],
) -> int:
    max_overlap = min(len(history), len(input_messages))
    for overlap_length in range(max_overlap, 0, -1):
        if all(
            messages_match_for_history(history[-overlap_length + offset], input_messages[offset])
            for offset in range(overlap_length)
        ):
            return overlap_length
    return 0


def sanitize_chat_message_sequence(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    index = 0
    while index < len(messages):
        message = deepcopy(messages[index])
        role = message.get("role")

        if role == "tool":
            index += 1
            continue

        if role == "assistant" and message.get("tool_calls"):
            expected_ids = tool_call_ids(message.get("tool_calls"))
            if not expected_ids:
                message.pop("tool_calls", None)
                if message.get("content"):
                    sanitized.append(message)
                index += 1
                continue

            tool_messages: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            scan_index = index + 1
            while scan_index < len(messages) and messages[scan_index].get("role") == "tool":
                tool_message = deepcopy(messages[scan_index])
                tool_call_id = str(tool_message.get("tool_call_id") or tool_message.get("call_id") or "")
                if tool_call_id in expected_ids and tool_call_id not in seen_ids:
                    tool_messages.append(tool_message)
                    seen_ids.add(tool_call_id)
                scan_index += 1

            if seen_ids == set(expected_ids):
                sanitized.append(message)
                sanitized.extend(tool_messages)
            else:
                message.pop("tool_calls", None)
                if message.get("content"):
                    sanitized.append(message)
            index = scan_index
            continue

        sanitized.append(message)
        index += 1

    return sanitized


def ensure_tool_call_reasoning_content(
    messages: list[dict[str, Any]],
    settings: Settings,
) -> list[dict[str, Any]]:
    if not requires_tool_call_reasoning_content(settings):
        return deepcopy(messages)

    restored: list[dict[str, Any]] = []
    for message in messages:
        next_message = deepcopy(message)
        if (
            next_message.get("role") == "assistant"
            and next_message.get("tool_calls")
            and not normalize_reasoning_value(next_message.get("reasoning_content"))
        ):
            next_message["reasoning_content"] = SYNTHETIC_TOOL_CALL_REASONING_CONTENT
        restored.append(next_message)
    return restored


def requires_tool_call_reasoning_content(settings: Settings) -> bool:
    marker = f"{settings.upstream_base_url} {settings.upstream_model or ''}".lower()
    return any(provider in marker for provider in ("mimo", "xiaomi", "deepseek"))


def hoist_system_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    system_messages = [deepcopy(message) for message in messages if message.get("role") == "system"]
    non_system_messages = [deepcopy(message) for message in messages if message.get("role") != "system"]
    return system_messages + non_system_messages


def tool_call_ids(tool_calls: Any) -> list[str]:
    if not isinstance(tool_calls, list):
        return []
    ids: list[str] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        tool_call_id = tool_call.get("id") or tool_call.get("call_id")
        if tool_call_id:
            ids.append(str(tool_call_id))
    return ids


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


def normalize_reasoning_content(message: dict[str, Any]) -> str:
    for key in ("reasoning_content", "reasoning", "thinking", "reasoning_summary"):
        value = message.get(key)
        text = normalize_reasoning_value(value)
        if text:
            return text
    return ""


def normalize_reasoning_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return normalize_assistant_text(value)
    if isinstance(value, dict):
        for key in ("content", "text", "summary", "reasoning_content"):
            text = normalize_reasoning_value(value.get(key))
            if text:
                return text
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def normalize_stream_reasoning_delta(delta: dict[str, Any]) -> str:
    for key in ("reasoning_content", "reasoning", "thinking"):
        text = normalize_reasoning_value(delta.get(key))
        if text:
            return text
    return ""


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


def extract_text_tool_call_markers(text: str, payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    if not text or "<tool_call>" not in text:
        return text, []

    tool_schemas = build_tool_schema_index(payload)
    if not tool_schemas:
        return text, []

    tool_calls: list[dict[str, Any]] = []
    seen_tool_calls: set[tuple[str, str]] = set()

    def append_marker_tool_call(parsed: dict[str, Any] | None) -> None:
        if parsed is None:
            return
        tool_call = build_marker_tool_call(parsed, tool_schemas)
        if not tool_call:
            return
        function = tool_call.get("function") or {}
        signature = (str(function.get("name", "")), str(function.get("arguments", "")))
        if signature in seen_tool_calls:
            return
        seen_tool_calls.add(signature)
        tool_calls.append(tool_call)

    for match in TOOL_CALL_MARKER_RE.finditer(text):
        parsed = parse_json_value(match.group(1))
        if not isinstance(parsed, dict):
            continue
        append_marker_tool_call(parsed)

    for match in TOOL_CALL_XML_RE.finditer(text):
        parsed = {
            "name": match.group(1),
            "arguments": parse_xml_tool_call_arguments(match.group(2)),
        }
        append_marker_tool_call(parsed)

    for match in TOOL_CALL_BLOCK_RE.finditer(text):
        block = match.group(1)
        if block.lstrip().startswith("{"):
            if isinstance(parse_json_value(block), dict):
                continue
            parsed = parse_jsonish_tool_call_block(block)
            append_marker_tool_call(parsed)
            continue
        parsed = parse_function_tag_tool_call_block(block)
        if parsed is None:
            parsed = parse_relaxed_tool_call_block(block)
        if parsed is None:
            parsed = parse_tag_named_tool_call_block(block)
        append_marker_tool_call(parsed)

    unclosed_block = extract_unclosed_tool_call_block(text)
    if unclosed_block is not None:
        parsed = parse_function_tag_tool_call_block(unclosed_block)
        if parsed is None:
            parsed = parse_relaxed_tool_call_block(unclosed_block)
        if parsed is None:
            parsed = parse_tag_named_tool_call_block(unclosed_block)
        if parsed is None and unclosed_block.lstrip().startswith("{"):
            parsed = parse_jsonish_tool_call_block(unclosed_block)
        append_marker_tool_call(parsed)

    if not tool_calls:
        synthesized_tool_call = synthesize_html_write_file_tool_call(text, payload, tool_schemas)
        if synthesized_tool_call is not None:
            tool_calls.append(synthesized_tool_call)
            return "", tool_calls
        if should_preserve_unparsed_tool_marker_text(text):
            return text, []
        return (
            "Responses Proxy could not parse malformed tool call from upstream model. "
            "The malformed marker was suppressed to avoid a retry loop; retry with a valid tool call.",
            [],
        )
    return "", tool_calls


def synthesize_html_write_file_tool_call(
    text: str,
    payload: dict[str, Any],
    tool_schemas: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    html_document = extract_html_document_from_text(text)
    if html_document is None:
        return None
    return build_marker_tool_call(
        {
            "name": "write_file",
            "arguments": {
                "path": infer_html_output_filename(payload),
                "content": html_document,
            },
        },
        tool_schemas,
    )


def extract_html_document_from_text(text: str) -> str | None:
    lower = text.lower()
    start_candidates = [index for index in (lower.find("<!doctype html"), lower.find("<html")) if index >= 0]
    if not start_candidates:
        return None
    start = min(start_candidates)
    end = lower.rfind("</html>")
    if end >= start:
        end += len("</html>")
        html_text = text[start:end]
    else:
        html_text = text[start:]
        for marker in ("</tool_call>", "```"):
            marker_index = html_text.find(marker)
            if marker_index >= 0:
                html_text = html_text[:marker_index]
                break
    html_text = html_text.strip()
    if "\\n" in html_text and "\n" not in html_text[:200]:
        html_text = decode_jsonish_string(html_text)
    html_text = strip_markdown_code_fence(html_text)
    if "<html" not in html_text.lower() and "<!doctype html" not in html_text.lower():
        return None
    return html_text


def strip_markdown_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def infer_html_output_filename(payload: dict[str, Any]) -> str:
    prompt_text = extract_text(payload.get("input"), source="input").lower()
    if "消息同步" in prompt_text:
        return "message-sync-board.html"
    if "看板" in prompt_text or "kanban" in prompt_text:
        return "kanban.html"
    if "api" in prompt_text or "中转" in prompt_text:
        return "api-relay.html"
    return "index.html"


def build_marker_tool_call(
    parsed: dict[str, Any],
    tool_schemas: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    name = parsed.get("name")
    if not isinstance(name, str) or not name:
        function = parsed.get("function")
        if isinstance(function, dict):
            name = function.get("name")
    if not isinstance(name, str):
        return None

    arguments = parsed.get("arguments", {})
    if not isinstance(arguments, str):
        arguments = stringify_value(arguments)

    translated = translate_hallucinated_write_file(name, arguments, tool_schemas)
    if translated is None:
        translated = translate_hallucinated_apply_patch(name, arguments, tool_schemas)
    if translated is None:
        translated = translate_hallucinated_read_file(name, arguments, tool_schemas)
    if translated is not None:
        translated_name, translated_arguments = translated
        return {
            "id": str(parsed.get("id") or parsed.get("call_id") or make_id("call")),
            "type": "function",
            "function": {
                "name": translated_name,
                "arguments": translated_arguments,
            },
        }

    name = resolve_tool_schema_name(name, tool_schemas)
    if name is None:
        return None

    normalized_name, normalized_arguments = normalize_tool_call_output(name, arguments, tool_schemas)
    return {
        "id": str(parsed.get("id") or parsed.get("call_id") or make_id("call")),
        "type": "function",
        "function": {
            "name": normalized_name,
            "arguments": normalized_arguments,
        },
    }


def resolve_tool_schema_name(name: str, tool_schemas: dict[str, dict[str, Any]]) -> str | None:
    if name in tool_schemas:
        return name
    aliases = {
        "shell": ("shell_command",),
        "shell_command": ("shell",),
    }
    for alias in aliases.get(name, ()):
        if alias in tool_schemas:
            return alias
    return None


def parse_xml_tool_call_arguments(content: str) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    closed_parameter_spans: list[tuple[int, int]] = []
    for match in TOOL_CALL_XML_PARAMETER_RE.finditer(content):
        key = match.group(1)
        value = html.unescape(match.group(2)).strip()
        arguments[key] = parse_relaxed_parameter_value(value)
        closed_parameter_spans.append(match.span())
    for key, value in iter_unclosed_parameter_values(
        content,
        TOOL_CALL_PARAMETER_START_RE,
        closed_parameter_spans,
        ignored_keys={"function", "tool_call"},
    ):
        arguments[key] = parse_relaxed_parameter_value(value)
    if arguments:
        return arguments

    closed_simple_spans: list[tuple[int, int]] = []
    for match in TOOL_CALL_SIMPLE_PARAMETER_RE.finditer(content):
        key = match.group(1)
        if key in {"function", "tool_call"}:
            continue
        value = html.unescape(match.group(2)).strip()
        arguments[key] = parse_relaxed_parameter_value(value)
        closed_simple_spans.append(match.span())
    for key, value in iter_unclosed_parameter_values(
        content,
        TOOL_CALL_SIMPLE_PARAMETER_START_RE,
        closed_simple_spans,
        ignored_keys={"function", "tool_call"},
    ):
        arguments[key] = parse_relaxed_parameter_value(value)
    if arguments:
        return arguments
    return arguments


def span_contains_position(spans: list[tuple[int, int]], position: int) -> bool:
    return any(start <= position < end for start, end in spans)


def iter_unclosed_parameter_values(
    content: str,
    start_pattern: re.Pattern[str],
    closed_spans: list[tuple[int, int]],
    *,
    ignored_keys: set[str],
) -> list[tuple[str, str]]:
    matches = [
        match
        for match in start_pattern.finditer(content)
        if not span_contains_position(closed_spans, match.start()) and match.group(1) not in ignored_keys
    ]
    values: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        key = match.group(1)
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        value = strip_trailing_tool_tags(html.unescape(content[match.end() : value_end])).strip()
        values.append((key, value))
    return values


def strip_trailing_tool_tags(value: str) -> str:
    stripped = value.strip()
    while True:
        updated = re.sub(r"</(?:parameter|function|tool_call)>\s*$", "", stripped, flags=re.DOTALL).strip()
        if updated == stripped:
            return stripped
        stripped = updated


def extract_unclosed_tool_call_block(text: str) -> str | None:
    start = text.rfind("<tool_call>")
    if start < 0:
        return None
    end = text.rfind("</tool_call>")
    if end > start:
        return None
    return text[start + len("<tool_call>") :]


def should_preserve_unparsed_tool_marker_text(text: str) -> bool:
    stripped = text.strip()
    if stripped.startswith("<tool_call>") or stripped.startswith("```"):
        return False
    if "<function=" in stripped or "<parameter=" in stripped:
        return False
    if len(stripped) > 3000:
        return False
    explanatory_markers = (
        "报错",
        "错误",
        "意思",
        "格式",
        "示例",
        "合法",
        "invalid",
        "malformed",
    )
    lower = stripped.lower()
    return any(marker in lower for marker in explanatory_markers)


def parse_jsonish_tool_call_block(block: str) -> dict[str, Any] | None:
    stripped = strip_trailing_tool_tags(block).strip()
    if not stripped.startswith("{"):
        return None

    name = extract_jsonish_string_field(stripped, "name")
    function_name = extract_jsonish_string_field(stripped, "function")
    if not name and function_name:
        name = function_name
    path = extract_jsonish_string_field(stripped, "path")
    content = extract_jsonish_tail_string_field(stripped, "content")
    if path is not None and content is not None:
        return {"name": name or "write_file", "arguments": {"path": path, "content": content}}

    command = extract_jsonish_command_field(stripped)
    if command is not None:
        return {"name": name or "shell_command", "arguments": {"command": command}}
    return None


def extract_jsonish_string_field(text: str, field: str) -> str | None:
    pattern = rf'"{re.escape(field)}"\s*:\s*"((?:\\.|[^"\\])*)"'
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return None
    return decode_jsonish_string(match.group(1))


def extract_jsonish_tail_string_field(text: str, field: str) -> str | None:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"', text, flags=re.DOTALL)
    if not match:
        return None
    tail = text[match.end() :]
    last_quote = tail.rfind('"')
    if last_quote >= 0:
        value = tail[:last_quote]
    else:
        value = tail.rstrip().rstrip("}")
    value = value.rstrip()
    while value.endswith("}"):
        value = value[:-1].rstrip()
    return decode_jsonish_string(value)


def extract_jsonish_command_field(text: str) -> Any | None:
    match = re.search(r'"command"\s*:', text, flags=re.DOTALL)
    if not match:
        return None
    value = extract_balanced_jsonish_value(text, match.end())
    if value is None:
        value = text[match.end() :].strip().rstrip("}")
    parsed = parse_json_value(value)
    if parsed is not None:
        return parsed
    relaxed = parse_relaxed_string_array(value)
    if relaxed is not None:
        return relaxed
    if value.startswith('"') and value.endswith('"'):
        return decode_jsonish_string(value[1:-1])
    return decode_jsonish_string(value.strip().strip('"'))


def extract_balanced_jsonish_value(text: str, start: int) -> str | None:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text):
        return None
    opener = text[index]
    pairs = {"[": "]", "{": "}"}
    if opener not in pairs:
        quote = text[index]
        if quote not in {'"', "'"}:
            return None
        end = find_unescaped_quote(text, quote, start=index + 1)
        if end is None:
            return None
        return text[index : end + 1]

    closer = pairs[opener]
    depth = 0
    quote: str | None = None
    escaped = False
    for position in range(index, len(text)):
        char = text[position]
        if quote:
            if char == "\\" and not escaped:
                escaped = True
                continue
            if char == quote and not escaped:
                quote = None
            escaped = False
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[index : position + 1]
    return None


def decode_jsonish_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except ValueError:
        return (
            value.replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\'", "'")
            .replace("\\\\", "\\")
        )


def parse_function_tag_tool_call_block(content: str) -> dict[str, Any] | None:
    match = TOOL_CALL_FUNCTION_TAG_RE.match(strip_trailing_tool_tags(content))
    if not match:
        return None
    return {
        "name": match.group(1),
        "arguments": parse_xml_tool_call_arguments(match.group(2)),
    }


def parse_relaxed_tool_call_block(content: str) -> dict[str, Any] | None:
    match = TOOL_CALL_RELAXED_FUNCTION_RE.search(strip_trailing_tool_tags(content))
    if not match:
        return None
    return {
        "name": match.group(1),
        "arguments": parse_xml_tool_call_arguments(match.group(2)),
    }


def parse_tag_named_tool_call_block(content: str) -> dict[str, Any] | None:
    stripped = strip_trailing_tool_tags(content)
    match = TOOL_CALL_TAG_NAMED_RE.match(stripped)
    if not match:
        match = TOOL_CALL_TAG_NAMED_RELAXED_RE.match(stripped)
    if not match:
        return None
    return {
        "name": match.group(1),
        "arguments": parse_xml_tool_call_arguments(match.group(2)),
    }


def parse_relaxed_parameter_value(value: str) -> Any:
    parsed = parse_json_value(value)
    if parsed is not None:
        return parsed
    relaxed_array = parse_relaxed_string_array(value)
    if relaxed_array is not None:
        return relaxed_array
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return value


def parse_relaxed_string_array(value: str) -> list[str] | None:
    stripped = value.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return None
    items = re.findall(r'"([^"]*)"|\'([^\']*)\'', stripped[1:-1])
    if not items:
        return None
    return [double_quoted if double_quoted else single_quoted for double_quoted, single_quoted in items]


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
    translated = translate_hallucinated_write_file(tool_name, arguments, tool_schemas)
    if translated is None:
        translated = translate_hallucinated_apply_patch(tool_name, arguments, tool_schemas)
    if translated is None:
        translated = translate_hallucinated_read_file(tool_name, arguments, tool_schemas)
    if translated is not None:
        return translated

    resolved_tool_name = resolve_tool_schema_name(tool_name, tool_schemas) or tool_name
    schema = effective_tool_parameters_schema(resolved_tool_name, tool_schemas)
    if not isinstance(schema, dict):
        return resolved_tool_name, arguments

    parsed_arguments = parse_json_value(arguments)
    if parsed_arguments is None:
        return resolved_tool_name, arguments

    original_arguments = deepcopy(parsed_arguments)
    if is_shell_tool_name(resolved_tool_name, tool_schemas):
        parsed_arguments = normalize_shell_tool_arguments(parsed_arguments)

    coerced_arguments = coerce_value_to_schema(parsed_arguments, schema)
    if coerced_arguments == original_arguments and resolved_tool_name == tool_name:
        return resolved_tool_name, arguments
    return resolved_tool_name, json.dumps(coerced_arguments, ensure_ascii=False)


def effective_tool_parameters_schema(
    tool_name: str,
    tool_schemas: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    schema = (tool_schemas.get(tool_name) or {}).get("parameters")
    if not isinstance(schema, dict):
        if not is_shell_tool_name(tool_name, tool_schemas):
            return None
        schema = {"type": "object", "properties": {}}
    if not is_shell_tool_name(tool_name, tool_schemas):
        return schema

    effective_schema = deepcopy(schema)
    effective_schema.setdefault("type", "object")
    properties = effective_schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
        effective_schema["properties"] = properties
    if "command" not in properties:
        properties["command"] = default_shell_command_schema(tool_name)
    return effective_schema


def default_shell_command_schema(tool_name: str) -> dict[str, Any]:
    if tool_name == "shell":
        return {"type": "array", "items": {"type": "string"}}
    return {"type": "string"}


def parse_json_value(value: str) -> Any | None:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def is_shell_tool_name(tool_name: str, tool_schemas: dict[str, dict[str, Any]]) -> bool:
    if tool_name in {"shell", "shell_command"}:
        return True
    schema = (tool_schemas.get(tool_name) or {}).get("parameters")
    if not isinstance(schema, dict):
        return False
    properties = schema.get("properties")
    return "shell" in tool_name.lower() and isinstance(properties, dict) and "command" in properties


def normalize_shell_tool_arguments(arguments: Any) -> Any:
    if not isinstance(arguments, dict) or "command" not in arguments:
        return arguments

    command = arguments.get("command")
    normalized_command = normalize_shell_command(command)
    if normalized_command == command:
        return arguments

    normalized_arguments = deepcopy(arguments)
    normalized_arguments["command"] = normalized_command
    return normalized_arguments


def normalize_shell_command(command: Any) -> Any:
    command_list = command_as_string_list(command)
    if command_list is None:
        return command

    placeholder_command = rewrite_compacted_history_placeholder_command(command_list)
    if placeholder_command is not None:
        return placeholder_command

    powershell_write_command = rewrite_powershell_inline_write_command(command_list)
    if powershell_write_command is not None:
        return powershell_write_command

    nested_python_command = rewrite_powershell_nested_python_dash_c_command(command_list)
    if nested_python_command is not None:
        return nested_python_command

    apply_patch_command = rewrite_apply_patch_shell_command(command_list)
    if apply_patch_command is not None:
        return apply_patch_command

    mkdir_command = rewrite_mkdir_p_command(command_list)
    if mkdir_command is not None:
        return mkdir_command

    python_command = rewrite_python_dash_c_command(command_list)
    if python_command is not None:
        return python_command

    bounded_read_command = rewrite_unbounded_file_read_command(command_list)
    if bounded_read_command is not None:
        return bounded_read_command

    if isinstance(command, str):
        return command_list
    return command


def rewrite_compacted_history_placeholder_command(command: list[str]) -> list[str] | None:
    command_text = " ".join(command)
    if "Responses Proxy compacted oversized shell command history placeholder" not in command_text:
        return None
    message = (
        "Responses Proxy blocked execution of a compacted history placeholder command. "
        "This command was only a replay summary for the upstream model; retry with the real intended action."
    )
    return [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        f"Write-Error {powershell_single_quoted(message)}; exit 64",
    ]


def rewrite_powershell_inline_write_command(command: list[str]) -> list[str] | None:
    command_index = find_powershell_command_index(command)
    if command_index is None or command_index + 1 >= len(command):
        return None

    script = command[command_index + 1]
    extracted = extract_powershell_inline_write(script)
    if extracted is None:
        return None

    path, content = extracted
    return [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        build_powershell_write_file_command(path, content),
    ]


def extract_powershell_inline_write(script: str) -> tuple[str, str] | None:
    if "@'" not in script and '@"' not in script:
        return None
    if not re.search(r"\b(?:Set-Content|Out-File|WriteAllText)\b", script, flags=re.IGNORECASE):
        return None

    content = extract_powershell_here_string_content(script)
    if content is None:
        return None

    path = extract_powershell_write_target(script) or extract_powershell_set_content_target(script)
    if not path:
        return None
    return path, content


def extract_powershell_here_string_content(script: str) -> str | None:
    assignment = re.search(
        r"\$[A-Za-z_][A-Za-z0-9_]*\s*=\s*@(?P<quote>['\"])(?P<content>.*?)(?P=quote)@",
        script,
        flags=re.DOTALL,
    )
    if assignment:
        return assignment.group("content")

    piped = re.search(
        r"@(?P<quote>['\"])(?P<content>.*?)(?P=quote)@\s*\|\s*(?:Set-Content|Out-File)\b",
        script,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if piped:
        return piped.group("content")
    return None


def extract_powershell_set_content_target(script: str) -> str | None:
    patterns = (
        r"\b(?:Set-Content|Out-File)\b.*?-(?:LiteralPath|FilePath|Path)\s+'((?:''|[^'])*)'",
        r'\b(?:Set-Content|Out-File)\b.*?-(?:LiteralPath|FilePath|Path)\s+"((?:`"|[^"])*)"',
    )
    for pattern in patterns:
        match = re.search(pattern, script, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        value = match.group(1)
        if "'" in pattern:
            return value.replace("''", "'")
        return value.replace('`"', '"')
    return None


def rewrite_powershell_nested_python_dash_c_command(command: list[str]) -> list[str] | None:
    command_index = find_powershell_command_index(command)
    if command_index is None or command_index + 1 >= len(command):
        return None

    script = command[command_index + 1]
    parsed = extract_nested_python_dash_c_script(script)
    if parsed is None:
        return None

    executable, python_script = parsed
    if not should_rewrite_inline_python_script(python_script):
        return None
    runner = build_python_temp_script_runner_command_list(executable, python_script, [])
    if len(subprocess.list2cmdline(runner)) > MAX_INLINE_PYTHON_RUNNER_COMMAND_CHARS:
        return build_oversized_inline_python_command_list(len(python_script), len(subprocess.list2cmdline(runner)))
    return runner


def extract_nested_python_dash_c_script(script: str) -> tuple[str, str] | None:
    match = re.search(
        r"(?i)\b(?P<executable>(?:python3?|py)(?:\.exe)?)\s+-c\s+",
        script,
        flags=re.DOTALL,
    )
    if not match:
        return None

    rest = script[match.end() :].lstrip()
    if rest.startswith("@'") or rest.startswith('@"'):
        quote = rest[1]
        closing = f"{quote}@"
        closing_index = rest.find(closing, 2)
        if closing_index < 0:
            return None
        return match.group("executable"), rest[2:closing_index]
    if not rest or rest[0] not in {"'", '"'}:
        return None

    quote = rest[0]
    closing_index = find_unescaped_quote(rest, quote, start=1)
    if closing_index is None:
        return None
    return match.group("executable"), rest[1:closing_index]


def rewrite_unbounded_file_read_command(command: list[str]) -> list[str] | None:
    command_index = find_powershell_command_index(command)
    if command_index is None or command_index + 1 >= len(command):
        return None

    script = command[command_index + 1]
    if "Get-Content" not in script or "-Raw" not in script:
        return None
    if re.search(r"\(\s*Get-Content\b.*?-Raw.*?\)\s*\.\s*Length", script, flags=re.IGNORECASE | re.DOTALL):
        return None

    path = extract_get_content_path(script)
    if path:
        bounded_script = (
            "$ErrorActionPreference = 'Stop'; "
            "Write-Output '[Responses Proxy truncated full-file read to "
            f"{MAX_FULL_FILE_READ_LINES} lines for speed]'; "
            f"Get-Content -LiteralPath {powershell_string_expr(path)} -TotalCount {MAX_FULL_FILE_READ_LINES}"
        )
    else:
        bounded_script = re.sub(r"\s+-Raw\b", "", script, flags=re.IGNORECASE)
        bounded_script += (
            "; Write-Output '[Responses Proxy removed -Raw from full-file read for speed; "
            f"prefer -TotalCount {MAX_FULL_FILE_READ_LINES} for verification]'"
        )

    updated = list(command)
    updated[command_index + 1] = bounded_script
    return updated


def extract_get_content_path(script: str) -> str | None:
    patterns = (
        r"Get-Content\b.*?-(?:LiteralPath|Path)\s+'((?:''|[^'])*)'",
        r'Get-Content\b.*?-(?:LiteralPath|Path)\s+"((?:`"|[^"])*)"',
        r"Get-Content\s+'((?:''|[^'])*)'",
        r'Get-Content\s+"((?:`"|[^"])*)"',
    )
    for pattern in patterns:
        match = re.search(pattern, script, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        value = match.group(1)
        if "'" in pattern:
            return value.replace("''", "'")
        return value.replace('`"', '"')
    return None


def command_as_string_list(command: Any) -> list[str] | None:
    if isinstance(command, list) and all(isinstance(item, str) for item in command):
        return command
    if not isinstance(command, str):
        return None

    parsed = parse_json_value(command)
    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        return parsed

    relaxed = parse_relaxed_string_array(command)
    if relaxed is not None:
        return relaxed

    python_dash_c = parse_python_dash_c_command_string(command)
    if python_dash_c is not None:
        return python_dash_c

    powershell_command = parse_powershell_command_string(command)
    if powershell_command is not None:
        return powershell_command
    return None


def parse_powershell_command_string(command: str) -> list[str] | None:
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not parts:
        return None

    executable = parts[0].replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    if executable not in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        return None

    command_index: int | None = None
    for index, part in enumerate(parts[1:8], start=1):
        if part.lower() in {"-command", "-c"}:
            command_index = index
            break
    if command_index is None or command_index + 1 >= len(parts):
        return None

    script = " ".join(parts[command_index + 1 :])
    return parts[: command_index + 1] + [script]


def parse_python_dash_c_command_string(command: str) -> list[str] | None:
    match = re.match(
        r"^\s*(?P<executable>(?:python3?|py)(?:\.exe)?)\s+-c\s+(?P<rest>.+?)\s*$",
        command,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None

    rest = match.group("rest")
    if not rest:
        return None

    script: str
    suffix = ""
    if rest[0] in {"'", '"'}:
        quote = rest[0]
        closing_index = find_unescaped_quote(rest, quote, start=1)
        if closing_index is None:
            return None
        script = rest[1:closing_index]
        suffix = rest[closing_index + 1 :].strip()
    else:
        script = rest

    args = [match.group("executable"), "-c", script]
    if suffix:
        try:
            args.extend(shlex.split(suffix, posix=not platform.system().lower().startswith("win")))
        except ValueError:
            return None
    return args


def find_unescaped_quote(value: str, quote: str, *, start: int) -> int | None:
    escaped = False
    found: int | None = None
    for index in range(start, len(value)):
        char = value[index]
        if char == "\\" and not escaped:
            escaped = True
            continue
        if char == quote and not escaped:
            found = index
        escaped = False
    return found


def rewrite_apply_patch_shell_command(command: list[str]) -> list[str] | None:
    if len(command) < 2:
        return None
    if command[0].replace("\\", "/").rsplit("/", 1)[-1].strip().lower() != "apply_patch":
        return None

    patch_text = command[1]
    simple_write = parse_simple_apply_patch_write(patch_text)
    if simple_write is None:
        return None

    path, content = simple_write
    return [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        build_powershell_write_file_command(path, content),
    ]


def rewrite_mkdir_p_command(command: list[str]) -> list[str] | None:
    if not platform.system().lower().startswith("win"):
        return None
    if len(command) != 3:
        return None
    if command[0].strip().lower() not in {"mkdir", "md"} or command[1].strip() != "-p":
        return None
    return [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        build_powershell_create_directory_command(command[2]),
    ]


def rewrite_python_dash_c_command(command: list[str]) -> list[str] | None:
    dash_c_index = find_python_dash_c_index(command)
    if dash_c_index is None or dash_c_index + 1 >= len(command):
        return None

    script = command[dash_c_index + 1]
    if not should_rewrite_inline_python_script(script):
        return None

    executable = command[0]
    extra_args = command[dash_c_index + 2 :]
    runner = build_python_temp_script_runner_command_list(executable, script, extra_args)
    runner_length = len(subprocess.list2cmdline(runner))
    if runner_length > MAX_INLINE_PYTHON_RUNNER_COMMAND_CHARS:
        return build_oversized_inline_python_command_list(len(script), runner_length)
    return runner


def find_python_dash_c_index(command: list[str]) -> int | None:
    if not command:
        return None
    executable = command[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if executable not in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        return None
    for index, item in enumerate(command[1:5], start=1):
        if item == "-c":
            return index
    return None


def should_rewrite_inline_python_script(script: str) -> bool:
    return "\n" in script or "base64" in script.lower() or len(script) >= 800


def build_python_temp_script_runner_command(executable: str, script: str, extra_args: list[str]) -> str:
    return subprocess.list2cmdline(build_python_temp_script_runner_command_list(executable, script, extra_args))


def build_python_temp_script_runner_command_list(executable: str, script: str, extra_args: list[str]) -> list[str]:
    script_path = spool_tool_payload(script, prefix="script", suffix=".py")
    executable_literal = powershell_single_quoted(executable)
    script_parts = [
        "$ErrorActionPreference = 'Stop'",
        f"$tmp = {powershell_string_expr(str(script_path))}",
        "$dir = Split-Path -Parent $tmp",
        "if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }",
    ]
    fallback_parts = build_python_temp_script_recovery_parts(script)
    if fallback_parts:
        candidate_parts = script_parts + fallback_parts
        candidate_command = build_python_temp_script_runner_command_parts(
            candidate_parts,
            executable_literal,
            extra_args,
        )
        candidate_runner = build_python_temp_script_runner_command_from_script(candidate_command)
        if len(subprocess.list2cmdline(candidate_runner)) <= MAX_INLINE_PYTHON_RUNNER_COMMAND_CHARS:
            return candidate_runner

    script_parts.append(
        "if (-not (Test-Path -LiteralPath $tmp)) { "
        "Write-Error 'Responses Proxy temporary Python payload is unavailable; retry with a write_file-generated script.'; "
        "exit 64 "
        "}"
    )
    powershell_script = build_python_temp_script_runner_command_parts(script_parts, executable_literal, extra_args)
    return build_python_temp_script_runner_command_from_script(powershell_script)


def build_python_temp_script_recovery_parts(script: str) -> list[str]:
    encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
    return [
        "if (-not (Test-Path -LiteralPath $tmp)) { "
        f"$script = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{encoded_script}')); "
        "[System.IO.File]::WriteAllText($tmp, $script, [System.Text.UTF8Encoding]::new($false)) "
        "}",
    ]


def build_python_temp_script_runner_command_parts(
    script_parts: list[str],
    executable_literal: str,
    extra_args: list[str],
) -> str:
    parts = list(script_parts)
    if extra_args:
        argv = ", ".join(powershell_string_expr(item) for item in extra_args)
        parts.append(f"$argv = @({argv})")
        parts.append(f"& {executable_literal} $tmp @argv")
    else:
        parts.append(f"& {executable_literal} $tmp")
    parts.extend(
        [
            "$code = if ($LASTEXITCODE -is [int]) { $LASTEXITCODE } else { 0 }",
            "exit $code",
        ]
    )
    return "; ".join(parts)


def build_python_temp_script_runner_command_from_script(powershell_script: str) -> list[str]:
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        powershell_script,
    ]


def build_oversized_inline_python_command(script_chars: int, command_chars: int) -> str:
    return subprocess.list2cmdline(build_oversized_inline_python_command_list(script_chars, command_chars))


def build_oversized_inline_python_command_list(script_chars: int, command_chars: int) -> list[str]:
    message = (
        "Responses Proxy blocked an oversized inline Python command "
        f"({script_chars} script chars, {command_chars} command chars). "
        "Use write_file to create a short script file before running it."
    )
    powershell_script = f"Write-Error {powershell_single_quoted(message)}; exit 64"
    return [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        powershell_script,
    ]


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

    if schema_type == "string" and isinstance(value, list) and all(isinstance(item, str) for item in value):
        if platform.system().lower().startswith("win"):
            return subprocess.list2cmdline(value)
        return shlex.join(value)

    return value


def translate_hallucinated_write_file(
    tool_name: str,
    arguments: str,
    tool_schemas: dict[str, dict[str, Any]],
) -> tuple[str, str] | None:
    if tool_name not in {"write_file", "create_file", "save_file", "overwrite_file"}:
        return None
    shell_tool_name = resolve_shell_tool_name(tool_schemas)
    if shell_tool_name is None:
        return None

    parsed_arguments = parse_json_value(arguments)
    if not isinstance(parsed_arguments, dict):
        return None

    path = first_string_value(parsed_arguments, "path", "file_path", "filename", "target")
    content = first_text_value(parsed_arguments, "content", "new_content", "file_content", "body", "text")
    if not path or content is None:
        return None

    shell_arguments = {
        "command": [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            build_powershell_write_file_command(path, content),
        ]
    }
    return shell_tool_name, serialize_tool_arguments(shell_tool_name, shell_arguments, tool_schemas)


def translate_hallucinated_apply_patch(
    tool_name: str,
    arguments: str,
    tool_schemas: dict[str, dict[str, Any]],
) -> tuple[str, str] | None:
    if tool_name != "apply_patch":
        return None
    shell_tool_name = resolve_shell_tool_name(tool_schemas)
    if shell_tool_name is None:
        return None

    parsed_arguments = parse_json_value(arguments)
    if not isinstance(parsed_arguments, dict):
        return None

    patch_text = extract_apply_patch_text(parsed_arguments)
    if patch_text:
        simple_write = parse_simple_apply_patch_write(patch_text)
        if simple_write is not None:
            path, content = simple_write
            shell_arguments = {
                "command": [
                    "powershell.exe",
                    "-Command",
                    build_powershell_write_file_command(path, content),
                ]
            }
            return shell_tool_name, serialize_tool_arguments(shell_tool_name, shell_arguments, tool_schemas)

    direct_path = first_string_value(parsed_arguments, "file_path", "path", "filename", "target")
    direct_content = first_text_value(
        parsed_arguments,
        "new_content",
        "content",
        "file_content",
        "body",
        "text",
    )
    if direct_path and direct_content is not None:
        shell_arguments = {
            "command": [
                "powershell.exe",
                "-Command",
                build_powershell_write_file_command(direct_path, direct_content),
            ]
        }
        return shell_tool_name, serialize_tool_arguments(shell_tool_name, shell_arguments, tool_schemas)

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
    return shell_tool_name, serialize_tool_arguments(shell_tool_name, shell_arguments, tool_schemas)


def translate_hallucinated_read_file(
    tool_name: str,
    arguments: str,
    tool_schemas: dict[str, dict[str, Any]],
) -> tuple[str, str] | None:
    if tool_name not in {"read", "read_file", "view", "cat", "open_file"}:
        return None
    shell_tool_name = resolve_shell_tool_name(tool_schemas)
    if shell_tool_name is None:
        return None

    parsed_arguments = parse_json_value(arguments)
    if not isinstance(parsed_arguments, dict):
        return None

    path = first_string_value(parsed_arguments, "path", "file_path", "filename", "target")
    if not path:
        return None

    shell_arguments = {"command": build_read_file_command(path)}
    return shell_tool_name, serialize_tool_arguments(shell_tool_name, shell_arguments, tool_schemas)


def first_string_value(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def first_text_value(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def resolve_shell_tool_name(tool_schemas: dict[str, dict[str, Any]]) -> str | None:
    if "shell" in tool_schemas:
        return "shell"
    if "shell_command" in tool_schemas:
        return "shell_command"
    return None


def serialize_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    tool_schemas: dict[str, dict[str, Any]],
) -> str:
    schema = (tool_schemas.get(tool_name) or {}).get("parameters")
    return json.dumps(coerce_value_to_schema(arguments, schema), ensure_ascii=False)


def build_read_file_command(path: str) -> list[str]:
    if platform.system().lower().startswith("win"):
        return [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"Get-Content -Raw -LiteralPath {powershell_string_expr(path)}",
        ]
    return ["cat", "--", path]


def build_powershell_create_directory_command(path: str) -> str:
    return (
        f"$path = {powershell_string_expr(path)}; "
        "New-Item -ItemType Directory -Force -Path $path | Out-Null; "
        "Write-Output \"Directory ready\""
    )


def extract_apply_patch_text(parsed_arguments: dict[str, Any]) -> str | None:
    command = parsed_arguments.get("command")
    if isinstance(command, list) and len(command) >= 2 and str(command[0]).strip() == "apply_patch":
        return str(command[1])
    if isinstance(command, str) and "*** Begin Patch" in command:
        return command[command.find("*** Begin Patch") :]
    for key in ("patch", "input", "content"):
        value = parsed_arguments.get(key)
        if isinstance(value, str) and "*** Begin Patch" in value:
            return value[value.find("*** Begin Patch") :]
    return None


def parse_simple_apply_patch_write(patch_text: str) -> tuple[str, str] | None:
    if "*** Begin Patch\\n" in patch_text:
        patch_text = patch_text.replace("\\r\\n", "\n").replace("\\n", "\n")
    lines = patch_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].strip() != "*** Begin Patch":
        return None
    if not any(line.strip() == "*** End Patch" for line in lines):
        return None

    file_path: str | None = None
    content_lines: list[str] = []
    in_file = False
    for raw_line in lines[1:]:
        line = raw_line.rstrip("\n")
        if (
            line.startswith("*** Add File: ")
            or line.startswith("*** Create File: ")
            or line.startswith("*** Update File: ")
        ):
            if file_path is not None:
                return None
            file_path = line.split(": ", 1)[1].strip()
            in_file = True
            continue
        if line.startswith("*** End Patch"):
            break
        if line.startswith("*** "):
            return None
        if not in_file:
            continue
        if not line.startswith("+"):
            return None
        content_lines.append(line[1:])

    if not file_path:
        return None
    return file_path, "\n".join(content_lines)


def build_powershell_write_file_command(path: str, content: str) -> str:
    encoded_content = base64.b64encode(content.encode("utf-8")).decode("ascii")
    command = (
        f"$path = {powershell_string_expr(path)}; "
        "$dir = Split-Path -Parent $path; "
        "if ($dir -and -not (Test-Path -LiteralPath $dir)) { "
        "New-Item -ItemType Directory -Path $dir -Force | Out-Null "
        "}; "
        f"$content = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{encoded_content}')); "
        "[System.IO.File]::WriteAllText($path, $content, [System.Text.UTF8Encoding]::new($false)); "
        "Write-Output \"File written\""
    )
    if len(command) <= MAX_INLINE_POWERSHELL_WRITE_COMMAND_CHARS:
        return command
    return build_powershell_spooled_write_file_command(path, content)


def build_powershell_spooled_write_file_command(path: str, content: str) -> str:
    payload_path = spool_tool_payload(content)
    return (
        "$ErrorActionPreference = 'Stop'; "
        f"$src = {powershell_string_expr(str(payload_path))}; "
        f"$path = {powershell_string_expr(path)}; "
        "$dir = Split-Path -Parent $path; "
        "if ($dir -and -not (Test-Path -LiteralPath $dir)) { "
        "New-Item -ItemType Directory -Path $dir -Force | Out-Null "
        "}; "
        "Copy-Item -LiteralPath $src -Destination $path -Force -ErrorAction Stop; "
        "Write-Output \"File written\""
    )


def spool_tool_payload(content: str, *, prefix: str = "write", suffix: str = ".txt") -> Path:
    TOOL_PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_tool_payloads(TOOL_PAYLOAD_DIR)
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "-", prefix).strip("-") or "payload"
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    payload_path = TOOL_PAYLOAD_DIR / f"{safe_prefix}-{uuid.uuid4().hex}{safe_suffix}"
    with payload_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(content)
    return payload_path


def cleanup_old_tool_payloads(payload_dir: Path, *, max_age_seconds: int = 24 * 60 * 60) -> None:
    cutoff = time.time() - max_age_seconds
    try:
        candidates = list(payload_dir.glob("write-*.txt")) + list(payload_dir.glob("script-*.py"))
    except OSError:
        return
    for candidate in candidates:
        try:
            if candidate.stat().st_mtime < cutoff:
                candidate.unlink()
        except OSError:
            continue


def powershell_single_quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def powershell_string_expr(value: str) -> str:
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return powershell_utf8_string_expr(value)
    return powershell_single_quoted(value)


def powershell_utf8_string_expr(value: str) -> str:
    encoded_value = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{encoded_value}'))"


def decode_base64_utf8(value: str) -> str | None:
    try:
        return base64.b64decode(value).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def convert_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    prompt_tokens = int(value.get("prompt_tokens", 0))
    completion_tokens = int(value.get("completion_tokens", 0))
    total_tokens = int(value.get("total_tokens", prompt_tokens + completion_tokens))
    completion_details = value.get("completion_tokens_details")
    reasoning_tokens = 0
    if isinstance(completion_details, dict):
        reasoning_tokens = int(completion_details.get("reasoning_tokens", 0))
    return {
        "input_tokens": prompt_tokens,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": completion_tokens,
        "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
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
    added_emitted: bool = False


@dataclass
class StreamAccumulator:
    payload: dict[str, Any]
    response_id: str
    hosted_output_items: list[dict[str, Any]] = field(default_factory=list)
    hosted_annotations: list[dict[str, Any]] = field(default_factory=list)
    protocol_report: ProtocolReport | None = None
    created_at: int = field(default_factory=lambda: int(time.time()))
    message_id: str = field(default_factory=lambda: make_id("msg"))
    reasoning_id: str = field(default_factory=lambda: make_id("rs"))
    text_started: bool = False
    reasoning_started: bool = False
    text_chunks: list[str] = field(default_factory=list)
    reasoning_chunks: list[str] = field(default_factory=list)
    tool_calls: dict[int, StreamToolCall] = field(default_factory=dict)
    tool_call_order: list[int] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    sequence_number: int = 0
    message_output_index: int | None = None

    def should_buffer_text(self) -> bool:
        return bool(build_tool_schema_index(self.payload))

    def reasoning_output_index(self) -> int:
        return len(self.hosted_output_items)

    def current_output_prefix_count(self) -> int:
        return len(self.hosted_output_items) + (1 if self.reasoning_started else 0)

    def get_message_output_index(self) -> int:
        if self.message_output_index is None:
            self.message_output_index = self.current_output_prefix_count()
        return self.message_output_index

    def next_tool_output_index(self) -> int:
        message_reserved = self.message_output_index is not None or bool(self.text_chunks)
        return self.current_output_prefix_count() + (1 if message_reserved else 0) + len(self.tool_call_order)

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
            protocol_report=self.protocol_report,
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

    def in_progress_event(self) -> bytes:
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
            protocol_report=self.protocol_report,
        )
        return self.emit("response.in_progress", {"response": response_stub})

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
        reasoning_content = normalize_stream_reasoning_delta(delta)
        if isinstance(reasoning_content, str) and reasoning_content:
            self.reasoning_chunks.append(reasoning_content)
            if not self.reasoning_started:
                self.reasoning_started = True
                events.append(
                    self.emit(
                        "response.output_item.added",
                        {
                            "output_index": self.reasoning_output_index(),
                            "item": build_reasoning_item("", item_id=self.reasoning_id, status="in_progress"),
                        },
                    )
                )
            events.append(
                self.emit(
                    "response.reasoning_summary_text.delta",
                    {
                        "item_id": self.reasoning_id,
                        "output_index": self.reasoning_output_index(),
                        "summary_index": 0,
                        "delta": reasoning_content,
                    },
                )
            )
        if isinstance(content, str) and content:
            self.text_chunks.append(content)
            if self.should_buffer_text():
                self.finish_reason = choice.get("finish_reason") or self.finish_reason
                return events
            if not self.text_started:
                self.text_started = True
                output_index = self.get_message_output_index()
                events.append(
                    self.emit(
                        "response.output_item.added",
                        {
                            "output_index": output_index,
                            "item": self.build_stream_message_item(status="in_progress", text=""),
                        },
                    )
                )
                events.append(
                    self.emit(
                        "response.content_part.added",
                        {
                            "item_id": self.message_id,
                            "output_index": output_index,
                            "content_index": 0,
                            "part": self.build_stream_text_part(""),
                        },
                    )
                )
            events.append(
                self.emit(
                    "response.output_text.delta",
                        {
                            "item_id": self.message_id,
                            "output_index": self.get_message_output_index(),
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
                    tool_output_index = self.next_tool_output_index()
                    tool_call = StreamToolCall(
                        id=raw_tool_call.get("id") or make_id("call"),
                        output_index=tool_output_index,
                        name=function.get("name", ""),
                        added_emitted=True,
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

    def should_retry_tool_intent(self) -> bool:
        assistant_text = "".join(self.text_chunks)
        if self.tool_call_order:
            return False
        return should_retry_tool_intent_text(self.payload, assistant_text, tool_calls=[])

    def reset_for_tool_intent_retry(self) -> str:
        assistant_text = "".join(self.text_chunks)
        self.text_chunks.clear()
        self.reasoning_chunks.clear()
        self.tool_calls.clear()
        self.tool_call_order.clear()
        self.finish_reason = None
        self.usage = None
        self.text_started = False
        self.reasoning_started = False
        self.message_output_index = None
        self.reasoning_id = make_id("rs")
        return assistant_text

    def finalize(self) -> tuple[list[bytes], dict[str, Any], list[dict[str, Any]]]:
        events: list[bytes] = []
        assistant_text = "".join(self.text_chunks)
        reasoning_content = "".join(self.reasoning_chunks)
        tool_schemas = build_tool_schema_index(self.payload)
        normalized_tool_calls = []
        tool_added_emitted: list[bool] = []
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
            tool_added_emitted.append(raw_tool_call.added_emitted)

        if not normalized_tool_calls:
            assistant_text, normalized_tool_calls = extract_text_tool_call_markers(assistant_text, self.payload)
            tool_added_emitted = [False for _ in normalized_tool_calls]

        if normalized_tool_calls and not any(tool_added_emitted):
            normalized_tool_calls = dedupe_tool_calls_for_execution(normalized_tool_calls)
            tool_added_emitted = [False for _ in normalized_tool_calls]

        if should_suppress_tool_preamble_text(self.payload, assistant_text, normalized_tool_calls):
            assistant_text = ""

        has_message_item = bool(assistant_text or not normalized_tool_calls)
        if reasoning_content and self.reasoning_started:
            events.append(
                self.emit(
                    "response.reasoning_summary_text.done",
                    {
                        "item_id": self.reasoning_id,
                        "output_index": self.reasoning_output_index(),
                        "summary_index": 0,
                        "text": reasoning_content,
                    },
                )
            )
            events.append(
                self.emit(
                    "response.output_item.done",
                    {
                        "output_index": self.reasoning_output_index(),
                        "item": build_reasoning_item(
                            reasoning_content,
                            item_id=self.reasoning_id,
                            status="completed",
                        ),
                    },
                )
            )
        if has_message_item:
            message_output_index = self.get_message_output_index()
            stream_message_item = self.build_stream_message_item(status="completed", text=assistant_text)
            if not self.text_started:
                events.append(
                    self.emit(
                        "response.output_item.added",
                        {
                            "output_index": message_output_index,
                            "item": self.build_stream_message_item(status="in_progress", text=""),
                        },
                    )
                )
                events.append(
                    self.emit(
                        "response.content_part.added",
                        {
                            "item_id": self.message_id,
                            "output_index": message_output_index,
                            "content_index": 0,
                            "part": self.build_stream_text_part(""),
                        },
                    )
                )
            events.append(
                self.emit(
                    "response.output_text.done",
                    {
                        "item_id": self.message_id,
                        "output_index": message_output_index,
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
                        "output_index": message_output_index,
                        "content_index": 0,
                        "part": self.build_stream_text_part(assistant_text),
                    },
                )
            )
            events.append(
                self.emit(
                    "response.output_item.done",
                    {
                        "output_index": message_output_index,
                        "item": stream_message_item,
                    },
                )
            )

        for tool_index, tool_call in enumerate(normalized_tool_calls):
            output_index = self.final_tool_output_index(
                tool_index,
                has_message_item,
                tool_added_emitted[tool_index],
            )
            if not tool_added_emitted[tool_index]:
                events.append(
                    self.emit(
                        "response.output_item.added",
                        {
                            "output_index": output_index,
                            "item": self.build_stream_function_call_item(
                                StreamToolCall(
                                    id=tool_call["id"],
                                    output_index=output_index,
                                    name=tool_call["function"]["name"],
                                    arguments="",
                                ),
                                status="in_progress",
                            ),
                        },
                    )
                )
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
            reasoning_content=reasoning_content,
            prefix_output_items=self.hosted_output_items,
            annotations=self.hosted_annotations,
            protocol_report=self.protocol_report,
        )
        history_output = build_history_output(
            assistant_text,
            normalized_tool_calls,
            reasoning_content=reasoning_content,
        )
        return events, response, history_output

    def final_tool_output_index(self, tool_index: int, has_message_item: bool, already_emitted: bool) -> int:
        if already_emitted and tool_index < len(self.tool_call_order):
            raw_tool_call = self.tool_calls[self.tool_call_order[tool_index]]
            return raw_tool_call.output_index
        return (
            len(self.hosted_output_items)
            + (1 if self.reasoning_started else 0)
            + (1 if has_message_item else 0)
            + tool_index
        )

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
