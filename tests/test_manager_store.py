from __future__ import annotations

import json
from pathlib import Path

from app.manager_config import ModelPresetInput
from app.manager_store import DEFAULT_MANAGER_PASSWORD, ManagerStore


def test_store_bootstraps_default_manager_and_empty_presets(tmp_path: Path) -> None:
    store = ManagerStore(
        manager_config_path=tmp_path / "manager-config.json",
        presets_path=tmp_path / "model-presets.json",
        runtime_dir=tmp_path / "runtime",
    )

    state = store.load_state()

    assert state.manager.manager_host == "127.0.0.1"
    assert state.manager.manager_port == 8899
    assert state.manager.runtime_dir == str(tmp_path / "runtime")
    assert state.presets.active_preset_id is None
    assert state.presets.presets == []
    assert state.manager.password_hash
    assert state.manager.password_salt
    assert state.manager.session_secret
    assert DEFAULT_MANAGER_PASSWORD == "admin123"


def test_upsert_preset_marks_only_one_active(tmp_path: Path) -> None:
    store = ManagerStore(
        manager_config_path=tmp_path / "manager-config.json",
        presets_path=tmp_path / "model-presets.json",
        runtime_dir=tmp_path / "runtime",
    )
    store.load_state()

    first = store.save_preset(
        ModelPresetInput(
            name="DeepSeek",
            provider="DeepSeek",
            base_url="https://api.deepseek.com/v1",
            chat_path="/chat/completions",
            api_key="key-1",
            model="deepseek-chat",
            proxy_host="127.0.0.1",
            proxy_port=8800,
            request_timeout_seconds=120.0,
            headers={},
            description="",
        )
    )
    second = store.save_preset(
        ModelPresetInput(
            name="Mimo",
            provider="Xiaomi",
            base_url="https://token-plan-cn.xiaomimimo.com/v1",
            chat_path="/chat/completions",
            api_key="key-2",
            model="mimo-v2.5-pro",
            proxy_host="127.0.0.1",
            proxy_port=8801,
            request_timeout_seconds=120.0,
            headers={},
            description="",
        )
    )

    state = store.set_active_preset(second.id)

    assert first.id != second.id
    assert state.presets.active_preset_id == second.id
    assert {preset.id for preset in state.presets.presets if preset.is_active} == {second.id}
    assert store.get_active_preset().name == "Mimo"


def test_store_imports_legacy_model_config_and_env_when_presets_missing(tmp_path: Path) -> None:
    legacy_model_config = tmp_path / "model-config.json"
    legacy_env = tmp_path / ".env"
    legacy_model_config.write_text(
        json.dumps(
            {
                "upstream_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
                "upstream_chat_path": "/chat/completions",
                "upstream_model": "mimo-v2.5-pro",
                "proxy_host": "127.0.0.1",
                "proxy_port": 8800,
                "request_timeout_seconds": 120,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    legacy_env.write_text(
        "RESPONSES_PROXY_UPSTREAM_API_KEY=test-upstream\nRESPONSES_PROXY_PROXY_API_KEY=test-proxy\n",
        encoding="utf-8",
    )

    store = ManagerStore(
        manager_config_path=tmp_path / "manager-config.json",
        presets_path=tmp_path / "model-presets.json",
        runtime_dir=tmp_path / "runtime",
        legacy_env_path=legacy_env,
        legacy_model_config_path=legacy_model_config,
    )

    state = store.load_state()

    assert state.manager.proxy_api_key == "test-proxy"
    assert state.presets.active_preset_id is not None
    assert len(state.presets.presets) == 1
    preset = state.presets.presets[0]
    assert preset.base_url == "https://token-plan-cn.xiaomimimo.com/v1"
    assert preset.model == "mimo-v2.5-pro"
    assert preset.api_key == "test-upstream"
    assert preset.is_active is True


def test_store_syncs_active_preset_to_env_and_model_files(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    model_config_path = tmp_path / "model-config.json"
    store = ManagerStore(
        manager_config_path=tmp_path / "manager-config.json",
        presets_path=tmp_path / "model-presets.json",
        runtime_dir=tmp_path / "runtime",
        legacy_env_path=env_path,
        legacy_model_config_path=model_config_path,
        project_root=tmp_path,
    )
    store.load_state()
    store.update_manager_config(proxy_api_key="proxy-key-123")
    preset = store.save_preset(
        ModelPresetInput(
            name="DeepSeek",
            provider="DeepSeek",
            base_url="https://api.deepseek.com/v1",
            chat_path="/chat/completions",
            api_key="upstream-key-abc",
            model="deepseek-v4-pro",
            proxy_host="127.0.0.1",
            proxy_port=8811,
            request_timeout_seconds=45.0,
            headers={"X-Provider": "deepseek"},
            description="",
            api_key_header_name="Authorization",
            api_key_prefix="Bearer",
        )
    )

    result = store.sync_active_files(preset.id)

    env_values = _read_env_file(env_path)
    assert result["launch_path"] == str(tmp_path / "runtime" / "proxy-launch.json")
    assert env_values["RESPONSES_PROXY_UPSTREAM_BASE_URL"] == "https://api.deepseek.com/v1"
    assert env_values["RESPONSES_PROXY_UPSTREAM_CHAT_PATH"] == "/chat/completions"
    assert env_values["RESPONSES_PROXY_UPSTREAM_MODEL"] == "deepseek-v4-pro"
    assert env_values["RESPONSES_PROXY_UPSTREAM_API_KEY"] == "upstream-key-abc"
    assert env_values["RESPONSES_PROXY_PROXY_API_KEY"] == "proxy-key-123"
    assert env_values["RESPONSES_PROXY_UPSTREAM_API_KEY_PREFIX"] == "Bearer"
    assert env_values["RESPONSES_PROXY_REQUEST_TIMEOUT_SECONDS"] == "45.0"

    model_config = json.loads(model_config_path.read_text(encoding="utf-8"))
    runtime_launch = json.loads((tmp_path / "runtime" / "proxy-launch.json").read_text(encoding="utf-8"))
    assert model_config == runtime_launch
    assert model_config["proxy_port"] == 8811
    assert model_config["upstream_headers"] == {"X-Provider": "deepseek"}


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values
