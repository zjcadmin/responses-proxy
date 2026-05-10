from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

try:
    from scripts.stop_proxy import find_listening_pids, parse_listening_pids, stop_process
except ImportError:
    from stop_proxy import find_listening_pids, parse_listening_pids, stop_process

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stop the manager UI by configured port.")
    parser.add_argument(
        "--config",
        default="manager-config.json",
        help="Path to the manager config JSON file. Defaults to manager-config.json in the project root.",
    )
    return parser.parse_args()


def load_manager_port(config_path: Path) -> int:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    port = payload.get("manager_port", 8899)
    try:
        return int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid manager_port value in {config_path}.") from exc


def main() -> int:
    args = parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    if not config_path.exists():
        print(f"Manager config not found: {config_path}", file=sys.stderr)
        return 1

    port = load_manager_port(config_path)
    pids = find_listening_pids(port)

    if not pids:
        print(f"No process is listening on port {port}.")
        return 0

    for pid in pids:
        stop_process(pid)
        print(f"Stopped process on port {port}: PID {pid}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
