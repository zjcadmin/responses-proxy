from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class ManagerConfig(BaseModel):
    manager_host: str = "127.0.0.1"
    manager_port: int = 8899
    password_hash: str = ""
    password_salt: str = ""
    session_secret: str = ""
    local_only: bool = True
    log_tail_lines: int = 200
    runtime_dir: str = "runtime"
    proxy_api_key: str = ""

    @field_validator("manager_host")
    @classmethod
    def _normalize_manager_host(cls, value: str) -> str:
        return value.strip() or "127.0.0.1"

    @field_validator("runtime_dir")
    @classmethod
    def _normalize_runtime_dir(cls, value: str) -> str:
        return value.strip() or "runtime"


class ManagerSettingsInput(BaseModel):
    proxy_api_key: str = ""

    @field_validator("proxy_api_key")
    @classmethod
    def _normalize_proxy_api_key(cls, value: str) -> str:
        return value.strip()


class ModelPresetInput(BaseModel):
    name: str
    provider: str
    base_url: str
    chat_path: str = "/chat/completions"
    api_key: str
    model: str
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 8800
    request_timeout_seconds: float = 120.0
    headers: dict[str, str] = Field(default_factory=dict)
    description: str = ""
    api_key_header_name: str = "Authorization"
    api_key_prefix: str = "Bearer "

    @field_validator("name", "provider", "model", "proxy_host")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("This field is required.")
        return stripped

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        stripped = value.strip().rstrip("/")
        parsed = urlparse(stripped)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be a valid http or https URL.")
        return stripped

    @field_validator("chat_path")
    @classmethod
    def _normalize_chat_path(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            return "/chat/completions"
        return stripped if stripped.startswith("/") else f"/{stripped}"

    @field_validator("api_key", "description", "api_key_prefix", "api_key_header_name")
    @classmethod
    def _normalize_optional_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("headers", mode="before")
    @classmethod
    def _normalize_headers(cls, value: Any) -> dict[str, str]:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise ValueError("headers must be a JSON object.")
        normalized: dict[str, str] = {}
        for key, header_value in value.items():
            normalized[str(key).strip()] = str(header_value).strip()
        return normalized


class ModelPreset(ModelPresetInput):
    id: str
    is_active: bool = False


class ModelPresets(BaseModel):
    active_preset_id: str | None = None
    presets: list[ModelPreset] = Field(default_factory=list)


class ManagerState(BaseModel):
    manager: ManagerConfig
    presets: ModelPresets


def manager_config_example() -> dict[str, Any]:
    return ManagerConfig(
        manager_host="127.0.0.1",
        manager_port=8899,
        password_hash="<set-on-first-run>",
        password_salt="<set-on-first-run>",
        session_secret="<set-on-first-run>",
        local_only=True,
        log_tail_lines=200,
        runtime_dir="runtime",
        proxy_api_key="<optional-proxy-api-key>",
    ).model_dump(mode="json")


def presets_example() -> dict[str, Any]:
    preset = ModelPreset(
        id="preset_example_deepseek",
        name="DeepSeek Example",
        provider="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        chat_path="/chat/completions",
        api_key="<your-upstream-api-key>",
        model="deepseek-chat",
        proxy_host="127.0.0.1",
        proxy_port=8800,
        request_timeout_seconds=120.0,
        headers={},
        description="Example preset for local setup",
    )
    return ModelPresets(active_preset_id=preset.id, presets=[preset]).model_dump(mode="json")


def resolve_runtime_dir(project_root: Path, manager_config: ManagerConfig) -> Path:
    runtime_dir = Path(manager_config.runtime_dir)
    if runtime_dir.is_absolute():
        return runtime_dir
    return project_root / runtime_dir
