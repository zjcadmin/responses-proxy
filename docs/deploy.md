# 多平台部署文档 / Multi-Platform Deployment Guide

中文 | [English](#english)

## 中文

本文档说明如何在 Windows、macOS、Linux、NAS 和 Docker 环境部署 Responses Proxy Manager。

### 默认端口

- 管理台：`8899`
- Responses 代理：`8800`

本机使用时可以保持 `127.0.0.1`。如果需要局域网、NAS 或 Docker 外部访问，请把管理台或代理监听地址设置为 `0.0.0.0`，客户端访问时使用实际机器 IP。

管理台端口可以在 Web 管理台“环境配置”页修改，也可以在桌面应用启动界面修改。保存后需要重启管理台，新端口才会生效。

管理台登录密码也可以在“环境配置”页修改。新密码至少 8 位，请修改后妥善保存。

### Windows

启动管理台：

```powershell
.\start-manager.ps1
```

停止管理台：

```powershell
.\stop-manager.ps1
```

也可以双击：

```text
start-manager.bat
stop-manager.bat
```

真实代理服务推荐在 Web 管理台里点击“启动代理 / 停止代理”。如需脚本方式：

```powershell
.\start-proxy.ps1
.\stop-proxy.ps1
```

### macOS / Linux

首次安装：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
chmod +x *.sh
```

启动管理台：

```bash
./start-manager.sh
```

停止管理台：

```bash
./stop-manager.sh
```

如需脚本方式启动真实代理：

```bash
./start-proxy.sh
./stop-proxy.sh
```

### NAS 裸机部署

适用于群晖、威联通、绿联等可以运行 Python 3.12 的 NAS。

```bash
cd /volume1/docker/responses-proxy
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
RESPONSES_PROXY_MANAGER_HOST=0.0.0.0 ./start-manager.sh
```

浏览器访问：

```text
http://NAS_IP:8899
```

在管理台编辑模型预设时，代理监听地址建议填写：

```text
0.0.0.0
```

Codex 或其他客户端填写：

```text
http://NAS_IP:8800/v1
```

### Docker Compose

推荐生产或 NAS 环境使用 Docker Compose：

```bash
docker compose up -d --build
```

打开：

```text
http://localhost:8899
```

NAS 或服务器访问时：

```text
http://SERVER_IP:8899
```

停止：

```bash
docker compose down
```

### Docker CLI

```bash
docker build -t responses-proxy .
docker run -d \
  --name responses-proxy \
  --restart unless-stopped \
  -p 8899:8899 \
  -p 8800:8800 \
  -e RESPONSES_PROXY_DATA_DIR=/data \
  -e RESPONSES_PROXY_MANAGER_HOST=0.0.0.0 \
  -v "$PWD/data:/data" \
  responses-proxy
```

### 数据目录

默认源码部署会把私有配置保存在项目目录：

- `manager-config.json`
- `model-presets.json`
- `model-config.json`
- `.env`
- `runtime/`

Docker 默认使用：

```text
./data
```

也可以通过环境变量指定：

```bash
RESPONSES_PROXY_DATA_DIR=/path/to/data ./start-manager.sh
```

### 常见问题

- 管理台能打开但代理不能访问：检查模型预设里的代理监听地址是否为 `0.0.0.0`，以及防火墙是否放行 `8800`。
- 端口被占用：先运行对应 `stop-*` 脚本，或在管理台修改端口。
- Docker 内部可用但外部不可用：确认 `ports` 映射包含 `8899:8899` 和 `8800:8800`。
- macOS 首次运行 `.sh` 失败：执行 `chmod +x *.sh`。
- 真实 API Key 不要写入示例文件，不要提交到 GitHub。

---

## English

This guide explains how to deploy Responses Proxy Manager on Windows, macOS, Linux, NAS, and Docker.

### Default Ports

- Web Manager: `8899`
- Responses Proxy: `8800`

For local-only usage, keep `127.0.0.1`. For LAN, NAS, server, or Docker access, bind the manager or proxy to `0.0.0.0` and use the machine IP from clients.

The manager port can be changed in the Web Manager "Environment" page or in the desktop app before startup. Restart the manager after saving the new port.

The manager login password can also be changed in the "Environment" page. The new password must be at least 8 characters.

### Windows

Start the manager:

```powershell
.\start-manager.ps1
```

Stop the manager:

```powershell
.\stop-manager.ps1
```

You can also double-click:

```text
start-manager.bat
stop-manager.bat
```

The recommended way to control the real proxy service is through the Web Manager buttons. Script entrypoints are also available:

```powershell
.\start-proxy.ps1
.\stop-proxy.ps1
```

### macOS / Linux

First-time setup:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
chmod +x *.sh
```

Start the manager:

```bash
./start-manager.sh
```

Stop the manager:

```bash
./stop-manager.sh
```

Optional proxy scripts:

```bash
./start-proxy.sh
./stop-proxy.sh
```

### NAS Bare-Metal Deployment

Use this mode for Synology, QNAP, UGREEN, or other NAS devices that can run Python 3.12.

```bash
cd /volume1/docker/responses-proxy
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
RESPONSES_PROXY_MANAGER_HOST=0.0.0.0 ./start-manager.sh
```

Open:

```text
http://NAS_IP:8899
```

When editing a model preset, set the proxy bind host to:

```text
0.0.0.0
```

Use this base URL in Codex or other clients:

```text
http://NAS_IP:8800/v1
```

### Docker Compose

Docker Compose is recommended for production or NAS environments:

```bash
docker compose up -d --build
```

Open locally:

```text
http://localhost:8899
```

Open from another machine:

```text
http://SERVER_IP:8899
```

Stop:

```bash
docker compose down
```

### Docker CLI

```bash
docker build -t responses-proxy .
docker run -d \
  --name responses-proxy \
  --restart unless-stopped \
  -p 8899:8899 \
  -p 8800:8800 \
  -e RESPONSES_PROXY_DATA_DIR=/data \
  -e RESPONSES_PROXY_MANAGER_HOST=0.0.0.0 \
  -v "$PWD/data:/data" \
  responses-proxy
```

### Data Directory

Source deployments store private configuration in the project directory by default:

- `manager-config.json`
- `model-presets.json`
- `model-config.json`
- `.env`
- `runtime/`

Docker uses:

```text
./data
```

You can override the data directory:

```bash
RESPONSES_PROXY_DATA_DIR=/path/to/data ./start-manager.sh
```

### Troubleshooting

- Manager opens but proxy is unreachable: check that the preset proxy host is `0.0.0.0` and that the firewall allows `8800`.
- Port is already in use: run the corresponding `stop-*` script or change ports in the Web Manager.
- Docker works internally but not externally: confirm port mappings include `8899:8899` and `8800:8800`.
- `.sh` fails on macOS: run `chmod +x *.sh`.
- Never put real API keys in example files or commit them to GitHub.
