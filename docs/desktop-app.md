# 桌面应用打包 / Desktop App Packaging

中文 | [English](#english)

## 中文

桌面应用是对现有 Web 管理台的本地启动器封装。它不会替换 `bat`、`ps1`、`sh` 或 Docker 部署方式。

### 工作方式

- 双击应用后启动本地 Web 管理台。
- 自动打开默认浏览器访问管理台。
- 启动前可以在应用窗口里修改管理台监听地址和端口。
- 应用窗口提供“启动服务 / 停止服务 / 打开控制台 / 退出程序”按钮。
- Web 管理台里的“启动代理 / 停止代理 / 重启代理”继续可用。
- 打包后的真实代理进程由同一个应用以 `--run-proxy` 模式启动，因此终端用户不需要单独安装 Python。
- 关闭应用窗口或点击“退出程序”时，会自动停止管理台和由它拉起的代理进程。

### 运行数据目录

桌面应用把真实配置写入用户数据目录，而不是程序目录。

- Windows：`%APPDATA%\Responses Proxy`
- macOS：`~/Library/Application Support/Responses Proxy`
- Linux：`$XDG_DATA_HOME/responses-proxy`，未设置时使用 `~/.local/share/responses-proxy`

这些目录会保存：

- `manager-config.json`
- `model-presets.json`
- `model-config.json`
- `.env`
- `runtime/`

### Windows 应用

构建：

```powershell
.\build-desktop.ps1
```

或：

```text
build-desktop.bat
```

输出：

```text
dist\ResponsesProxy.exe
```

### macOS 应用

构建：

```bash
chmod +x build-desktop.sh
./build-desktop.sh
```

输出：

```text
dist/Responses Proxy.app
```

当前项目没有内置 Apple Developer 签名和 notarization。公开分发前建议接入正式签名流程。

### Linux 应用

构建：

```bash
chmod +x build-desktop.sh
./build-desktop.sh
```

输出：

```text
dist/ResponsesProxy
dist/responses-proxy.desktop
```

直接运行：

```bash
./dist/ResponsesProxy
```

加入桌面菜单时，把 `responses-proxy.desktop` 复制到 `~/.local/share/applications/`，并按实际安装位置修改 `Exec`。

### 无界面模式

无桌面环境的 Linux 或 NAS 推荐继续使用 `.sh` 或 Docker。桌面启动器也支持无界面模式：

```bash
./ResponsesProxy --headless --no-browser
```

### 构建依赖

- Python 3.12
- pip
- 当前平台的桌面运行环境

构建脚本会安装：

```text
responses-proxy[desktop]
```

其中包含 `PyInstaller`。

### 平台限制

PyInstaller 通常不能可靠交叉打包。请在目标平台上构建目标应用：

- Windows `.exe` 在 Windows 构建。
- macOS `.app` 在 macOS 构建。
- Linux 可执行文件在 Linux 构建。

---

## English

The desktop app is a local launcher wrapper around the existing Web Manager. It does not replace the `bat`, `ps1`, `sh`, or Docker deployment options.

### How It Works

- Double-clicking the app starts the local Web Manager.
- The app opens the Web Manager in the default browser.
- You can edit the manager bind host and port in the app window before startup.
- The app window provides "Start Service / Stop Service / Open Console / Exit" controls.
- The Web Manager "Start Proxy / Stop Proxy / Restart Proxy" buttons still control the real proxy process.
- The packaged proxy process is launched by the same executable in `--run-proxy` mode, so end users do not need to install Python separately.
- Closing the app window or clicking "Exit" stops both the manager and the proxy process launched by it.

### Runtime Data Directory

The desktop app writes real configuration to the user data directory, not the application directory.

- Windows: `%APPDATA%\Responses Proxy`
- macOS: `~/Library/Application Support/Responses Proxy`
- Linux: `$XDG_DATA_HOME/responses-proxy`, or `~/.local/share/responses-proxy` when `XDG_DATA_HOME` is not set

These directories contain:

- `manager-config.json`
- `model-presets.json`
- `model-config.json`
- `.env`
- `runtime/`

### Windows App

Build:

```powershell
.\build-desktop.ps1
```

Or:

```text
build-desktop.bat
```

Output:

```text
dist\ResponsesProxy.exe
```

### macOS App

Build:

```bash
chmod +x build-desktop.sh
./build-desktop.sh
```

Output:

```text
dist/Responses Proxy.app
```

This project does not include Apple Developer signing or notarization. Add a proper signing pipeline before public macOS distribution.

### Linux App

Build:

```bash
chmod +x build-desktop.sh
./build-desktop.sh
```

Output:

```text
dist/ResponsesProxy
dist/responses-proxy.desktop
```

Run directly:

```bash
./dist/ResponsesProxy
```

To add a desktop menu entry, copy `responses-proxy.desktop` to `~/.local/share/applications/` and update `Exec` to the actual install path.

### Headless Mode

For Linux or NAS systems without a desktop environment, prefer `.sh` scripts or Docker. The desktop launcher also supports headless mode:

```bash
./ResponsesProxy --headless --no-browser
```

### Build Requirements

- Python 3.12
- pip
- Desktop runtime for the target platform

The build script installs:

```text
responses-proxy[desktop]
```

This includes `PyInstaller`.

### Platform Limitation

PyInstaller generally cannot cross-package reliably. Build each target on that target platform:

- Build Windows `.exe` on Windows.
- Build macOS `.app` on macOS.
- Build Linux executable on Linux.
