from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import sys
import threading
import time
from typing import Callable, Mapping
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

import uvicorn

APP_NAME = "Responses Proxy"
APP_DIR_NAME = "responses-proxy"
DESKTOP_BOOTSTRAP_FILES = ("model-presets.json", ".env", "model-config.json", "manager-config.json")
PRESERVED_MANAGER_BOOTSTRAP_KEYS = {
    "manager_host",
    "manager_port",
    "password_hash",
    "password_salt",
    "session_secret",
    "runtime_dir",
}


def default_user_data_dir(
    *,
    platform_name: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    platform_name = platform_name or platform.system()
    env = env or os.environ
    home = home or Path.home()

    if platform_name == "Windows":
        return Path(env.get("APPDATA") or home / "AppData" / "Roaming") / APP_NAME
    if platform_name == "Darwin":
        return home / "Library" / "Application Support" / APP_NAME

    xdg_data_home = env.get("XDG_DATA_HOME", "").strip()
    if xdg_data_home:
        return Path(xdg_data_home) / APP_DIR_NAME
    return home / ".local" / "share" / APP_DIR_NAME


def build_desktop_environment(*, data_dir: Path, executable: Path) -> dict[str, str]:
    return {
        "RESPONSES_PROXY_DATA_DIR": str(data_dir),
        "RESPONSES_PROXY_PROXY_COMMAND": json.dumps([str(executable), "--run-proxy"], ensure_ascii=False),
    }


def _has_presets(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    presets = data.get("presets")
    return isinstance(presets, list) and len(presets) > 0


def _merge_manager_config_from_legacy(source: Path, target: Path) -> None:
    try:
        source_data = json.loads(source.read_text(encoding="utf-8"))
        target_data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(source_data, dict) or not isinstance(target_data, dict):
        return

    merged = dict(target_data)
    for key, value in source_data.items():
        if key in PRESERVED_MANAGER_BOOTSTRAP_KEYS:
            continue
        merged[key] = value
    target.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_legacy_data_dirs(data_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    env_legacy = os.getenv("RESPONSES_PROXY_LEGACY_DATA_DIR", "").strip()
    if env_legacy:
        candidates.append(Path(env_legacy))

    candidates.extend([Path.cwd(), Path(__file__).resolve().parents[1]])

    executable_dir = Path(sys.executable).resolve().parent
    candidates.extend([executable_dir, executable_dir.parent])

    resolved_data_dir = data_dir.resolve()
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = str(resolved).lower()
        if key in seen or resolved == resolved_data_dir:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def bootstrap_desktop_data_dir(data_dir: Path, *, legacy_dirs: list[Path] | None = None) -> Path | None:
    data_dir.mkdir(parents=True, exist_ok=True)
    legacy_dirs = legacy_dirs or default_legacy_data_dirs(data_dir)

    source_dir = next((candidate for candidate in legacy_dirs if _has_presets(candidate / "model-presets.json")), None)
    if source_dir is None:
        return None

    target_presets = data_dir / "model-presets.json"
    should_copy_presets = not _has_presets(target_presets)
    for file_name in DESKTOP_BOOTSTRAP_FILES:
        source = source_dir / file_name
        target = data_dir / file_name
        if not source.exists():
            continue
        if file_name == "model-presets.json":
            if should_copy_presets:
                shutil.copy2(source, target)
            continue
        if file_name == "manager-config.json" and target.exists():
            if should_copy_presets:
                _merge_manager_config_from_legacy(source, target)
            continue
        if not target.exists():
            shutil.copy2(source, target)

    return source_dir


def wait_for_url(url: str, *, timeout_seconds: float = 15.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=0.5) as response:
                return 200 <= response.status < 500
        except URLError:
            time.sleep(0.2)
    return False


class ManagerServerController:
    def __init__(
        self,
        *,
        data_dir: Path,
        open_browser: bool = True,
        manager_host: str | None = None,
        manager_port: int | None = None,
        service_stopper: Callable[[Path], None] | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.open_browser = open_browser
        self.manager_host = manager_host
        self.manager_port = manager_port
        self.service_stopper = service_stopper or stop_desktop_services
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None
        self.url: str = ""

    def start(self) -> str:
        if self.thread is not None and self.thread.is_alive():
            if self.open_browser and self.url:
                webbrowser.open(self.url)
            return self.url

        self.data_dir.mkdir(parents=True, exist_ok=True)
        bootstrap_desktop_data_dir(self.data_dir)
        os.environ.update(build_desktop_environment(data_dir=self.data_dir, executable=Path(sys.executable)))

        from app.manager_main import create_manager_app

        store = create_desktop_manager_store(self.data_dir)
        state = configure_manager_endpoint(
            self.data_dir,
            manager_host=self.manager_host,
            manager_port=self.manager_port,
            store=store,
        )
        self.url = f"http://{state.manager.manager_host}:{state.manager.manager_port}"
        config = uvicorn.Config(
            create_manager_app(store=store, project_root=self.data_dir),
            host=state.manager.manager_host,
            port=state.manager.manager_port,
            log_level="info",
            log_config=None,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="responses-proxy-manager", daemon=True)
        self.thread.start()

        if wait_for_url(self.url) and self.open_browser:
            webbrowser.open(self.url)
        return self.url

    def stop(self) -> None:
        try:
            self.service_stopper(self.data_dir)
        finally:
            if self.server is not None:
                self.server.should_exit = True
            if self.thread is not None and self.thread.is_alive():
                self.thread.join(timeout=8)


def create_desktop_manager_store(data_dir: Path):
    from app.manager_store import ManagerStore

    return ManagerStore(
        manager_config_path=data_dir / "manager-config.json",
        presets_path=data_dir / "model-presets.json",
        runtime_dir=data_dir / "runtime",
        legacy_env_path=data_dir / ".env",
        legacy_model_config_path=data_dir / "model-config.json",
        project_root=data_dir,
    )


def configure_manager_endpoint(
    data_dir: Path,
    *,
    manager_host: str | None = None,
    manager_port: int | None = None,
    store=None,  # noqa: ANN001
):
    store = store or create_desktop_manager_store(data_dir)
    state = store.load_state()
    updates: dict[str, object] = {}
    if manager_host is not None and manager_host.strip():
        updates["manager_host"] = manager_host.strip()
    if manager_port is not None:
        updates["manager_port"] = int(manager_port)
    if updates:
        store.update_manager_config(**updates)
        state = store.load_state()
    return state


def stop_desktop_services(data_dir: Path) -> None:
    from app.manager_config import resolve_runtime_dir
    from app.process_manager import ProcessManager

    store = create_desktop_manager_store(data_dir)
    state = store.load_state()
    active = next(
        (preset for preset in state.presets.presets if preset.id == state.presets.active_preset_id),
        None,
    )
    process_manager = ProcessManager(
        project_root=Path(sys.executable).parent,
        python_executable=Path(sys.executable),
        runtime_dir=resolve_runtime_dir(data_dir, state.manager),
    )
    process_manager.stop_proxy(
        host=active.proxy_host if active else None,
        port=active.proxy_port if active else None,
    )


def run_proxy_from_desktop(config_path: str | None) -> int:
    from scripts import run_proxy

    argv = [sys.argv[0]]
    if config_path:
        argv.extend(["--config", config_path])
    original_argv = sys.argv
    try:
        sys.argv = argv
        return run_proxy.main()
    finally:
        sys.argv = original_argv


def run_headless(
    *,
    data_dir: Path,
    open_browser: bool,
    manager_host: str | None = None,
    manager_port: int | None = None,
) -> int:
    controller = ManagerServerController(
        data_dir=data_dir,
        open_browser=open_browser,
        manager_host=manager_host,
        manager_port=manager_port,
    )
    url = controller.start()
    print(f"{APP_NAME} manager started: {url}")
    print("Press Ctrl+C to stop.")

    stop_event = threading.Event()

    def request_stop(signum, frame) -> None:  # noqa: ANN001
        stop_event.set()

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop) if hasattr(signal, "SIGTERM") else None
    try:
        while not stop_event.is_set():
            time.sleep(0.2)
    finally:
        controller.stop()
        signal.signal(signal.SIGINT, previous_sigint)
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)
    return 0


def run_tk_app(*, data_dir: Path) -> int:
    import tkinter as tk
    from tkinter import ttk
    from tkinter import messagebox

    bootstrap_desktop_data_dir(data_dir)
    initial_state = configure_manager_endpoint(data_dir)
    controller_ref: dict[str, ManagerServerController | None] = {"controller": None}

    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("980x640")
    root.minsize(860, 560)
    root.resizable(True, True)
    root.configure(bg="#eaf1f8")

    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Vertical.TScrollbar", troughcolor="#eef3f8", background="#cbd8e6", bordercolor="#eef3f8")

    status = tk.StringVar(value="未启动")
    url_text = tk.StringVar(value="管理台地址会在启动后显示")
    host_text = tk.StringVar(value=initial_state.manager.manager_host)
    port_text = tk.StringVar(value=str(initial_state.manager.manager_port))

    shell = tk.Frame(root, bg="#eaf1f8")
    shell.pack(fill="both", expand=True)

    sidebar = tk.Frame(shell, bg="#0f1d32", width=250)
    sidebar.pack(side="left", fill="y")
    sidebar.pack_propagate(False)

    sidebar_top = tk.Frame(sidebar, bg="#0f1d32", padx=24, pady=28)
    sidebar_top.pack(fill="x")
    logo = tk.Canvas(sidebar_top, width=64, height=64, bg="#0f1d32", highlightthickness=0)
    logo.create_oval(5, 5, 59, 59, fill="#dbeafe", outline="#8fb8ff", width=2)
    logo.create_text(32, 32, text="R", fill="#1d4ed8", font=("Segoe UI", 24, "bold"))
    logo.pack(anchor="w")
    tk.Label(
        sidebar_top,
        text="Responses\nProxy",
        bg="#0f1d32",
        fg="#f8fbff",
        justify="left",
        font=("Segoe UI", 24, "bold"),
    ).pack(anchor="w", pady=(18, 6))
    tk.Label(
        sidebar_top,
        text="本地 Responses API 代理服务启动器",
        bg="#0f1d32",
        fg="#94a3b8",
        justify="left",
        wraplength=190,
        font=("Segoe UI", 10),
    ).pack(anchor="w")

    sidebar_card = tk.Frame(sidebar, bg="#172a46", padx=18, pady=16)
    sidebar_card.pack(fill="x", padx=18, pady=(16, 0))
    tk.Label(sidebar_card, text="本机管理台", bg="#172a46", fg="#93c5fd", font=("Segoe UI", 9, "bold")).pack(anchor="w")
    tk.Label(
        sidebar_card,
        text="启动后自动打开浏览器，可在 Web 端继续配置模型、端口和密码。",
        bg="#172a46",
        fg="#dbeafe",
        wraplength=188,
        justify="left",
        font=("Segoe UI", 9),
    ).pack(anchor="w", pady=(8, 0))

    sidebar_footer = tk.Frame(sidebar, bg="#0f1d32", padx=24, pady=20)
    sidebar_footer.pack(side="bottom", fill="x")
    tk.Label(sidebar_footer, text="Private Local Runtime", bg="#0f1d32", fg="#64748b", font=("Segoe UI", 9)).pack(anchor="w")

    main = tk.Frame(shell, bg="#f3f7fb")
    main.pack(side="left", fill="both", expand=True)

    action_bar = tk.Frame(main, bg="#ffffff", padx=22, pady=16, highlightbackground="#dce7f3", highlightthickness=1)
    action_bar.pack(side="bottom", fill="x")

    action_title = tk.Frame(action_bar, bg="#ffffff")
    action_title.pack(side="left", fill="y", padx=(0, 18))
    tk.Label(action_title, text="操作面板", bg="#ffffff", fg="#10233f", font=("Segoe UI", 11, "bold")).pack(anchor="w")
    tk.Label(action_title, textvariable=status, bg="#ffffff", fg="#64748b", font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))

    buttons = tk.Frame(action_bar, bg="#ffffff")
    buttons.pack(side="right")

    body = tk.Frame(main, bg="#f3f7fb")
    body.pack(side="top", fill="both", expand=True)
    canvas = tk.Canvas(body, bg="#f3f7fb", highlightthickness=0)
    scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview, style="Vertical.TScrollbar")
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    content = tk.Frame(canvas, bg="#f3f7fb", padx=28, pady=24)
    content_window = canvas.create_window((0, 0), window=content, anchor="nw")

    def refresh_scroll_region(_: object | None = None) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))

    def fit_content_width(event: object) -> None:
        width = getattr(event, "width", 0)
        canvas.itemconfigure(content_window, width=width)

    content.bind("<Configure>", refresh_scroll_region)
    canvas.bind("<Configure>", fit_content_width)

    def on_mousewheel(event: object) -> None:
        delta = int(getattr(event, "delta", 0))
        if delta:
            canvas.yview_scroll(int(-1 * (delta / 120)), "units")

    canvas.bind_all("<MouseWheel>", on_mousewheel)

    def card(parent: tk.Misc, *, bg: str = "#ffffff", padx: int = 22, pady: int = 18) -> tk.Frame:
        panel = tk.Frame(parent, bg=bg, padx=padx, pady=pady, highlightbackground="#dce7f3", highlightthickness=1)
        panel.pack(fill="x", pady=(0, 18))
        return panel

    hero = card(content, padx=24, pady=22)
    hero_grid = tk.Frame(hero, bg="#ffffff")
    hero_grid.pack(fill="x")
    hero_grid.grid_columnconfigure(0, weight=1)
    tk.Label(
        hero_grid,
        text=APP_NAME,
        bg="#ffffff",
        fg="#0f172a",
        font=("Segoe UI", 28, "bold"),
    ).grid(row=0, column=0, sticky="w")
    tk.Label(
        hero_grid,
        text="一键启动本地管理台，并在浏览器中配置模型预设、端口、Hosted Tools 和登录密码。",
        bg="#ffffff",
        fg="#64748b",
        wraplength=560,
        justify="left",
        font=("Segoe UI", 10),
    ).grid(row=1, column=0, sticky="w", pady=(8, 0))
    tk.Label(
        hero_grid,
        text="桌面端",
        bg="#e0f2fe",
        fg="#0369a1",
        padx=12,
        pady=6,
        font=("Segoe UI", 9, "bold"),
    ).grid(row=0, column=1, sticky="ne", padx=(16, 0))

    status_panel = card(content, bg="#f8fbff", padx=20, pady=18)
    status_panel.grid_columnconfigure(1, weight=1)
    tk.Label(status_panel, text="服务状态", bg="#f8fbff", fg="#64748b", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
    tk.Label(status_panel, textvariable=status, bg="#f8fbff", fg="#0f172a", font=("Segoe UI", 24, "bold")).grid(row=1, column=0, sticky="w", pady=(4, 0))
    tk.Label(status_panel, textvariable=url_text, bg="#f8fbff", fg="#2563eb", font=("Segoe UI", 10, "bold"), wraplength=420).grid(
        row=1,
        column=1,
        sticky="e",
        padx=(18, 0),
    )

    endpoint_card = card(content, padx=22, pady=20)
    tk.Label(endpoint_card, text="管理台监听地址", bg="#ffffff", fg="#10233f", font=("Segoe UI", 13, "bold")).grid(
        row=0,
        column=0,
        columnspan=2,
        sticky="w",
    )
    tk.Label(
        endpoint_card,
        text="本机使用 127.0.0.1；需要局域网、NAS 或 Docker 外部访问时可填 0.0.0.0。",
        bg="#ffffff",
        fg="#64748b",
        font=("Segoe UI", 9),
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 16))
    tk.Label(endpoint_card, text="管理台地址", bg="#ffffff", fg="#334155", font=("Segoe UI", 9, "bold")).grid(row=2, column=0, sticky="w")
    tk.Entry(
        endpoint_card,
        textvariable=host_text,
        width=30,
        bg="#f8fafc",
        fg="#0f172a",
        insertbackground="#0f172a",
        relief="solid",
        bd=1,
        font=("Segoe UI", 11),
    ).grid(row=3, column=0, sticky="ew", padx=(0, 18), pady=(7, 0), ipady=8)
    tk.Label(endpoint_card, text="管理台端口", bg="#ffffff", fg="#334155", font=("Segoe UI", 9, "bold")).grid(row=2, column=1, sticky="w")
    tk.Entry(
        endpoint_card,
        textvariable=port_text,
        width=14,
        bg="#f8fafc",
        fg="#0f172a",
        insertbackground="#0f172a",
        relief="solid",
        bd=1,
        font=("Segoe UI", 11),
    ).grid(row=3, column=1, sticky="ew", pady=(7, 0), ipady=8)
    endpoint_card.grid_columnconfigure(0, weight=2)
    endpoint_card.grid_columnconfigure(1, weight=1)

    path_card = card(content, bg="#ffffff", padx=22, pady=18)
    tk.Label(path_card, text="配置目录", bg="#ffffff", fg="#10233f", font=("Segoe UI", 13, "bold")).pack(anchor="w")
    tk.Label(
        path_card,
        text=str(data_dir),
        bg="#ffffff",
        fg="#64748b",
        wraplength=620,
        justify="left",
        font=("Segoe UI", 9),
    ).pack(anchor="w", pady=(8, 0))
    tk.Label(
        path_card,
        text="关闭桌面程序会自动停止管理台和代理进程；如需常驻，请保持此窗口打开。",
        bg="#ffffff",
        fg="#94a3b8",
        wraplength=620,
        justify="left",
        font=("Segoe UI", 9),
    ).pack(anchor="w", pady=(8, 0))

    help_card = card(content, bg="#edf6ff", padx=22, pady=16)
    tk.Label(help_card, text="使用流程", bg="#edf6ff", fg="#1e3a8a", font=("Segoe UI", 12, "bold")).pack(anchor="w")
    tk.Label(
        help_card,
        text="1. 修改管理台地址或端口；2. 点击启动服务；3. 在浏览器登录后管理模型预设和密码。",
        bg="#edf6ff",
        fg="#475569",
        wraplength=640,
        justify="left",
        font=("Segoe UI", 9),
    ).pack(anchor="w", pady=(8, 0))

    spacer = tk.Frame(content, height=10, bg="#f3f7fb")
    spacer.pack(fill="x")

    def set_running_controls(running: bool) -> None:
        start_button.configure(state="disabled" if running else "normal")
        stop_button.configure(state="normal" if running else "disabled")
        open_button.configure(state="normal" if running else "disabled")

    def make_button(parent: tk.Misc, text: str, command, bg: str, fg: str = "#ffffff", width: int = 12):  # noqa: ANN001
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=bg,
            activeforeground=fg,
            relief="flat",
            bd=0,
            width=width,
            padx=12,
            pady=10,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
        )

    def start() -> None:
        existing = controller_ref.get("controller")
        if existing is not None and existing.thread is not None and existing.thread.is_alive():
            open_console()
            return
        try:
            manager_port = int(port_text.get().strip())
            if manager_port < 1 or manager_port > 65535:
                raise ValueError("管理台端口必须在 1 到 65535 之间")
        except ValueError as exc:
            messagebox.showerror(APP_NAME, f"端口无效：{exc}")
            return

        status.set("启动中")
        url_text.set("正在启动管理台，请稍候...")
        root.update_idletasks()
        controller = ManagerServerController(
            data_dir=data_dir,
            open_browser=True,
            manager_host=host_text.get().strip() or "127.0.0.1",
            manager_port=manager_port,
        )
        controller_ref["controller"] = controller
        try:
            url = controller.start()
        except Exception as exc:  # pragma: no cover - UI safety net
            controller_ref["controller"] = None
            status.set("启动失败")
            url_text.set("请检查端口是否被占用，或查看终端日志。")
            set_running_controls(False)
            messagebox.showerror(APP_NAME, f"启动失败：{exc}")
            return
        status.set("运行中")
        url_text.set(url)
        set_running_controls(True)

    def stop_service() -> None:
        controller = controller_ref.get("controller")
        if controller is not None:
            controller.stop()
        controller_ref["controller"] = None
        status.set("已停止")
        url_text.set("服务已停止，可修改端口后重新启动")
        set_running_controls(False)

    def open_console() -> None:
        controller = controller_ref.get("controller")
        if controller is not None and controller.url:
            webbrowser.open(controller.url)
            return
        messagebox.showinfo(APP_NAME, "服务尚未启动，请先点击“启动服务”。")

    def stop_and_exit() -> None:
        stop_service()
        root.destroy()

    start_button = make_button(buttons, "启动服务", start, "#2563eb")
    stop_button = make_button(buttons, "停止服务", stop_service, "#ef4444")
    open_button = make_button(buttons, "打开控制台", open_console, "#dbeafe", "#1d4ed8")
    exit_button = make_button(buttons, "退出程序", stop_and_exit, "#eef2f7", "#334155")
    start_button.pack(side="left", padx=(0, 10))
    stop_button.pack(side="left", padx=(0, 10))
    open_button.pack(side="left", padx=(0, 10))
    exit_button.pack(side="left")
    set_running_controls(False)

    root.protocol("WM_DELETE_WINDOW", stop_and_exit)
    root.mainloop()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Responses Proxy desktop launcher.")
    parser.add_argument("--data-dir", default="", help="Directory for desktop app configuration and runtime files.")
    parser.add_argument("--headless", action="store_true", help="Start the manager without the desktop window.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the web console automatically.")
    parser.add_argument("--manager-host", default="", help="Override the manager bind host before starting.")
    parser.add_argument("--manager-port", type=int, default=None, help="Override the manager port before starting.")
    parser.add_argument("--run-proxy", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--config", default="", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.run_proxy:
        return run_proxy_from_desktop(args.config or None)

    data_dir = Path(args.data_dir) if args.data_dir else default_user_data_dir()
    if args.headless:
        return run_headless(
            data_dir=data_dir,
            open_browser=not args.no_browser,
            manager_host=args.manager_host or None,
            manager_port=args.manager_port,
        )

    try:
        return run_tk_app(data_dir=data_dir)
    except Exception as exc:
        print(f"Desktop UI unavailable, falling back to headless mode: {exc}", file=sys.stderr)
        return run_headless(data_dir=data_dir, open_browser=not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
