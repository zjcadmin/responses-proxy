from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import run_manager, run_proxy, stop_manager, stop_proxy


def test_ensure_port_available_rejects_occupied_port() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.listen()

    try:
        with pytest.raises(RuntimeError, match=rf"Port {port} .*already in use"):
            run_proxy.ensure_port_available(host, port)
    finally:
        sock.close()


def test_parse_listening_pids_from_netstat_output() -> None:
    output = """
  TCP    127.0.0.1:8800       0.0.0.0:0              LISTENING       1234
  TCP    0.0.0.0:8800         0.0.0.0:0              LISTENING       5678
  TCP    127.0.0.1:9999       0.0.0.0:0              LISTENING       4321
"""

    assert stop_proxy.parse_listening_pids(output, 8800) == [1234, 5678]


def test_stop_manager_parse_listening_pids_from_netstat_output() -> None:
    output = """
  TCP    127.0.0.1:8899       0.0.0.0:0              LISTENING       2222
  TCP    0.0.0.0:8899         0.0.0.0:0              LISTENING       3333
  TCP    127.0.0.1:8800       0.0.0.0:0              LISTENING       4444
"""

    assert stop_manager.parse_listening_pids(output, 8899) == [2222, 3333]


def test_stop_proxy_script_terminates_listener_from_config(tmp_path: Path) -> None:
    port = _find_free_port()
    config_path = tmp_path / "model-config.json"
    config_path.write_text(
        json.dumps({"proxy_host": "127.0.0.1", "proxy_port": port}, ensure_ascii=False),
        encoding="utf-8",
    )

    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        _wait_for_listener(port)

        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "stop_proxy.py"), "--config", str(config_path)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        assert result.returncode == 0
        assert f"Stopped process on port {port}" in result.stdout

        server.wait(timeout=10)
        assert server.returncode is not None
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=10)


def test_stop_manager_script_terminates_listener_from_config(tmp_path: Path) -> None:
    port = _find_free_port()
    config_path = tmp_path / "manager-config.json"
    config_path.write_text(
        json.dumps({"manager_host": "127.0.0.1", "manager_port": port}, ensure_ascii=False),
        encoding="utf-8",
    )

    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        _wait_for_listener(port)

        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "stop_manager.py"), "--config", str(config_path)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        assert result.returncode == 0
        assert f"Stopped process on port {port}" in result.stdout

        server.wait(timeout=10)
        assert server.returncode is not None
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=10)


def test_unix_shell_scripts_are_present_and_executable_entrypoints() -> None:
    project_root = Path(__file__).resolve().parents[1]
    for script_name, expected_entrypoint in {
        "start-manager.sh": "scripts/run_manager.py",
        "stop-manager.sh": "scripts/stop_manager.py",
        "start-proxy.sh": "scripts/run_proxy.py",
        "stop-proxy.sh": "scripts/stop_proxy.py",
    }.items():
        script = project_root / script_name
        source = script.read_text(encoding="utf-8")
        assert source.startswith("#!/usr/bin/env sh")
        assert expected_entrypoint in source


def test_run_manager_uses_pyinstaller_safe_uvicorn_logging(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(app, **kwargs):  # noqa: ANN001
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr(run_manager.uvicorn, "run", fake_run)
    monkeypatch.setattr(run_manager, "ensure_port_available", lambda host, port: None)
    monkeypatch.setattr(sys, "argv", ["run_manager.py", "--data-dir", str(tmp_path)])

    assert run_manager.main() == 0
    assert captured["app"] == "app.manager_main:app"
    assert captured["kwargs"]["log_config"] is None


def test_run_proxy_uses_pyinstaller_safe_uvicorn_logging(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    config_path = tmp_path / "model-config.json"
    config_path.write_text(
        json.dumps(
            {
                "upstream_base_url": "https://api.deepseek.com/v1",
                "upstream_model": "deepseek-chat",
                "proxy_host": "127.0.0.1",
                "proxy_port": 8800,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_run(app, **kwargs):  # noqa: ANN001
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr(run_proxy.uvicorn, "run", fake_run)
    monkeypatch.setattr(run_proxy, "ensure_port_available", lambda host, port: None)
    monkeypatch.setattr(sys, "argv", ["run_proxy.py", "--config", str(config_path)])

    assert run_proxy.main() == 0
    assert captured["app"] == "app.main:app"
    assert captured["kwargs"]["log_config"] is None


def test_docker_assets_define_manager_service_and_persistent_volumes() -> None:
    project_root = Path(__file__).resolve().parents[1]
    dockerfile = (project_root / "Dockerfile").read_text(encoding="utf-8")
    compose = (project_root / "docker-compose.yml").read_text(encoding="utf-8")
    dockerignore = (project_root / ".dockerignore").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim" in dockerfile
    assert "scripts/run_manager.py" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "127.0.0.1:8899/healthz" in dockerfile
    assert "8899:8899" in compose
    assert "8800:8800" in compose
    assert "./data:/data" in compose
    assert ".env" in dockerignore


def _find_free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _wait_for_listener(port: int, timeout_seconds: float = 10.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for listener on port {port}")
