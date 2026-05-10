# Responses Proxy Manager

中文 | [English](#english)

## 中文

Responses Proxy Manager 是一个本地 OpenAI Responses API 兼容代理，用于把 Codex、Agents SDK 或其他只支持 `/v1/responses` 的客户端请求，转换到 DeepSeek、Mimo 等只提供 `chat/completions` 风格接口的上游模型。

项目包含两部分：

- Web 管理台：管理登录口令、多套模型预设、代理端口、Hosted Tools 降级配置，并在网页里启动/停止真实代理进程。
- Responses 代理服务：对外提供 OpenAI 风格 `/v1/responses` 接口，对内转发到上游 `chat/completions` 接口。

### 核心能力

- 支持 `POST /v1/responses`、`GET /v1/responses/{id}`、`POST /v1/responses/{id}/cancel`、`DELETE /v1/responses/{id}`。
- 支持普通 JSON 响应和 `stream: true` SSE 流式响应。
- 兼容 `developer` role、`namespace` tool、`function` tool。
- 支持 `web_search`、`file_search`、`computer_use` 的本地降级桥接。
- 支持文本和多模态输入的 Chat Completions 兼容转换。
- 支持多套模型预设、管理台访问密码、代理访问 API Key。
- 支持 Windows、macOS、Linux、NAS、Docker 和桌面应用部署。

### 快速开始

Windows：

```powershell
.\start-manager.ps1
```

Linux / macOS：

```bash
chmod +x start-manager.sh
./start-manager.sh
```

Docker：

```bash
docker compose up -d --build
```

打开管理台：

```text
http://127.0.0.1:8899
```

首次默认管理密码：

```text
admin123
```

登录后在页面里创建或编辑模型预设，然后点击“启动代理”。代理默认地址：

```text
http://127.0.0.1:8800/v1
```

把这个地址填到 Codex 或其他 OpenAI-compatible 客户端的 `base_url`。

管理台默认端口是 `8899`。你可以在 Web 管理台的“环境配置”页修改管理台监听地址和端口；保存后需要重启管理台才会按新端口启动。桌面应用启动界面也可以在点击“启动管理台”前直接填写管理台端口。

登录密码也可以在 Web 管理台“环境配置”页修改。需要输入当前密码、新密码和确认密码；新密码至少 8 位。

### 部署方式

- 脚本部署：Windows 使用 `*.ps1` 或 `*.bat`，Linux/macOS/NAS 使用 `*.sh`。
- Docker 部署：使用 `Dockerfile` 或 `docker-compose.yml`。
- 桌面应用：使用 `build-desktop.*` 打包 Windows `.exe`、macOS `.app`、Linux 可执行程序。

完整部署文档：

- [多平台部署文档 / Deployment Guide](docs/deploy.md)
- [桌面应用打包文档 / Desktop App Packaging](docs/desktop-app.md)

### 配置文件

本地私有配置文件不会提交到 Git：

- `.env`
- `manager-config.json`
- `model-config.json`
- `model-presets.json`
- `runtime/`
- `data/`

可提交的示例配置：

- `.env.example`
- `manager-config.example.json`
- `model-config.example.json`
- `model-presets.example.json`

### 测试

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
```

### 当前限制

- Hosted Tools 是本地降级桥接，不是 OpenAI 官方托管工具运行时。
- 多模态能力取决于上游模型是否支持对应 Chat Completions 内容格式。
- API Key 以本地配置文件形式保存，请不要提交真实配置。
- macOS `.app` 正式分发需要 Apple Developer 签名和 notarization。

---

## English

Responses Proxy Manager is a local OpenAI Responses API compatible proxy. It lets Codex, Agents SDK, and other `/v1/responses` clients work with upstream providers that only expose `chat/completions` style APIs, such as DeepSeek or Mimo.

The project has two runtime components:

- Web Manager: manages the login password, model presets, proxy ports, hosted-tool fallback settings, and starts/stops the real proxy process from the browser.
- Responses Proxy: exposes OpenAI-style `/v1/responses` endpoints locally and forwards requests to upstream `chat/completions` providers.

### Features

- Supports `POST /v1/responses`, `GET /v1/responses/{id}`, `POST /v1/responses/{id}/cancel`, and `DELETE /v1/responses/{id}`.
- Supports JSON responses and `stream: true` SSE streaming.
- Handles `developer` role, `namespace` tools, and `function` tools.
- Provides local fallback bridges for `web_search`, `file_search`, and `computer_use`.
- Converts text and multimodal inputs into Chat Completions compatible payloads.
- Supports multiple model presets, manager password protection, and proxy API key protection.
- Supports Windows, macOS, Linux, NAS, Docker, and desktop-app deployment.

### Quick Start

Windows:

```powershell
.\start-manager.ps1
```

Linux / macOS:

```bash
chmod +x start-manager.sh
./start-manager.sh
```

Docker:

```bash
docker compose up -d --build
```

Open the manager:

```text
http://127.0.0.1:8899
```

Default first-run password:

```text
admin123
```

Create or edit a model preset in the web UI, then click "Start Proxy". The default proxy base URL is:

```text
http://127.0.0.1:8800/v1
```

Use this URL as the `base_url` in Codex or any OpenAI-compatible client.

The default manager port is `8899`. You can change the manager bind host and port in the Web Manager "Environment" page. Save the change and restart the manager for the new port to take effect. The desktop app also lets you edit the manager port before clicking "Start Manager".

The manager login password can also be changed in the Web Manager "Environment" page. Enter the current password, new password, and confirmation. The new password must be at least 8 characters.

### Deployment Options

- Script deployment: use `*.ps1` or `*.bat` on Windows, and `*.sh` on Linux/macOS/NAS.
- Docker deployment: use `Dockerfile` or `docker-compose.yml`.
- Desktop app: use `build-desktop.*` to package Windows `.exe`, macOS `.app`, or Linux executable builds.

Documentation:

- [Deployment Guide](docs/deploy.md)
- [Desktop App Packaging](docs/desktop-app.md)
- [GitHub Publishing Checklist](docs/github.md)

### Configuration Files

Private local files are ignored by Git:

- `.env`
- `manager-config.json`
- `model-config.json`
- `model-presets.json`
- `runtime/`
- `data/`

Commit example files instead:

- `.env.example`
- `manager-config.example.json`
- `model-config.example.json`
- `model-presets.example.json`

### Tests

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

### Limitations

- Hosted tools are local fallback bridges, not the official OpenAI hosted-tool runtime.
- Multimodal support depends on whether the upstream model supports the translated Chat Completions content format.
- API keys are stored in local config files. Never commit real secrets.
- Public macOS `.app` distribution requires Apple Developer signing and notarization.
