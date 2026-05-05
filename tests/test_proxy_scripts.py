from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import run_proxy, stop_manager, stop_proxy


def test_ensure_port_available_rejects_occupied_port() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.listen()

    try:
        with pytest.raises(RuntimeError, match=f"Port {port} on {host} is already in use"):
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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific process management")
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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific process management")
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
