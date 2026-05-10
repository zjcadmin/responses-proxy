from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import LaunchConfig, load_launch_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the responses proxy from a model config file.")
    parser.add_argument(
        "--config",
        default="model-config.json",
        help="Path to the model config JSON file. Defaults to model-config.json in the project root.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate config and print the resolved runtime settings without starting the server.",
    )
    return parser.parse_args()


def apply_launch_config(config: LaunchConfig) -> None:
    for key, value in config.to_env().items():
        os.environ[key] = value


def resolved_runtime_payload(config: LaunchConfig) -> dict[str, object]:
    return {
        "config_file_values": config.model_dump(),
        "effective_env": config.to_env(),
        "dotenv_file": ".env",
        "secrets_required": [
            "RESPONSES_PROXY_UPSTREAM_API_KEY",
            "RESPONSES_PROXY_PROXY_API_KEY",
        ],
    }


def ensure_port_available(host: str, port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError as exc:
        raise RuntimeError(
            f"Port {port} on {host} is already in use. Close the existing proxy first with stop-proxy.bat."
        ) from exc
    finally:
        sock.close()


def main() -> int:
    args = parse_args()
    project_root = PROJECT_ROOT
    os.chdir(project_root)

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = project_root / config_path

    config = load_launch_config(config_path)
    apply_launch_config(config)

    if args.check:
        print(json.dumps(resolved_runtime_payload(config), ensure_ascii=False, indent=2))
        return 0

    try:
        ensure_port_available(config.proxy_host, config.proxy_port)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Starting responses proxy on http://{config.proxy_host}:{config.proxy_port}")
    print(f"Using model config: {config_path}")
    print("Secrets are loaded from .env if present.")

    uvicorn.run(
        "app.main:app",
        host=config.proxy_host,
        port=config.proxy_port,
        reload=False,
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
