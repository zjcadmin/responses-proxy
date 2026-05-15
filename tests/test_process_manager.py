from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import app.process_manager as process_manager_module
from app.manager_config import ModelPreset
from app.process_manager import ProcessManager


def test_write_launch_config_snapshot(tmp_path: Path) -> None:
    manager = ProcessManager(project_root=tmp_path, python_executable=Path(sys.executable))
    preset = _build_preset()

    launch_path = manager.write_launch_config(preset, proxy_api_key="proxy-key")

    payload = json.loads(launch_path.read_text(encoding="utf-8"))
    assert payload["upstream_model"] == preset.model
    assert payload["proxy_port"] == preset.proxy_port
    assert payload["upstream_headers"] == preset.headers
    assert payload["proxy_api_key"] == "proxy-key"
    assert payload["upstream_supports_image_input"] is True


def test_stop_by_pid_file_returns_stopped_state(tmp_path: Path) -> None:
    manager = ProcessManager(project_root=tmp_path, python_executable=Path(sys.executable))
    process = subprocess.Popen(
        [sys.executable, "-m", "http.server", "0", "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        manager.write_pid(process.pid)
        result = manager.stop_proxy()
        process.wait(timeout=10)

        assert result.state == "stopped"
        assert result.running is False
        assert manager.read_pid() is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_tail_logs_returns_latest_lines(tmp_path: Path) -> None:
    manager = ProcessManager(project_root=tmp_path, python_executable=Path(sys.executable))
    manager.stdout_log_path.write_text("line1\nline2\nline3\n", encoding="utf-8")

    tail = manager.tail_file(manager.stdout_log_path, lines=2)

    assert tail == ["line2", "line3"]


def test_start_proxy_uses_unbuffered_child_process_and_records_launch_log(monkeypatch, tmp_path: Path) -> None:
    manager = ProcessManager(project_root=tmp_path, python_executable=Path(sys.executable))
    captured: dict[str, object] = {}

    class FakePopen:
        pid = 4321

        def __init__(self, command, **kwargs) -> None:
            captured["command"] = command
            captured["env"] = kwargs.get("env")

    monkeypatch.setattr(manager, "ensure_port_available", lambda host, port: None)
    monkeypatch.setattr(manager, "_wait_for_listen", lambda host, port, timeout_seconds: True)
    monkeypatch.setattr(process_manager_module.subprocess, "Popen", FakePopen)

    result = manager.start_proxy(tmp_path / "proxy-launch.json", host="127.0.0.1", port=8800)

    assert result.running is True
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert any("Launching proxy command" in line for line in manager.tail_file(manager.stdout_log_path, lines=10))


def test_find_listening_pids_uses_cross_platform_psutil(monkeypatch, tmp_path: Path) -> None:
    manager = ProcessManager(project_root=tmp_path, python_executable=Path(sys.executable))

    connections = [
        SimpleNamespace(status=process_manager_module.psutil.CONN_LISTEN, laddr=SimpleNamespace(port=8800), pid=1111),
        SimpleNamespace(status=process_manager_module.psutil.CONN_LISTEN, laddr=("127.0.0.1", 8800), pid=2222),
        SimpleNamespace(status="ESTABLISHED", laddr=SimpleNamespace(port=8800), pid=3333),
        SimpleNamespace(status=process_manager_module.psutil.CONN_LISTEN, laddr=SimpleNamespace(port=9999), pid=4444),
        SimpleNamespace(status=process_manager_module.psutil.CONN_LISTEN, laddr=SimpleNamespace(port=8800), pid=None),
    ]
    monkeypatch.setattr(process_manager_module.psutil, "net_connections", lambda kind: connections)

    assert manager.find_listening_pids(8800) == [1111, 2222]


def test_ensure_port_available_rejects_any_listener_on_same_port(monkeypatch, tmp_path: Path) -> None:
    manager = ProcessManager(project_root=tmp_path, python_executable=Path(sys.executable))
    monkeypatch.setattr(manager, "find_listening_pids", lambda port: [1111, 2222])

    with pytest.raises(RuntimeError) as exc:
        manager.ensure_port_available("0.0.0.0", 8800)

    assert "PID(s): 1111, 2222" in str(exc.value)


def test_status_recovers_from_stale_pid_file_by_port_lookup(monkeypatch, tmp_path: Path) -> None:
    manager = ProcessManager(project_root=tmp_path, python_executable=Path(sys.executable))
    manager.write_pid(9999)
    monkeypatch.setattr(manager, "is_pid_running", lambda pid: False)
    monkeypatch.setattr(manager, "find_listening_pids", lambda port: [2222])
    monkeypatch.setattr(manager, "_can_connect", lambda host, port: True)

    status = manager.status(host="127.0.0.1", port=8800)

    assert status.running is True
    assert status.pid == 2222


def _build_preset() -> ModelPreset:
    return ModelPreset(
        id="preset_1",
        name="Mimo",
        provider="Xiaomi Mimo",
        base_url="https://token-plan-cn.xiaomimimo.com/v1",
        chat_path="/chat/completions",
        api_key="upstream-key",
        model="mimo-v2.5-pro",
        proxy_host="127.0.0.1",
        proxy_port=8800,
        request_timeout_seconds=120.0,
        headers={"X-Provider": "mimo"},
        description="",
        api_key_header_name="Authorization",
        api_key_prefix="Bearer ",
        supports_image_input=True,
        is_active=True,
    )
