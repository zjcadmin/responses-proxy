from __future__ import annotations

from pathlib import Path
import json

from fastapi.testclient import TestClient
import pytest

from app.auth import SessionStore
from app.manager_config import ModelPresetInput
from app.manager_main import create_manager_app
from app.manager_store import ManagerStore
from app.process_manager import ProcessStatus


class FakeProcessManager:
    def __init__(self) -> None:
        self.started_with: tuple[str, int] | None = None
        self.stopped = False
        self.current_status = ProcessStatus(state="stopped", running=False)

    def write_launch_config(self, preset, *, proxy_api_key: str):
        self.started_with = (preset.name, preset.proxy_port)
        return Path("runtime/proxy-launch.json")

    def start_proxy(self, launch_config_path: Path, *, host: str, port: int, timeout_seconds: float = 5.0) -> ProcessStatus:
        self.current_status = ProcessStatus(state="running", running=True, pid=12345, host=host, port=port)
        return self.current_status

    def stop_proxy(self, *, host: str | None = None, port: int | None = None) -> ProcessStatus:
        self.stopped = True
        self.current_status = ProcessStatus(state="stopped", running=False, host=host, port=port)
        return self.current_status

    def status(self, *, host: str, port: int) -> ProcessStatus:
        if self.current_status.host is None:
            self.current_status.host = host
            self.current_status.port = port
        return self.current_status

    def read_logs(self, *, lines: int):
        return {"events": ["started"], "stdout": [], "stderr": []}

    def record_event(self, message: str) -> None:
        return None


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    store = ManagerStore(
        manager_config_path=tmp_path / "manager-config.json",
        presets_path=tmp_path / "model-presets.json",
        runtime_dir=tmp_path / "runtime",
        project_root=tmp_path,
    )
    store.load_state()
    store.save_preset(
        ModelPresetInput(
            name="DeepSeek",
            provider="DeepSeek",
            base_url="https://api.deepseek.com/v1",
            chat_path="/chat/completions",
            api_key="upstream-key",
            model="deepseek-chat",
            proxy_host="127.0.0.1",
            proxy_port=8800,
            request_timeout_seconds=120.0,
            headers={},
            description="",
        )
    )

    app = create_manager_app(
        store=store,
        process_manager=FakeProcessManager(),
        session_store=SessionStore(),
        connection_tester=lambda preset: {"ok": True, "message": f"Connected to {preset.name}"},
        project_root=tmp_path,
    )
    return TestClient(app)


def test_login_sets_session_cookie(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": "admin123"})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "manager_session=" in response.headers["set-cookie"]


def test_manager_index_serves_html(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Responses Proxy Manager" in response.text
    assert "manager-root" in response.text


def test_protected_status_requires_login(client: TestClient) -> None:
    response = client.get("/api/status")

    assert response.status_code == 401


def test_start_proxy_uses_active_preset(client: TestClient) -> None:
    client.post("/api/auth/login", json={"password": "admin123"})
    response = client.post("/api/proxy/start")

    assert response.status_code == 200
    assert response.json()["proxy"]["running"] is True
    assert response.json()["active_preset"]["name"] == "DeepSeek"
    assert response.json()["proxy"]["base_url"] == "http://127.0.0.1:8800/v1"


def test_connection_test_returns_message(client: TestClient) -> None:
    client.post("/api/auth/login", json={"password": "admin123"})
    presets = client.get("/api/presets")
    preset_id = presets.json()["presets"][0]["id"]

    response = client.post(f"/api/presets/{preset_id}/test")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "Connected to DeepSeek" in response.json()["message"]


def test_activate_preset_syncs_model_files(client: TestClient, tmp_path: Path) -> None:
    client.post("/api/auth/login", json={"password": "admin123"})
    presets = client.get("/api/presets").json()["presets"]
    preset_id = presets[0]["id"]

    response = client.post(f"/api/presets/{preset_id}/activate")

    assert response.status_code == 200
    env_values = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "RESPONSES_PROXY_UPSTREAM_MODEL=deepseek-chat" in env_values
    model_config = json.loads((tmp_path / "model-config.json").read_text(encoding="utf-8"))
    assert model_config["upstream_model"] == "deepseek-chat"
    assert model_config["proxy_port"] == 8800


def test_settings_endpoint_updates_proxy_api_key_and_sync_preview(client: TestClient, tmp_path: Path) -> None:
    client.post("/api/auth/login", json={"password": "admin123"})
    presets = client.get("/api/presets").json()["presets"]
    preset_id = presets[0]["id"]
    client.post(f"/api/presets/{preset_id}/activate")

    response = client.put("/api/settings", json={"proxy_api_key": "proxy-key-updated"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["proxy_api_key"] == "proxy-key-updated"
    assert "RESPONSES_PROXY_PROXY_API_KEY=proxy-key-updated" in payload["sync"]["env_preview"]
    env_values = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "RESPONSES_PROXY_PROXY_API_KEY=proxy-key-updated" in env_values
