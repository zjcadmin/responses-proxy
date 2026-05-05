from __future__ import annotations

import os
from pathlib import Path
import sys

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.manager_store import DEFAULT_MANAGER_PASSWORD, ManagerStore


def main() -> int:
    os.chdir(PROJECT_ROOT)
    manager_config_path = PROJECT_ROOT / "manager-config.json"
    first_run = not manager_config_path.exists()

    store = ManagerStore(
        manager_config_path=manager_config_path,
        presets_path=PROJECT_ROOT / "model-presets.json",
        runtime_dir=PROJECT_ROOT / "runtime",
        legacy_env_path=PROJECT_ROOT / ".env",
        legacy_model_config_path=PROJECT_ROOT / "model-config.json",
        project_root=PROJECT_ROOT,
    )
    state = store.load_state()

    print(f"Starting manager on http://{state.manager.manager_host}:{state.manager.manager_port}")
    if first_run:
        print(f"First run detected. Default manager password: {DEFAULT_MANAGER_PASSWORD}")
        print("You can now configure presets in the web UI.")

    uvicorn.run(
        "app.manager_main:app",
        host=state.manager.manager_host,
        port=state.manager.manager_port,
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
