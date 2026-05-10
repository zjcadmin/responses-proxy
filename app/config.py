from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MODEL_CONFIG_PATH = Path(__file__).resolve().parents[1] / "model-config.json"


class Settings(BaseSettings):
    app_name: str = "responses-proxy"
    upstream_base_url: str = "https://api.deepseek.com/v1"
    upstream_chat_path: str = "/chat/completions"
    upstream_api_key: str | None = None
    upstream_model: str | None = None
    proxy_api_key: str | None = None
    upstream_headers: dict[str, str] = Field(default_factory=dict)
    upstream_api_key_header_name: str = "Authorization"
    upstream_api_key_prefix: str = "Bearer "
    request_timeout_seconds: float = 120.0
    web_search_backend: str = "disabled"
    web_search_searxng_url: str = ""
    web_search_tavily_api_key: str | None = None
    web_search_max_results: int = 5
    file_search_paths: list[str] = Field(default_factory=list)
    file_search_max_results: int = 5

    model_config = SettingsConfigDict(
        env_prefix="RESPONSES_PROXY_",
        env_file=".env",
        extra="ignore",
    )

    @field_validator("upstream_base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("upstream_chat_path")
    @classmethod
    def _normalize_chat_path(cls, value: str) -> str:
        return value if value.startswith("/") else f"/{value}"

    @field_validator("upstream_api_key", "upstream_model", "proxy_api_key")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("upstream_api_key_header_name", "upstream_api_key_prefix")
    @classmethod
    def _normalize_header_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("web_search_backend", "web_search_searxng_url")
    @classmethod
    def _normalize_search_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("web_search_tavily_api_key")
    @classmethod
    def _normalize_optional_search_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("file_search_paths", mode="before")
    @classmethod
    def _parse_file_search_paths(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = [item for item in value.split(";") if item.strip()]
            value = parsed
        if not isinstance(value, list):
            raise ValueError("file_search_paths must be a list or semicolon-separated string.")
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("upstream_headers", mode="before")
    @classmethod
    def _parse_headers(cls, value: Any) -> dict[str, str]:
        if value in (None, ""):
            return {}
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict):
            raise ValueError("upstream_headers must be a JSON object.")
        return {str(key).strip(): str(header_value).strip() for key, header_value in value.items()}


def load_settings(overrides: dict[str, Any] | None = None) -> Settings:
    synced_defaults = load_synced_model_config_defaults(DEFAULT_MODEL_CONFIG_PATH)
    if overrides:
        synced_defaults.update(overrides)
    return Settings(**synced_defaults)


class LaunchConfig(BaseModel):
    upstream_base_url: str = "https://api.deepseek.com/v1"
    upstream_chat_path: str = "/chat/completions"
    upstream_model: str = "deepseek-chat"
    upstream_api_key: str | None = None
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 8000
    proxy_api_key: str | None = None
    upstream_headers: dict[str, str] = Field(default_factory=dict)
    upstream_api_key_header_name: str = "Authorization"
    upstream_api_key_prefix: str = "Bearer "
    request_timeout_seconds: float = 120.0
    web_search_backend: str = "disabled"
    web_search_searxng_url: str = ""
    web_search_tavily_api_key: str | None = None
    web_search_max_results: int = 5
    file_search_paths: list[str] = Field(default_factory=list)
    file_search_max_results: int = 5

    @field_validator("upstream_base_url")
    @classmethod
    def _normalize_launch_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("upstream_chat_path")
    @classmethod
    def _normalize_launch_chat_path(cls, value: str) -> str:
        return value if value.startswith("/") else f"/{value}"

    @field_validator("upstream_api_key", "proxy_api_key")
    @classmethod
    def _normalize_launch_keys(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("upstream_headers", mode="before")
    @classmethod
    def _parse_launch_headers(cls, value: Any) -> dict[str, str]:
        if value in (None, ""):
            return {}
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict):
            raise ValueError("upstream_headers must be a JSON object.")
        return {str(key).strip(): str(header_value).strip() for key, header_value in value.items()}

    @field_validator("upstream_api_key_header_name", "upstream_api_key_prefix")
    @classmethod
    def _normalize_launch_header_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("web_search_backend", "web_search_searxng_url")
    @classmethod
    def _normalize_launch_search_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("web_search_tavily_api_key")
    @classmethod
    def _normalize_launch_optional_search_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("file_search_paths", mode="before")
    @classmethod
    def _parse_launch_file_search_paths(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = [item for item in value.split(";") if item.strip()]
            value = parsed
        if not isinstance(value, list):
            raise ValueError("file_search_paths must be a list or semicolon-separated string.")
        return [str(item).strip() for item in value if str(item).strip()]

    def to_env(self) -> dict[str, str]:
        return {
            "RESPONSES_PROXY_UPSTREAM_BASE_URL": self.upstream_base_url,
            "RESPONSES_PROXY_UPSTREAM_CHAT_PATH": self.upstream_chat_path,
            "RESPONSES_PROXY_UPSTREAM_MODEL": self.upstream_model,
            "RESPONSES_PROXY_UPSTREAM_API_KEY": self.upstream_api_key or "",
            "RESPONSES_PROXY_PROXY_API_KEY": self.proxy_api_key or "",
            "RESPONSES_PROXY_UPSTREAM_HEADERS": json.dumps(self.upstream_headers, ensure_ascii=False),
            "RESPONSES_PROXY_UPSTREAM_API_KEY_HEADER_NAME": self.upstream_api_key_header_name,
            "RESPONSES_PROXY_UPSTREAM_API_KEY_PREFIX": self.upstream_api_key_prefix,
            "RESPONSES_PROXY_REQUEST_TIMEOUT_SECONDS": str(self.request_timeout_seconds),
            "RESPONSES_PROXY_WEB_SEARCH_BACKEND": self.web_search_backend,
            "RESPONSES_PROXY_WEB_SEARCH_SEARXNG_URL": self.web_search_searxng_url,
            "RESPONSES_PROXY_WEB_SEARCH_TAVILY_API_KEY": self.web_search_tavily_api_key or "",
            "RESPONSES_PROXY_WEB_SEARCH_MAX_RESULTS": str(self.web_search_max_results),
            "RESPONSES_PROXY_FILE_SEARCH_PATHS": json.dumps(self.file_search_paths, ensure_ascii=False),
            "RESPONSES_PROXY_FILE_SEARCH_MAX_RESULTS": str(self.file_search_max_results),
        }


def load_launch_config(path: str | Path) -> LaunchConfig:
    config_path = Path(path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return LaunchConfig.model_validate(data)


def load_synced_model_config_defaults(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    data = json.loads(config_path.read_text(encoding="utf-8"))
    mapped: dict[str, Any] = {}
    for key in (
        "upstream_base_url",
        "upstream_chat_path",
        "upstream_model",
        "upstream_api_key",
        "proxy_api_key",
        "upstream_headers",
        "upstream_api_key_header_name",
        "upstream_api_key_prefix",
        "request_timeout_seconds",
        "web_search_backend",
        "web_search_searxng_url",
        "web_search_tavily_api_key",
        "web_search_max_results",
        "file_search_paths",
        "file_search_max_results",
    ):
        if key in data:
            mapped[key] = data[key]
    return mapped
