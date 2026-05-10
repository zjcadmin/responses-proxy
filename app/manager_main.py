from __future__ import annotations

import json
import os
from pathlib import Path
import inspect
import sys
from typing import Any, Callable

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator, model_validator

from app.auth import SessionStore, verify_password
from app.config import load_settings
from app.manager_config import ManagerSettingsInput, ManagerState, ModelPreset, ModelPresetInput, resolve_runtime_dir
from app.manager_store import ManagerStore
from app.process_manager import ProcessManager, ProcessStatus
from app.upstream import UpstreamChatClient, UpstreamHTTPError


class LoginPayload(BaseModel):
    password: str


class PasswordChangePayload(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str

    @field_validator("current_password", "new_password", "confirm_password")
    @classmethod
    def _strip_password(cls, value: str) -> str:
        return value.strip()

    @field_validator("new_password")
    @classmethod
    def _validate_new_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("New password must be at least 8 characters.")
        return value

    @model_validator(mode="after")
    def _validate_confirmation(self) -> "PasswordChangePayload":
        if self.new_password != self.confirm_password:
            raise ValueError("Password confirmation does not match.")
        return self


class ProxyStartPayload(BaseModel):
    preset_id: str | None = None


def create_manager_app(
    *,
    store: ManagerStore | None = None,
    process_manager: ProcessManager | None = None,
    session_store: SessionStore | None = None,
    connection_tester: Callable[[ModelPreset], Any] | None = None,
    project_root: Path | None = None,
) -> FastAPI:
    code_root = Path(__file__).resolve().parents[1]
    resolved_project_root = project_root or resolve_data_root(code_root)
    manager_store = store or ManagerStore(
        manager_config_path=resolved_project_root / "manager-config.json",
        presets_path=resolved_project_root / "model-presets.json",
        runtime_dir=resolved_project_root / "runtime",
        legacy_env_path=resolved_project_root / ".env",
        legacy_model_config_path=resolved_project_root / "model-config.json",
        project_root=resolved_project_root,
    )
    state = manager_store.load_state()
    manager_process = process_manager or ProcessManager(
        project_root=code_root,
        python_executable=Path(sys.executable),
        runtime_dir=resolve_runtime_dir(resolved_project_root, state.manager),
        proxy_command=resolve_proxy_command(),
    )
    sessions = session_store or SessionStore()
    tester = connection_tester or _default_connection_tester

    app = FastAPI(title="Responses Proxy Manager")
    app.state.manager_store = manager_store
    app.state.process_manager = manager_process
    app.state.session_store = sessions
    app.state.project_root = resolved_project_root

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def require_session(manager_session: str | None = Cookie(default=None)) -> str:
        if not sessions.is_valid(manager_session):
            raise HTTPException(status_code=401, detail="Authentication required.")
        return manager_session or ""

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        template_path = Path(__file__).resolve().parent / "templates" / "index.html"
        if template_path.exists():
            return HTMLResponse(
                template_path.read_text(encoding="utf-8"),
                headers={"Cache-Control": "no-store"},
            )
        return HTMLResponse(
            "<html><body><div id='manager-root'>Responses Proxy Manager</div></body></html>",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/auth/login")
    async def login(payload: LoginPayload, response: Response) -> dict[str, Any]:
        current = manager_store.load_state().manager
        if not verify_password(payload.password, current.password_salt, current.password_hash):
            raise HTTPException(status_code=401, detail="Invalid password.")
        token = sessions.create_session()
        response.set_cookie("manager_session", token, httponly=True, samesite="lax")
        manager_process.record_event("Manager login succeeded")
        return {"ok": True}

    @app.post("/api/auth/logout")
    async def logout(
        response: Response,
        manager_session: str | None = Cookie(default=None),
        _: str = Depends(require_session),
    ) -> dict[str, Any]:
        sessions.destroy_session(manager_session)
        response.delete_cookie("manager_session")
        manager_process.record_event("Manager logout completed")
        return {"ok": True}

    @app.put("/api/auth/password")
    async def change_password(payload: PasswordChangePayload, _: str = Depends(require_session)) -> dict[str, Any]:
        current = manager_store.load_state().manager
        if not verify_password(payload.current_password, current.password_salt, current.password_hash):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")
        manager_store.update_password(payload.new_password)
        manager_process.record_event("Manager password changed")
        return {"ok": True}

    @app.get("/api/session")
    async def session(manager_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        return {"authenticated": sessions.is_valid(manager_session)}

    @app.get("/api/status")
    async def status(_: str = Depends(require_session)) -> dict[str, Any]:
        return _build_dashboard_payload(manager_store.load_state(), manager_process)

    @app.get("/api/presets")
    async def presets(_: str = Depends(require_session)) -> dict[str, Any]:
        state = manager_store.load_state()
        return {
            "active_preset_id": state.presets.active_preset_id,
            "presets": [preset.model_dump(mode="json") for preset in state.presets.presets],
        }

    @app.get("/api/settings")
    async def settings(_: str = Depends(require_session)) -> dict[str, Any]:
        return manager_store.build_sync_preview()

    @app.put("/api/settings")
    async def update_settings(payload: ManagerSettingsInput, _: str = Depends(require_session)) -> dict[str, Any]:
        manager_store.update_manager_config(**payload.model_dump())
        state = manager_store.load_state()
        if _maybe_get_active_preset(state) is not None:
            manager_store.sync_active_files()
            manager_process.record_event("Manager settings updated and sync files refreshed")
        else:
            manager_process.record_event("Manager settings updated")
        return manager_store.build_sync_preview()

    @app.post("/api/presets")
    async def create_preset(payload: ModelPresetInput, _: str = Depends(require_session)) -> dict[str, Any]:
        preset = manager_store.save_preset(payload)
        manager_process.record_event(f"Preset created: {preset.name}")
        return {"preset": preset.model_dump(mode="json")}

    @app.put("/api/presets/{preset_id}")
    async def update_preset(preset_id: str, payload: ModelPresetInput, _: str = Depends(require_session)) -> dict[str, Any]:
        preset = manager_store.save_preset(payload, preset_id=preset_id)
        manager_process.record_event(f"Preset updated: {preset.name}")
        return {"preset": preset.model_dump(mode="json")}

    @app.delete("/api/presets/{preset_id}")
    async def delete_preset(preset_id: str, _: str = Depends(require_session)) -> dict[str, Any]:
        state = manager_store.delete_preset(preset_id)
        manager_process.record_event(f"Preset deleted: {preset_id}")
        return {
            "active_preset_id": state.presets.active_preset_id,
            "presets": [preset.model_dump(mode="json") for preset in state.presets.presets],
        }

    @app.post("/api/presets/{preset_id}/activate")
    async def activate_preset(preset_id: str, _: str = Depends(require_session)) -> dict[str, Any]:
        state = manager_store.set_active_preset(preset_id)
        manager_store.sync_active_files(preset_id)
        manager_process.record_event(f"Preset activated: {preset_id}")
        return {
            "active_preset_id": state.presets.active_preset_id,
            "presets": [preset.model_dump(mode="json") for preset in state.presets.presets],
        }

    @app.post("/api/presets/{preset_id}/test")
    async def test_preset(preset_id: str, _: str = Depends(require_session)) -> dict[str, Any]:
        preset = _get_preset(manager_store.load_state(), preset_id)
        result = tester(preset)
        if inspect.isawaitable(result):
            result = await result
        manager_process.record_event(f"Preset connectivity tested: {preset.name}")
        return result

    @app.post("/api/proxy/start")
    async def start_proxy(payload: ProxyStartPayload | None = None, _: str = Depends(require_session)) -> dict[str, Any]:
        state_before = manager_store.load_state()
        target_preset = _resolve_start_preset(manager_store, state_before, payload.preset_id if payload else None)
        sync_result = manager_store.sync_active_files(target_preset.id)
        launch_path = Path(sync_result["launch_path"])
        status_result = manager_process.start_proxy(launch_path, host=target_preset.proxy_host, port=target_preset.proxy_port)
        if not status_result.running and status_result.state == "error":
            raise HTTPException(status_code=400, detail=status_result.detail or "Proxy failed to start.")
        return _build_dashboard_payload(manager_store.load_state(), manager_process)

    @app.post("/api/proxy/stop")
    async def stop_proxy(_: str = Depends(require_session)) -> dict[str, Any]:
        state = manager_store.load_state()
        active = _maybe_get_active_preset(state)
        stop_result = manager_process.stop_proxy(
            host=active.proxy_host if active else None,
            port=active.proxy_port if active else None,
        )
        if stop_result.state == "stopped":
            manager_process.record_event("Proxy stop requested from manager UI")
        return _build_dashboard_payload(manager_store.load_state(), manager_process)

    @app.post("/api/proxy/restart")
    async def restart_proxy(payload: ProxyStartPayload | None = None, _: str = Depends(require_session)) -> dict[str, Any]:
        state_before = manager_store.load_state()
        target_preset = _resolve_start_preset(manager_store, state_before, payload.preset_id if payload else None)
        manager_process.stop_proxy(host=target_preset.proxy_host, port=target_preset.proxy_port)
        sync_result = manager_store.sync_active_files(target_preset.id)
        launch_path = Path(sync_result["launch_path"])
        status_result = manager_process.start_proxy(launch_path, host=target_preset.proxy_host, port=target_preset.proxy_port)
        if not status_result.running and status_result.state == "error":
            raise HTTPException(status_code=400, detail=status_result.detail or "Proxy failed to restart.")
        return _build_dashboard_payload(manager_store.load_state(), manager_process)

    @app.get("/api/logs")
    async def logs(_: str = Depends(require_session)) -> dict[str, Any]:
        lines = manager_store.load_state().manager.log_tail_lines
        return manager_process.read_logs(lines=lines)

    return app


def resolve_data_root(code_root: Path) -> Path:
    data_dir = os.getenv("RESPONSES_PROXY_DATA_DIR", "").strip()
    if not data_dir:
        return code_root
    path = Path(data_dir)
    return path if path.is_absolute() else code_root / path


def resolve_proxy_command() -> list[str] | None:
    raw = os.getenv("RESPONSES_PROXY_PROXY_COMMAND", "").strip()
    if not raw:
        return None
    payload = json.loads(raw)
    if not isinstance(payload, list) or not all(isinstance(item, str) and item for item in payload):
        raise ValueError("RESPONSES_PROXY_PROXY_COMMAND must be a JSON array of command parts.")
    return payload


def _resolve_start_preset(store: ManagerStore, state: ManagerState, preset_id: str | None) -> ModelPreset:
    if preset_id:
        state = store.set_active_preset(preset_id)
    active = _maybe_get_active_preset(state)
    if active is None:
        raise HTTPException(status_code=400, detail="No active preset configured.")
    return active


def _maybe_get_active_preset(state: ManagerState) -> ModelPreset | None:
    if not state.presets.active_preset_id:
        return None
    for preset in state.presets.presets:
        if preset.id == state.presets.active_preset_id:
            return preset
    return None


def _get_preset(state: ManagerState, preset_id: str) -> ModelPreset:
    for preset in state.presets.presets:
        if preset.id == preset_id:
            return preset
    raise HTTPException(status_code=404, detail=f"Unknown preset `{preset_id}`.")


def _build_dashboard_payload(state: ManagerState, process_manager: ProcessManager) -> dict[str, Any]:
    active = _maybe_get_active_preset(state)
    if active is None:
        proxy = ProcessStatus(state="stopped", running=False)
    else:
        proxy = process_manager.status(host=active.proxy_host, port=active.proxy_port)

    return {
        "manager": {
            "host": state.manager.manager_host,
            "port": state.manager.manager_port,
            "local_only": state.manager.local_only,
            "runtime_dir": state.manager.runtime_dir,
        },
        "proxy": {
            "state": proxy.state,
            "running": proxy.running,
            "pid": proxy.pid,
            "host": proxy.host,
            "port": proxy.port,
            "base_url": f"http://{proxy.host}:{proxy.port}/v1" if proxy.host and proxy.port else None,
            "detail": proxy.detail,
        },
        "active_preset": active.model_dump(mode="json") if active else None,
        "presets_count": len(state.presets.presets),
    }


async def _default_connection_tester(preset: ModelPreset) -> dict[str, Any]:
    settings = load_settings(
        {
            "upstream_base_url": preset.base_url,
            "upstream_chat_path": preset.chat_path,
            "upstream_api_key": preset.api_key,
            "upstream_model": preset.model,
            "proxy_api_key": "",
            "upstream_headers": preset.headers,
            "upstream_api_key_header_name": preset.api_key_header_name,
            "upstream_api_key_prefix": preset.api_key_prefix,
            "request_timeout_seconds": preset.request_timeout_seconds,
        }
    )
    payload = {
        "model": preset.model,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 8,
        "stream": False,
    }
    client = UpstreamChatClient(settings)

    try:
        response = await client.create_completion(payload, preset.api_key)
    except httpx.TimeoutException:
        return {"ok": False, "message": "Connection timed out."}
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Network error: {exc}"}
    except UpstreamHTTPError as exc:
        error_message = exc.payload.get("error", {}).get("message", "Upstream error.")
        return {"ok": False, "message": str(error_message)}
    except Exception as exc:  # pragma: no cover - defensive for provider quirks
        return {"ok": False, "message": f"Unexpected error: {exc}"}

    message = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    return {"ok": True, "message": message or f"Connected to {preset.name}"}


app = create_manager_app()
