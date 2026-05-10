from __future__ import annotations

import json
from pathlib import Path

from app import desktop_launcher
from app import process_manager as process_manager_module
from app.desktop_launcher import (
    ManagerServerController,
    build_desktop_environment,
    bootstrap_desktop_data_dir,
    configure_manager_endpoint,
    default_user_data_dir,
)
from app.manager_main import resolve_proxy_command
from app.process_manager import ProcessManager


def test_default_user_data_dir_uses_platform_conventions() -> None:
    assert default_user_data_dir(
        platform_name="Windows",
        env={"APPDATA": "C:/Users/test/AppData/Roaming"},
        home=Path("C:/Users/test"),
    ) == Path("C:/Users/test/AppData/Roaming") / "Responses Proxy"

    assert default_user_data_dir(
        platform_name="Darwin",
        env={},
        home=Path("/Users/test"),
    ) == Path("/Users/test/Library/Application Support/Responses Proxy")

    assert default_user_data_dir(
        platform_name="Linux",
        env={"XDG_DATA_HOME": "/home/test/.local/state"},
        home=Path("/home/test"),
    ) == Path("/home/test/.local/state/responses-proxy")

    assert default_user_data_dir(
        platform_name="Linux",
        env={},
        home=Path("/home/test"),
    ) == Path("/home/test/.local/share/responses-proxy")


def test_build_desktop_environment_routes_proxy_through_same_executable(tmp_path: Path) -> None:
    executable = tmp_path / "ResponsesProxy"
    env = build_desktop_environment(data_dir=tmp_path / "data", executable=executable)

    assert env["RESPONSES_PROXY_DATA_DIR"] == str(tmp_path / "data")
    assert json.loads(env["RESPONSES_PROXY_PROXY_COMMAND"]) == [str(executable), "--run-proxy"]


def test_process_manager_uses_proxy_command_override(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 2468

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        return FakeProcess()

    monkeypatch.setattr(process_manager_module.subprocess, "Popen", fake_popen)
    manager = ProcessManager(
        project_root=tmp_path,
        python_executable=Path("/usr/bin/python3"),
        runtime_dir=tmp_path / "runtime",
        proxy_command=["/Applications/Responses Proxy.app/Contents/MacOS/Responses Proxy", "--run-proxy"],
    )
    monkeypatch.setattr(manager, "ensure_port_available", lambda host, port: None)
    monkeypatch.setattr(manager, "_wait_for_listen", lambda host, port, timeout_seconds: True)

    launch_config = tmp_path / "runtime" / "proxy-launch.json"
    launch_config.write_text("{}", encoding="utf-8")

    status = manager.start_proxy(launch_config, host="127.0.0.1", port=8800)

    assert status.running is True
    assert captured["cwd"] == tmp_path
    assert captured["command"] == [
        "/Applications/Responses Proxy.app/Contents/MacOS/Responses Proxy",
        "--run-proxy",
        "--config",
        str(launch_config),
    ]


def test_manager_resolves_proxy_command_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("RESPONSES_PROXY_PROXY_COMMAND", '["/app/ResponsesProxy", "--run-proxy"]')

    assert resolve_proxy_command() == ["/app/ResponsesProxy", "--run-proxy"]


def test_desktop_manager_uses_pyinstaller_safe_uvicorn_logging(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeConfig:
        def __init__(self, app, **kwargs):  # noqa: ANN001
            captured["kwargs"] = kwargs

    class FakeServer:
        should_exit = False

        def __init__(self, config):  # noqa: ANN001
            self.config = config

        def run(self) -> None:
            return None

    class FakeThread:
        def __init__(self, target, name: str, daemon: bool):  # noqa: ANN001
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self) -> None:
            self.target()

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: int) -> None:
            return None

    monkeypatch.setattr(desktop_launcher.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(desktop_launcher.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(desktop_launcher.threading, "Thread", FakeThread)
    monkeypatch.setattr(desktop_launcher, "wait_for_url", lambda url: False)

    controller = ManagerServerController(data_dir=tmp_path, open_browser=False, manager_port=17777)

    assert controller.start() == "http://127.0.0.1:17777"
    assert captured["kwargs"]["port"] == 17777
    assert captured["kwargs"]["log_config"] is None


def test_desktop_manager_endpoint_override_is_persisted(tmp_path: Path) -> None:
    state = configure_manager_endpoint(tmp_path, manager_host="127.0.0.1", manager_port=18888)

    assert state.manager.manager_host == "127.0.0.1"
    assert state.manager.manager_port == 18888
    manager_config = json.loads((tmp_path / "manager-config.json").read_text(encoding="utf-8"))
    assert manager_config["manager_port"] == 18888


def test_desktop_bootstrap_migrates_non_empty_project_presets_without_overwriting_password(tmp_path: Path) -> None:
    data_dir = tmp_path / "appdata"
    legacy_dir = tmp_path / "project"
    data_dir.mkdir()
    legacy_dir.mkdir()

    (data_dir / "manager-config.json").write_text(
        '{"manager_port": 18888, "password_hash": "keep", "web_search_backend": "disabled", "web_search_max_results": 5}',
        encoding="utf-8",
    )
    (data_dir / "model-presets.json").write_text('{"active_preset_id": null, "presets": []}', encoding="utf-8")
    (legacy_dir / "manager-config.json").write_text(
        '{"manager_port": 8899, "password_hash": "old", "web_search_backend": "searxng", "web_search_max_results": 10}',
        encoding="utf-8",
    )
    (legacy_dir / "model-presets.json").write_text(
        json.dumps(
            {
                "active_preset_id": "preset_1",
                "presets": [
                    {
                        "id": "preset_1",
                        "name": "Mimo",
                        "provider": "xiaomi",
                        "base_url": "https://example.test/v1",
                        "chat_path": "/chat/completions",
                        "api_key": "secret",
                        "model": "mimo-v2.5-pro",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (legacy_dir / ".env").write_text("RESPONSES_PROXY_UPSTREAM_MODEL=mimo-v2.5-pro\n", encoding="utf-8")
    (legacy_dir / "model-config.json").write_text('{"upstream_model": "mimo-v2.5-pro"}', encoding="utf-8")

    bootstrap_desktop_data_dir(data_dir, legacy_dirs=[legacy_dir])

    migrated_presets = json.loads((data_dir / "model-presets.json").read_text(encoding="utf-8"))
    manager_config = json.loads((data_dir / "manager-config.json").read_text(encoding="utf-8"))
    assert migrated_presets["active_preset_id"] == "preset_1"
    assert migrated_presets["presets"][0]["model"] == "mimo-v2.5-pro"
    assert manager_config["password_hash"] == "keep"
    assert manager_config["manager_port"] == 18888
    assert manager_config["web_search_backend"] == "searxng"
    assert manager_config["web_search_max_results"] == 10
    assert (data_dir / ".env").exists()
    assert (data_dir / "model-config.json").exists()


def test_desktop_stop_stops_services_and_manager_thread(tmp_path: Path) -> None:
    stopped_paths: list[Path] = []
    joined: list[int] = []

    class FakeServer:
        should_exit = False

    class FakeThread:
        def is_alive(self) -> bool:
            return True

        def join(self, timeout: int) -> None:
            joined.append(timeout)

    controller = ManagerServerController(
        data_dir=tmp_path,
        open_browser=False,
        service_stopper=lambda path: stopped_paths.append(path),
    )
    controller.server = FakeServer()
    controller.thread = FakeThread()

    controller.stop()

    assert stopped_paths == [tmp_path]
    assert controller.server.should_exit is True
    assert joined == [8]


def test_desktop_packaging_entrypoints_exist() -> None:
    project_root = Path(__file__).resolve().parents[1]

    for path, expected in {
        "build-desktop.bat": "pyinstaller",
        "build-desktop.ps1": "pyinstaller",
        "build-desktop.sh": "pyinstaller",
        "packaging/responses-proxy.spec": "app/desktop_launcher.py",
    }.items():
        source = (project_root / path).read_text(encoding="utf-8")
        assert expected.lower() in source.lower()

    launcher_source = (project_root / "app" / "desktop_launcher.py").read_text(encoding="utf-8")
    assert "停止服务" in launcher_source
    assert "退出程序" in launcher_source


def test_windows_scripts_use_project_python_resolver_not_old_monitoring_path() -> None:
    project_root = Path(__file__).resolve().parents[1]
    for path in [
        "start-manager.bat",
        "start-manager.ps1",
        "start-proxy.bat",
        "start-proxy.ps1",
        "stop-manager.bat",
        "stop-manager.ps1",
        "stop-proxy.bat",
        "stop-proxy.ps1",
    ]:
        source = (project_root / path).read_text(encoding="utf-8")
        assert "monitoring-platform" not in source
        assert "resolve-python.ps1" in source or "Resolve-Python" in source


def test_desktop_window_layout_is_resizable_and_scroll_safe() -> None:
    launcher_source = (Path(__file__).resolve().parents[1] / "app" / "desktop_launcher.py").read_text(encoding="utf-8")

    assert 'root.geometry("980x640")' in launcher_source
    assert "root.minsize(860, 560)" in launcher_source
    assert "root.resizable(True, True)" in launcher_source
    assert "tk.Canvas(" in launcher_source
    assert "ttk.Scrollbar(" in launcher_source
    assert "scrollregion" in launcher_source
    assert "操作面板" in launcher_source
