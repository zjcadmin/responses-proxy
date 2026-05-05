from __future__ import annotations

import json
from pathlib import Path
import tempfile

from app.config import load_launch_config, load_settings


def test_load_launch_config_reads_model_file() -> None:
    config_path = _write_config_file(
        {
            "upstream_base_url": "https://api.deepseek.com/v1",
            "upstream_chat_path": "/chat/completions",
            "upstream_model": "deepseek-chat",
            "proxy_host": "127.0.0.1",
            "proxy_port": 8010,
            "request_timeout_seconds": 45,
        }
    )
    try:
        config = load_launch_config(config_path)

        assert config.upstream_base_url == "https://api.deepseek.com/v1"
        assert config.proxy_port == 8010
        assert config.request_timeout_seconds == 45
    finally:
        config_path.unlink(missing_ok=True)


def test_launch_config_can_be_mapped_to_environment_variables() -> None:
    config_path = _write_config_file(
        {
            "upstream_base_url": "https://api.deepseek.com/v1",
            "upstream_model": "deepseek-reasoner",
            "proxy_host": "0.0.0.0",
            "proxy_port": 9000,
        }
    )
    try:
        config = load_launch_config(config_path)

        assert config.to_env() == {
            "RESPONSES_PROXY_UPSTREAM_BASE_URL": "https://api.deepseek.com/v1",
            "RESPONSES_PROXY_UPSTREAM_CHAT_PATH": "/chat/completions",
            "RESPONSES_PROXY_UPSTREAM_MODEL": "deepseek-reasoner",
            "RESPONSES_PROXY_UPSTREAM_API_KEY": "",
            "RESPONSES_PROXY_PROXY_API_KEY": "",
            "RESPONSES_PROXY_UPSTREAM_HEADERS": "{}",
            "RESPONSES_PROXY_UPSTREAM_API_KEY_HEADER_NAME": "Authorization",
            "RESPONSES_PROXY_UPSTREAM_API_KEY_PREFIX": "Bearer ",
            "RESPONSES_PROXY_REQUEST_TIMEOUT_SECONDS": "120.0",
        }
    finally:
        config_path.unlink(missing_ok=True)


def test_load_settings_reads_synced_model_config_defaults(tmp_path: Path, monkeypatch) -> None:
    synced_path = tmp_path / "model-config.json"
    synced_path.write_text(
        json.dumps(
            {
                "upstream_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
                "upstream_chat_path": "/chat/completions",
                "upstream_model": "mimo-v2.5-pro",
                "upstream_api_key": "sync-key",
                "upstream_headers": {"X-Provider": "mimo"},
                "upstream_api_key_header_name": "Authorization",
                "upstream_api_key_prefix": "Bearer",
                "request_timeout_seconds": 90.0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.config.DEFAULT_MODEL_CONFIG_PATH", synced_path)

    settings = load_settings()

    assert settings.upstream_base_url == "https://token-plan-cn.xiaomimimo.com/v1"
    assert settings.upstream_model == "mimo-v2.5-pro"
    assert settings.upstream_api_key == "sync-key"
    assert settings.upstream_headers == {"X-Provider": "mimo"}
    assert settings.request_timeout_seconds == 90.0


def _write_config_file(payload: dict[str, object]) -> Path:
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="launch-config-",
        dir=Path(__file__).resolve().parent,
        delete=False,
        encoding="utf-8",
    )
    try:
        temp_file.write(json.dumps(payload))
        temp_file.close()
        return Path(temp_file.name)
    except Exception:
        temp_file.close()
        Path(temp_file.name).unlink(missing_ok=True)
        raise
