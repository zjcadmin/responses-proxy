from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
from threading import Lock
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from app.auth import generate_salt, hash_password
from app.manager_config import (
    ManagerConfig,
    ManagerState,
    ModelPreset,
    ModelPresetInput,
    ModelPresets,
    manager_config_example,
    presets_example,
    resolve_runtime_dir,
)

DEFAULT_MANAGER_PASSWORD = "admin123"
MANAGED_ENV_KEYS = [
    "RESPONSES_PROXY_UPSTREAM_BASE_URL",
    "RESPONSES_PROXY_UPSTREAM_CHAT_PATH",
    "RESPONSES_PROXY_UPSTREAM_MODEL",
    "RESPONSES_PROXY_UPSTREAM_API_KEY",
    "RESPONSES_PROXY_PROXY_API_KEY",
    "RESPONSES_PROXY_UPSTREAM_HEADERS",
    "RESPONSES_PROXY_UPSTREAM_API_KEY_HEADER_NAME",
    "RESPONSES_PROXY_UPSTREAM_API_KEY_PREFIX",
    "RESPONSES_PROXY_REQUEST_TIMEOUT_SECONDS",
    "RESPONSES_PROXY_WEB_SEARCH_BACKEND",
    "RESPONSES_PROXY_WEB_SEARCH_SEARXNG_URL",
    "RESPONSES_PROXY_WEB_SEARCH_TAVILY_API_KEY",
    "RESPONSES_PROXY_WEB_SEARCH_MAX_RESULTS",
    "RESPONSES_PROXY_FILE_SEARCH_PATHS",
    "RESPONSES_PROXY_FILE_SEARCH_MAX_RESULTS",
]


class ManagerStore:
    def __init__(
        self,
        *,
        manager_config_path: Path,
        presets_path: Path,
        runtime_dir: Path,
        legacy_env_path: Path | None = None,
        legacy_model_config_path: Path | None = None,
        project_root: Path | None = None,
    ) -> None:
        self._manager_config_path = manager_config_path
        self._presets_path = presets_path
        self._runtime_dir = runtime_dir
        self._legacy_env_path = legacy_env_path
        self._legacy_model_config_path = legacy_model_config_path
        self._project_root = project_root or manager_config_path.parent
        self._lock = Lock()
        self._sync_env_path = legacy_env_path or (self._project_root / ".env")
        self._sync_model_config_path = legacy_model_config_path or (self._project_root / "model-config.json")

    def load_state(self) -> ManagerState:
        with self._lock:
            manager = self._read_or_initialize_manager_config()
            presets = self._read_or_initialize_presets(manager)
            self._ensure_runtime_dir(manager)
            self._write_example_files()
            return ManagerState(manager=manager, presets=presets)

    def save_preset(self, preset_input: ModelPresetInput, preset_id: str | None = None) -> ModelPreset:
        with self._lock:
            bundle = self._read_or_initialize_presets(self._read_or_initialize_manager_config())
            existing_by_id = {preset.id: preset for preset in bundle.presets}
            active_preset_id = bundle.active_preset_id

            if preset_id and preset_id in existing_by_id:
                current = existing_by_id[preset_id]
                is_active = current.is_active
                model = ModelPreset(id=preset_id, is_active=is_active, **preset_input.model_dump())
            else:
                new_id = preset_id or f"preset_{uuid4().hex[:12]}"
                is_active = not bundle.presets
                model = ModelPreset(id=new_id, is_active=is_active, **preset_input.model_dump())
                if is_active:
                    active_preset_id = new_id

            updated: list[ModelPreset] = []
            replaced = False
            for preset in bundle.presets:
                if preset.id == model.id:
                    updated.append(model)
                    replaced = True
                else:
                    updated.append(preset)
            if not replaced:
                updated.append(model)

            updated_bundle = ModelPresets(active_preset_id=active_preset_id, presets=updated)
            self._write_json(self._presets_path, updated_bundle.model_dump(mode="json"))
            return model

    def delete_preset(self, preset_id: str) -> ManagerState:
        with self._lock:
            manager = self._read_or_initialize_manager_config()
            bundle = self._read_or_initialize_presets(manager)
            remaining = [preset for preset in bundle.presets if preset.id != preset_id]
            active_preset_id = bundle.active_preset_id
            if active_preset_id == preset_id:
                active_preset_id = remaining[0].id if remaining else None
            normalized = self._normalize_active_flags(remaining, active_preset_id)
            updated_bundle = ModelPresets(active_preset_id=active_preset_id, presets=normalized)
            self._write_json(self._presets_path, updated_bundle.model_dump(mode="json"))
            return ManagerState(manager=manager, presets=updated_bundle)

    def set_active_preset(self, preset_id: str) -> ManagerState:
        with self._lock:
            manager = self._read_or_initialize_manager_config()
            bundle = self._read_or_initialize_presets(manager)
            if preset_id not in {preset.id for preset in bundle.presets}:
                raise KeyError(preset_id)
            normalized = self._normalize_active_flags(bundle.presets, preset_id)
            updated_bundle = ModelPresets(active_preset_id=preset_id, presets=normalized)
            self._write_json(self._presets_path, updated_bundle.model_dump(mode="json"))
            return ManagerState(manager=manager, presets=updated_bundle)

    def get_active_preset(self) -> ModelPreset:
        state = self.load_state()
        if not state.presets.active_preset_id:
            raise LookupError("No active preset configured.")
        for preset in state.presets.presets:
            if preset.id == state.presets.active_preset_id:
                return preset
        raise LookupError("Active preset id is missing from presets.")

    def update_manager_config(self, **updates: Any) -> ManagerConfig:
        with self._lock:
            manager = self._read_or_initialize_manager_config()
            updated = ManagerConfig.model_validate({**manager.model_dump(mode="json"), **updates})
            self._write_json(self._manager_config_path, updated.model_dump(mode="json"))
            self._ensure_runtime_dir(updated)
            return updated

    def update_password(self, new_password: str) -> ManagerConfig:
        salt = generate_salt()
        return self.update_manager_config(
            password_salt=salt,
            password_hash=hash_password(new_password, salt),
        )

    def sync_active_files(self, preset_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            manager = self._read_or_initialize_manager_config()
            bundle = self._read_or_initialize_presets(manager)
            target_preset_id = preset_id or bundle.active_preset_id
            if not target_preset_id:
                raise LookupError("No active preset configured.")

            target_preset = next((preset for preset in bundle.presets if preset.id == target_preset_id), None)
            if target_preset is None:
                raise LookupError(f"Unknown preset `{target_preset_id}`.")

            runtime_dir = resolve_runtime_dir(self._project_root, manager)
            launch_path = runtime_dir / "proxy-launch.json"
            launch_payload = self._build_launch_payload(target_preset, manager)
            env_payload = self._build_env_payload(launch_payload)

            self._write_env_file(env_payload)
            self._write_json(self._sync_model_config_path, launch_payload)
            self._write_json(launch_path, launch_payload)

            return {
                "preset_id": target_preset.id,
                "preset_name": target_preset.name,
                "env_path": str(self._sync_env_path),
                "model_config_path": str(self._sync_model_config_path),
                "launch_path": str(launch_path),
                "env_payload": env_payload,
                "launch_payload": launch_payload,
            }

    def build_sync_preview(self) -> dict[str, Any]:
        with self._lock:
            manager = self._read_or_initialize_manager_config()
            bundle = self._read_or_initialize_presets(manager)
            active_preset = next(
                (preset for preset in bundle.presets if preset.id == bundle.active_preset_id),
                None,
            )
            if active_preset is None:
                return {
                    "settings": self._build_settings_payload(manager),
                    "sync": {
                        "active_preset_id": None,
                        "active_preset_name": None,
                        "env_preview": "",
                        "model_config_preview": "{}",
                        "launch_preview": "{}",
                        "launch_path": str(resolve_runtime_dir(self._project_root, manager) / "proxy-launch.json"),
                        "env_path": str(self._sync_env_path),
                        "model_config_path": str(self._sync_model_config_path),
                    },
                }

            launch_payload = self._build_launch_payload(active_preset, manager)
            env_payload = self._build_env_payload(launch_payload)
            runtime_dir = resolve_runtime_dir(self._project_root, manager)
            return {
                "settings": self._build_settings_payload(manager),
                "sync": {
                    "active_preset_id": active_preset.id,
                    "active_preset_name": active_preset.name,
                    "env_preview": self._serialize_env_payload(env_payload),
                    "model_config_preview": json.dumps(launch_payload, ensure_ascii=False, indent=2),
                    "launch_preview": json.dumps(launch_payload, ensure_ascii=False, indent=2),
                    "launch_path": str(runtime_dir / "proxy-launch.json"),
                    "env_path": str(self._sync_env_path),
                    "model_config_path": str(self._sync_model_config_path),
                },
            }

    def _read_or_initialize_manager_config(self) -> ManagerConfig:
        if self._manager_config_path.exists():
            data = json.loads(self._manager_config_path.read_text(encoding="utf-8"))
            return ManagerConfig.model_validate(data)

        legacy_env = self._read_env_file(self._legacy_env_path) if self._legacy_env_path else {}
        password_salt = secrets.token_urlsafe(16)
        manager = ManagerConfig(
            manager_host=os.getenv("RESPONSES_PROXY_MANAGER_HOST", "127.0.0.1"),
            manager_port=int(os.getenv("RESPONSES_PROXY_MANAGER_PORT", "8899")),
            password_hash=hash_password(DEFAULT_MANAGER_PASSWORD, password_salt),
            password_salt=password_salt,
            session_secret=secrets.token_urlsafe(32),
            local_only=True,
            log_tail_lines=200,
            runtime_dir=str(self._runtime_dir),
            proxy_api_key=legacy_env.get("RESPONSES_PROXY_PROXY_API_KEY", secrets.token_urlsafe(24)),
        )
        self._write_json(self._manager_config_path, manager.model_dump(mode="json"))
        return manager

    def _read_or_initialize_presets(self, manager: ManagerConfig) -> ModelPresets:
        if self._presets_path.exists():
            data = json.loads(self._presets_path.read_text(encoding="utf-8"))
            bundle = ModelPresets.model_validate(data)
            normalized = self._normalize_active_flags(bundle.presets, bundle.active_preset_id)
            normalized_bundle = ModelPresets(active_preset_id=bundle.active_preset_id, presets=normalized)
            if normalized_bundle != bundle:
                self._write_json(self._presets_path, normalized_bundle.model_dump(mode="json"))
            return normalized_bundle

        imported = self._import_legacy_preset()
        if imported is None:
            bundle = ModelPresets(active_preset_id=None, presets=[])
        else:
            bundle = ModelPresets(active_preset_id=imported.id, presets=[imported])

        self._write_json(self._presets_path, bundle.model_dump(mode="json"))
        self._ensure_runtime_dir(manager)
        return bundle

    def _import_legacy_preset(self) -> ModelPreset | None:
        if not self._legacy_model_config_path or not self._legacy_model_config_path.exists():
            return None

        model_config = json.loads(self._legacy_model_config_path.read_text(encoding="utf-8"))
        legacy_env = self._read_env_file(self._legacy_env_path) if self._legacy_env_path else {}
        upstream_api_key = legacy_env.get("RESPONSES_PROXY_UPSTREAM_API_KEY", "")
        base_url = str(model_config.get("upstream_base_url", "")).strip().rstrip("/")
        if not base_url:
            return None

        provider = self._infer_provider(base_url)
        preset_input = ModelPresetInput(
            name=f"Imported {provider}",
            provider=provider,
            base_url=base_url,
            chat_path=str(model_config.get("upstream_chat_path", "/chat/completions")),
            api_key=upstream_api_key,
            model=str(model_config.get("upstream_model", "")).strip(),
            proxy_host=str(model_config.get("proxy_host", "127.0.0.1")).strip() or "127.0.0.1",
            proxy_port=int(model_config.get("proxy_port", 8800)),
            request_timeout_seconds=float(model_config.get("request_timeout_seconds", 120.0)),
            headers={},
            description="Imported from legacy local files.",
        )
        return ModelPreset(
            id=f"preset_{uuid4().hex[:12]}",
            is_active=True,
            **preset_input.model_dump(),
        )

    def _build_launch_payload(self, preset: ModelPreset, manager: ManagerConfig) -> dict[str, Any]:
        return {
            "upstream_base_url": preset.base_url,
            "upstream_chat_path": preset.chat_path,
            "upstream_model": preset.model,
            "upstream_api_key": preset.api_key,
            "proxy_host": preset.proxy_host,
            "proxy_port": preset.proxy_port,
            "proxy_api_key": manager.proxy_api_key,
            "upstream_headers": preset.headers,
            "upstream_api_key_header_name": preset.api_key_header_name,
            "upstream_api_key_prefix": preset.api_key_prefix,
            "request_timeout_seconds": preset.request_timeout_seconds,
            "web_search_backend": manager.web_search_backend,
            "web_search_searxng_url": manager.web_search_searxng_url,
            "web_search_tavily_api_key": manager.web_search_tavily_api_key,
            "web_search_max_results": manager.web_search_max_results,
            "file_search_paths": manager.file_search_paths,
            "file_search_max_results": manager.file_search_max_results,
        }

    def _build_env_payload(self, launch_payload: dict[str, Any]) -> dict[str, str]:
        return {
            "RESPONSES_PROXY_UPSTREAM_BASE_URL": str(launch_payload["upstream_base_url"]),
            "RESPONSES_PROXY_UPSTREAM_CHAT_PATH": str(launch_payload["upstream_chat_path"]),
            "RESPONSES_PROXY_UPSTREAM_MODEL": str(launch_payload["upstream_model"]),
            "RESPONSES_PROXY_UPSTREAM_API_KEY": str(launch_payload.get("upstream_api_key", "") or ""),
            "RESPONSES_PROXY_PROXY_API_KEY": str(launch_payload.get("proxy_api_key", "") or ""),
            "RESPONSES_PROXY_UPSTREAM_HEADERS": json.dumps(
                launch_payload.get("upstream_headers", {}),
                ensure_ascii=False,
            ),
            "RESPONSES_PROXY_UPSTREAM_API_KEY_HEADER_NAME": str(
                launch_payload.get("upstream_api_key_header_name", "Authorization")
            ),
            "RESPONSES_PROXY_UPSTREAM_API_KEY_PREFIX": str(
                launch_payload.get("upstream_api_key_prefix", "Bearer ")
            ),
            "RESPONSES_PROXY_REQUEST_TIMEOUT_SECONDS": str(
                launch_payload.get("request_timeout_seconds", 120.0)
            ),
            "RESPONSES_PROXY_WEB_SEARCH_BACKEND": str(
                launch_payload.get("web_search_backend", "disabled")
            ),
            "RESPONSES_PROXY_WEB_SEARCH_SEARXNG_URL": str(
                launch_payload.get("web_search_searxng_url", "")
            ),
            "RESPONSES_PROXY_WEB_SEARCH_TAVILY_API_KEY": str(
                launch_payload.get("web_search_tavily_api_key", "") or ""
            ),
            "RESPONSES_PROXY_WEB_SEARCH_MAX_RESULTS": str(
                launch_payload.get("web_search_max_results", 5)
            ),
            "RESPONSES_PROXY_FILE_SEARCH_PATHS": json.dumps(
                launch_payload.get("file_search_paths", []),
                ensure_ascii=False,
            ),
            "RESPONSES_PROXY_FILE_SEARCH_MAX_RESULTS": str(
                launch_payload.get("file_search_max_results", 5)
            ),
        }

    @staticmethod
    def _build_settings_payload(manager: ManagerConfig) -> dict[str, Any]:
        return {
            "manager_host": manager.manager_host,
            "manager_port": manager.manager_port,
            "proxy_api_key": manager.proxy_api_key,
            "web_search_backend": manager.web_search_backend,
            "web_search_searxng_url": manager.web_search_searxng_url,
            "web_search_tavily_api_key": manager.web_search_tavily_api_key,
            "web_search_max_results": manager.web_search_max_results,
            "file_search_paths": manager.file_search_paths,
            "file_search_max_results": manager.file_search_max_results,
        }

    def _ensure_runtime_dir(self, manager: ManagerConfig) -> None:
        runtime_dir = resolve_runtime_dir(self._project_root, manager)
        runtime_dir.mkdir(parents=True, exist_ok=True)

    def _write_example_files(self) -> None:
        manager_example_path = self._project_root / "manager-config.example.json"
        presets_example_path = self._project_root / "model-presets.example.json"
        if not manager_example_path.exists():
            self._write_json(manager_example_path, manager_config_example())
        if not presets_example_path.exists():
            self._write_json(presets_example_path, presets_example())

    def _normalize_active_flags(self, presets: list[ModelPreset], active_preset_id: str | None) -> list[ModelPreset]:
        normalized: list[ModelPreset] = []
        for preset in presets:
            normalized.append(preset.model_copy(update={"is_active": preset.id == active_preset_id}))
        return normalized

    def _read_env_file(self, path: Path | None) -> dict[str, str]:
        if path is None or not path.exists():
            return {}
        values: dict[str, str] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
        return values

    @staticmethod
    def _infer_provider(base_url: str) -> str:
        host = urlparse(base_url).netloc.lower()
        if "deepseek" in host:
            return "DeepSeek"
        if "mimo" in host or "xiaomi" in host:
            return "Xiaomi Mimo"
        if host.startswith("api."):
            host = host[4:]
        return host.split(".")[0].capitalize() or "Custom Provider"

    def _write_env_file(self, updates: dict[str, str]) -> None:
        existing = self._read_env_file(self._sync_env_path)
        merged = {**existing, **updates}
        extras = sorted(key for key in merged if key not in MANAGED_ENV_KEYS)
        ordered_keys = [key for key in MANAGED_ENV_KEYS if key in merged] + extras
        content = "\n".join(f"{key}={merged[key]}" for key in ordered_keys).strip()
        if content:
            content += "\n"
        self._sync_env_path.parent.mkdir(parents=True, exist_ok=True)
        self._sync_env_path.write_text(content, encoding="utf-8")

    @staticmethod
    def _serialize_env_payload(payload: dict[str, str]) -> str:
        return "\n".join(f"{key}={value}" for key, value in payload.items())

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
