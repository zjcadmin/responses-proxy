# Responses Proxy Manager

`responses-proxy` 现在包含两部分：

- `管理台`
  一个常驻本地 Web 控制台，用来登录、保存多套模型预设、测试连通性、启动和停止真正的代理进程。
- `代理服务`
  一个兼容 OpenAI `Responses API` 的本地代理，继续把请求转给只支持 `chat/completions` 的上游，例如 DeepSeek、Mimo 一类服务。

## 现在怎么用

### 1. 启动管理台

双击或运行：

```powershell
Set-Location '*你的电脑存放路径*\responses-proxy'
.\start-manager.bat
```

或者：

```powershell
Set-Location '*你的电脑存放路径*\responses-proxy'
.\start-manager.ps1
```

默认管理台地址：

`http://127.0.0.1:8899`

如果是第一次启动：

- 会自动生成 `manager-config.json`
- 会自动生成 `model-presets.json`
- 如果发现旧的 `.env` 和 `model-config.json`，会自动导入成第一套预设
- 默认登录密码是 `admin123`

### 2. 在网页里管理预设

登录后你可以在页面里：

- 新建 DeepSeek / Mimo / 自定义模型预设
- 配置 `Base URL`
- 配置 `Chat Path`
- 配置 `API Key`
- 配置 `模型名`
- 配置 `代理监听地址和端口`
- 配置 `认证头名称 / 前缀`
- 配置额外自定义 Headers
- 测试该预设是否真的能连上上游

### 3. 在网页里启动代理

激活预设后，点击页面里的：

- `启动代理`
- `停止代理`
- `重启代理`

这些按钮控制的是实际代理进程，不是假状态切换。

### 4. 给 Codex 配置

代理启动后，页面会显示当前可用的 `base_url`，例如：

`http://127.0.0.1:8800/v1`

把它填到 Codex 的 `base_url`。  
Codex 使用的 API Key 来自本地 `manager-config.json` 里的 `proxy_api_key`。

## 本地文件说明

### 本地私有文件

- `manager-config.json`
  管理台配置，包含登录密码哈希、管理台端口、代理访问 key 等
- `model-presets.json`
  你自己的模型预设仓库
- `runtime/`
  运行时日志、pid、启动快照
- `.env`
  旧版兼容导入来源，不再是主要配置入口


### 示例文件

- `manager-config.example.json`
- `model-presets.example.json`
- `model-config.example.json`
- `.env.example`

## 仍然保留的旧脚本

这些脚本还在，主要用于直接操作原始代理：

- `start-manager.bat`
- `start-manager.ps1`
- `stop-manager.bat`
- `stop-manager.ps1`
- `start-proxy.bat`
- `start-proxy.ps1`
- `stop-proxy.bat`
- `stop-proxy.ps1`

但新的推荐流程是：

`start-manager.* -> 浏览器里配置预设 -> 浏览器里启停代理`

## 代理当前支持

- `POST /v1/responses`
- 普通 JSON 响应
- `stream: true` SSE 流式输出
- 文本对话输入
- `function` 工具
- `previous_response_id` / `prompt_cache_key` 的短期会话续接
- `developer` 角色兼容
- `namespace` 工具展开
- hosted tools 忽略降级

## 当前限制

- 还没有做多用户
- 还没有做网页内改管理密码
- API Key 仍然是本地明文配置文件存储
- 没有实现真实 hosted tools 执行平台
- `GET /v1/responses/{id}`、cancel、delete 仍返回 `501`

## 测试

```powershell
Set-Location '*你的电脑存放路径*\responses-proxy'
& '*你的电脑存放路径*\monitoring-platform\backend\.venv\Scripts\python.exe' -m pytest -q
```

## 手工验证过的链路

我已经实际验证过这几步：

- 管理台可在 `8899` 端口启动
- 登录页可正常渲染
- 旧 `.env + model-config.json` 可自动导入成预设
- 网页 API 可以启动代理
- 启动后 `http://127.0.0.1:8800/healthz` 返回 `ok`
- 网页 API 可以停止代理并再次拉起
