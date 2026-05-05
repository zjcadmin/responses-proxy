from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_launch_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stop the responses proxy by configured port.")
    parser.add_argument(
        "--config",
        default="model-config.json",
        help="Path to the model config JSON file. Defaults to model-config.json in the project root.",
    )
    return parser.parse_args()


def parse_listening_pids(netstat_output: str, port: int) -> list[int]:
    pids: list[int] = []
    for raw_line in netstat_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local_endpoint = parts[1]
        remote_endpoint = parts[2]
        pid_text = parts[-1]
        if _extract_port(local_endpoint) != port:
            continue
        if not remote_endpoint.endswith(":0"):
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def find_listening_pids(port: int) -> list[int]:
    completed = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=True,
    )
    return parse_listening_pids(completed.stdout, port)


def stop_process(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=True,
    )


def _extract_port(endpoint: str) -> int | None:
    if ":" not in endpoint:
        return None
    _, port_text = endpoint.rsplit(":", 1)
    try:
        return int(port_text)
    except ValueError:
        return None


def main() -> int:
    if os.name != "nt":
        print("stop_proxy.py currently supports Windows only.", file=sys.stderr)
        return 1

    args = parse_args()
    os.chdir(PROJECT_ROOT)

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path

    config = load_launch_config(config_path)
    pids = find_listening_pids(config.proxy_port)

    if not pids:
        print(f"No process is listening on port {config.proxy_port}.")
        return 0

    for pid in pids:
        stop_process(pid)
        print(f"Stopped process on port {config.proxy_port}: PID {pid}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
