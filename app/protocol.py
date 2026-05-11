from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SUPPORTED_FIELDS = {
    "frequency_penalty",
    "input",
    "instructions",
    "max_output_tokens",
    "metadata",
    "model",
    "presence_penalty",
    "previous_response_id",
    "response_format",
    "stop",
    "store",
    "stream",
    "temperature",
    "text",
    "tool_choice",
    "tools",
    "top_p",
    "user",
}

EMULATED_FIELDS = {
    "background",
    "parallel_tool_calls",
    "prompt_cache_key",
    "reasoning",
}

IGNORED_FIELDS = {
    "include",
    "max_tool_calls",
    "prompt",
    "service_tier",
    "top_logprobs",
    "truncation",
}

KNOWN_FIELDS = SUPPORTED_FIELDS | EMULATED_FIELDS | IGNORED_FIELDS


@dataclass(frozen=True)
class ProtocolReport:
    supported_fields: list[str]
    emulated_fields: list[str]
    ignored_fields: list[str]
    unsupported_fields: list[str]

    @property
    def has_compatibility_notes(self) -> bool:
        return bool(self.emulated_fields or self.ignored_fields or self.unsupported_fields)

    def to_metadata(self) -> dict[str, Any]:
        compatibility: dict[str, Any] = {
            "mode": "chat_completions_bridge",
            "supported_fields": self.supported_fields,
        }
        if self.emulated_fields:
            compatibility["emulated_fields"] = self.emulated_fields
        if self.ignored_fields:
            compatibility["ignored_fields"] = self.ignored_fields
        if self.unsupported_fields:
            compatibility["unsupported_fields"] = self.unsupported_fields
        return {"compatibility": compatibility}


def analyze_protocol(payload: dict[str, Any]) -> ProtocolReport:
    keys = set(payload.keys())
    return ProtocolReport(
        supported_fields=sorted(keys & SUPPORTED_FIELDS),
        emulated_fields=sorted(keys & EMULATED_FIELDS),
        ignored_fields=sorted(keys & IGNORED_FIELDS),
        unsupported_fields=sorted(keys - KNOWN_FIELDS),
    )


def strict_protocol_error(report: ProtocolReport) -> str | None:
    blocked = report.ignored_fields + report.unsupported_fields
    if not blocked:
        return None
    return "Unsupported Responses API fields in strict mode: " + ", ".join(blocked)
