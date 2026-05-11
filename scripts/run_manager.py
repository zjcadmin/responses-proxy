from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.manager_store import DEFAULT_MANAGER_PASSWORD, ManagerStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the responses proxy manager UI.")
    parser.add_argument(
        "--data-dir",
        default=os.getenv("RESPONSES_PROXY_DATA_DIR", ""),
        help="Directory for manager-config.json, model-presets.json, model-config.json, .env, and runtime logs.",
    )
    return parser.parse_args()


def resolve_data_root(data_dir: str) -> Path:
    if not data_dir:
        return PROJECT_ROOT
    path = Path(data_dir)
    return path if path.is_absolute() else PROJECT_ROOT / path


def main() -> int:
    args = parse_args()
    data_root = resolve_data_root(args.data_dir)
    data_root.mkdir(parents=True, exist_ok=True)
    os.environ["RESPONSES_PROXY_DATA_DIR"] = str(data_root)
    os.environ.setdefault("RESPONSES_PROXY_MANAGER_REQUEST_LOGS", "1")
    os.chdir(PROJECT_ROOT)

    manager_config_path = data_root / "manager-config.json"
    first_run = not manager_config_path.exists()

    store = ManagerStore(
        manager_config_path=manager_config_path,
        presets_path=data_root / "model-presets.json",
        runtime_dir=data_root / "runtime",
        legacy_env_path=data_root / ".env",
        legacy_model_config_path=data_root / "model-config.json",
        project_root=data_root,
    )
    state = store.load_state()

    print(f"Starting manager on http://{state.manager.manager_host}:{state.manager.manager_port}", flush=True)
    print("Manager request logs are enabled. Open the web UI to see GET/POST lines here.", flush=True)
    if first_run:
        print(f"First run detected. Default manager password: {DEFAULT_MANAGER_PASSWORD}", flush=True)
        print("You can now configure presets in the web UI.", flush=True)

    uvicorn.run(
        "app.manager_main:app",
        host=state.manager.manager_host,
        port=state.manager.manager_port,
        reload=False,
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
