from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any

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
    def __init__(self, project_root: Path, python_executable: Path) -> None:
        self.project_root = project_root
        self.python_executable = python_executable
        self.runtime_dir = project_root / "runtime"
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
                [str(self.python_executable), str(self.run_proxy_path), "--config", str(launch_config_path)],
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
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=True,
        )
        pids: list[int] = []
        for raw_line in completed.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 5 or parts[0].upper() != "TCP":
                continue
            local_endpoint = parts[1]
            remote_endpoint = parts[2]
            pid_text = parts[-1]
            if self._extract_port(local_endpoint) != port or not remote_endpoint.endswith(":0"):
                continue
            try:
                pid = int(pid_text)
            except ValueError:
                continue
            if pid not in pids:
                pids.append(pid)
        return pids

    def is_pid_running(self, pid: int) -> bool:
        if os.name == "nt":
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=True,
            )
            return str(pid) in completed.stdout
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _terminate_pid(self, pid: int) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=True,
            )
            return
        os.kill(pid, 15)

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
