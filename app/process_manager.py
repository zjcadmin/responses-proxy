from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any

import psutil

from app.manager_config import ModelPreset


@dataclass
class ProcessStatus:
    state: str
    running: bool
    pid: int | None = None
    host: str | None = None
    port: int | None = None
    detail: str = ""


class ProcessManager:
    def __init__(
        self,
        project_root: Path,
        python_executable: Path,
        runtime_dir: Path | None = None,
        proxy_command: list[str] | None = None,
    ) -> None:
        self.project_root = project_root
        self.python_executable = python_executable
        self.proxy_command = proxy_command
        self.runtime_dir = runtime_dir or (project_root / "runtime")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.pid_path = self.runtime_dir / "proxy.pid"
        self.launch_path = self.runtime_dir / "proxy-launch.json"
        self.stdout_log_path = self.runtime_dir / "proxy.stdout.log"
        self.stderr_log_path = self.runtime_dir / "proxy.stderr.log"
        self.events_log_path = self.runtime_dir / "manager-events.log"
        self.run_proxy_path = project_root / "scripts" / "run_proxy.py"

    def write_launch_config(self, preset: ModelPreset, *, proxy_api_key: str) -> Path:
        payload = {
            "upstream_base_url": preset.base_url,
            "upstream_chat_path": preset.chat_path,
            "upstream_model": preset.model,
            "upstream_api_key": preset.api_key,
            "proxy_host": preset.proxy_host,
            "proxy_port": preset.proxy_port,
            "proxy_api_key": proxy_api_key,
            "upstream_headers": preset.headers,
            "upstream_api_key_header_name": preset.api_key_header_name,
            "upstream_api_key_prefix": preset.api_key_prefix,
            "request_timeout_seconds": preset.request_timeout_seconds,
        }
        self.launch_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.launch_path

    def write_pid(self, pid: int) -> None:
        self.pid_path.write_text(str(pid), encoding="utf-8")

    def read_pid(self) -> int | None:
        if not self.pid_path.exists():
            return None
        content = self.pid_path.read_text(encoding="utf-8").strip()
        if not content:
            return None
        try:
            return int(content)
        except ValueError:
            return None

    def clear_pid(self) -> None:
        if self.pid_path.exists():
            self.pid_path.unlink()

    def start_proxy(self, launch_config_path: Path, *, host: str, port: int, timeout_seconds: float = 5.0) -> ProcessStatus:
        self.ensure_port_available(host, port)
        stdout_handle = self.stdout_log_path.open("a", encoding="utf-8")
        stderr_handle = self.stderr_log_path.open("a", encoding="utf-8")
        try:
            process = subprocess.Popen(
                self._build_proxy_command(launch_config_path),
                cwd=self.project_root,
                stdout=stdout_handle,
                stderr=stderr_handle,
            )
        finally:
            stdout_handle.close()
            stderr_handle.close()
        self.write_pid(process.pid)
        started = self._wait_for_listen(host, port, timeout_seconds)
        if started:
            self.record_event(f"Proxy started on {host}:{port} with PID {process.pid}")
            return ProcessStatus(state="running", running=True, pid=process.pid, host=host, port=port)
        self.clear_pid()
        detail = self.tail_file(self.stderr_log_path, lines=1)
        self.record_event(f"Proxy failed to start on {host}:{port}")
        return ProcessStatus(
            state="error",
            running=False,
            pid=process.pid,
            host=host,
            port=port,
            detail=detail[-1] if detail else "Proxy failed to start.",
        )

    def stop_proxy(self, *, host: str | None = None, port: int | None = None) -> ProcessStatus:
        pid = self.read_pid()
        if pid and self.is_pid_running(pid):
            self._terminate_pid(pid)
            self.clear_pid()
            self.record_event(f"Proxy stopped for PID {pid}")
            return ProcessStatus(state="stopped", running=False, pid=None, host=host, port=port)

        if port is not None:
            pids = self.find_listening_pids(port)
            for candidate in pids:
                self._terminate_pid(candidate)
            if pids:
                self.clear_pid()
                self.record_event(f"Proxy stopped by port lookup on {host or '127.0.0.1'}:{port}")
                return ProcessStatus(state="stopped", running=False, pid=None, host=host, port=port)

        self.clear_pid()
        return ProcessStatus(state="stopped", running=False, pid=None, host=host, port=port, detail="Proxy was not running.")

    def status(self, *, host: str, port: int) -> ProcessStatus:
        pid = self.read_pid()
        if pid and self.is_pid_running(pid):
            if self._can_connect(host, port):
                return ProcessStatus(state="running", running=True, pid=pid, host=host, port=port)
            return ProcessStatus(state="starting", running=True, pid=pid, host=host, port=port)
        return ProcessStatus(state="stopped", running=False, pid=None, host=host, port=port)

    def record_event(self, message: str) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with self.events_log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")

    def tail_file(self, path: Path, *, lines: int) -> list[str]:
        if not path.exists():
            return []
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return content[-lines:]

    def read_logs(self, *, lines: int) -> dict[str, list[str]]:
        return {
            "events": self.tail_file(self.events_log_path, lines=lines),
            "stdout": self.tail_file(self.stdout_log_path, lines=lines),
            "stderr": self.tail_file(self.stderr_log_path, lines=lines),
        }

    def ensure_port_available(self, host: str, port: int) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, port))
        except OSError as exc:
            raise RuntimeError(f"Port {port} on {host} is already in use.") from exc
        finally:
            sock.close()

    def find_listening_pids(self, port: int) -> list[int]:
        pids: list[int] = []
        for connection in psutil.net_connections(kind="tcp"):
            if connection.status != psutil.CONN_LISTEN:
                continue
            if self._connection_port(connection.laddr) != port or connection.pid is None:
                continue
            pid = int(connection.pid)
            if pid not in pids:
                pids.append(pid)
        return pids

    def is_pid_running(self, pid: int) -> bool:
        if not psutil.pid_exists(pid):
            return False
        try:
            process = psutil.Process(pid)
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            return False

    def _terminate_pid(self, pid: int) -> None:
        try:
            process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return
        children = process.children(recursive=True)
        for child in children:
            child.terminate()
        process.terminate()
        _, alive = psutil.wait_procs([*children, process], timeout=5)
        for still_alive in alive:
            still_alive.kill()

    def _build_proxy_command(self, launch_config_path: Path) -> list[str]:
        if self.proxy_command:
            return [*self.proxy_command, "--config", str(launch_config_path)]
        return [str(self.python_executable), str(self.run_proxy_path), "--config", str(launch_config_path)]

    def _wait_for_listen(self, host: str, port: int, timeout_seconds: float) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self._can_connect(host, port):
                return True
            time.sleep(0.1)
        return False

    def _can_connect(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            return False

    @staticmethod
    def _extract_port(endpoint: str) -> int | None:
        if ":" not in endpoint:
            return None
        _, port_text = endpoint.rsplit(":", 1)
        try:
            return int(port_text)
        except ValueError:
            return None

    @staticmethod
    def _connection_port(local_address: Any) -> int | None:
        if hasattr(local_address, "port"):
            return int(local_address.port)
        if isinstance(local_address, tuple) and len(local_address) >= 2:
            return int(local_address[1])
        return None
