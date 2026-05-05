from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

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
        is_active=True,
    )
