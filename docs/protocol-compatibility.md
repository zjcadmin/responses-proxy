# Responses Protocol Compatibility

中文 | [English](#english)

## 中文

本项目是一个 OpenAI Responses API 兼容代理，底层仍然面向 `chat/completions` 风格的上游模型。因此它会尽量对 Codex 暴露 Responses 风格的协议对象，但无法让不具备原生 Responses 能力的上游模型真正拥有全部官方能力。

### 已支持

- `POST /v1/responses`
- `GET /v1/responses/{response_id}`
- `GET /v1/responses/{response_id}/input_items`
- `POST /v1/responses/{response_id}/cancel`
- `DELETE /v1/responses/{response_id}`
- 文本输入、`developer` role 转换、普通 assistant message
- `function` tools、`namespace` tools 扁平化、Mimo prompt-tool fallback
- `previous_response_id` 和 `prompt_cache_key` 本地会话续接
- `store: true/false` 的本地响应存储行为
- `stream: true` 的基础 SSE 事件
- `response.output_text.*` 和 `response.function_call_arguments.*` 事件
- `reasoning_content` 到 Responses `reasoning` output item 的兼容映射
- 可选 `input_image` 到 Chat Completions `image_url` 内容块的转换；默认文本模型会返回清晰的“不支持图片输入”错误，视觉模型需启用 `upstream_supports_image_input`
- `input_file.file_data` 中 UTF-8 文本文件的本地解码
- SQLite 持久化状态存储，可通过 `RESPONSES_PROXY_STATE_STORE_PATH` 或 `state_store_path` 配置

### 本地模拟能力

- `background: true` 会创建本地后台任务，立即返回 `queued/in_progress` 响应，可通过 `cancel` 取消本地任务。
- `web_search` 会调用本地配置的 SearXNG 或 Tavily，并生成 `web_search_call` 输出项和 `url_citation` annotations。
- `file_search` 会扫描本地配置目录中的文本文件，并生成 `file_search_call` 输出项和 `file_citation` annotations。
- `computer_use` 会接受 `computer_call_output` 上下文，并生成本地 `computer_call` 输出项，但不会真正控制浏览器或桌面。

### 会报告但不执行的字段

以下字段会出现在响应 `metadata.response_proxy.compatibility.ignored_fields` 中，表示代理没有静默假装执行：

- `include`
- `max_tool_calls`
- `prompt`
- `service_tier`
- `top_logprobs`
- `truncation`

如果设置 `RESPONSES_PROXY_STRICT_PROTOCOL=true` 或 `strict_protocol: true`，这些字段会直接返回 `400`，适合做协议兼容性测试。

### 仍非官方等价的部分

- 官方托管的 vector store/file search 排序、过滤、文件引用不是原生实现。
- 官方 computer use 的屏幕操作、安全检查、坐标动作不是原生实现。
- `code_interpreter`、`image_generation`、MCP/remote MCP 等工具类型未原生执行。
- Responses 原生 conversation、token count、response compact 等扩展资源未完整实现。
- 多模态能力最终取决于上游 `chat/completions` 模型是否支持对应内容块。默认不转发图片，避免文本模型返回上游 404/重连错误。

---

## English

This project is an OpenAI Responses API compatibility proxy, but the upstream provider is still a `chat/completions` style model. The proxy exposes Responses-shaped objects for Codex, while some features are locally emulated instead of being true OpenAI-hosted runtime features.

### Supported

- `POST /v1/responses`
- `GET /v1/responses/{response_id}`
- `GET /v1/responses/{response_id}/input_items`
- `POST /v1/responses/{response_id}/cancel`
- `DELETE /v1/responses/{response_id}`
- Text input, `developer` role conversion, assistant messages
- `function` tools, flattened `namespace` tools, Mimo prompt-tool fallback
- Local continuation through `previous_response_id` and `prompt_cache_key`
- Local `store: true/false` behavior
- Basic `stream: true` SSE events
- `response.output_text.*` and `response.function_call_arguments.*` events
- Compatibility mapping from upstream `reasoning_content` to Responses `reasoning` output items
- Optional `input_image` to Chat Completions `image_url` content parts; text-only models return a clear unsupported-image error by default, and vision models require `upstream_supports_image_input`
- Local decoding for UTF-8 text files in `input_file.file_data`
- SQLite-backed persistent state through `RESPONSES_PROXY_STATE_STORE_PATH` or `state_store_path`

### Locally Emulated

- `background: true` creates a local background task and returns a `queued/in_progress` response immediately. The local task can be cancelled through the `cancel` endpoint.
- `web_search` calls the configured local SearXNG or Tavily backend and emits `web_search_call` output items plus `url_citation` annotations.
- `file_search` scans configured local text-file paths and emits `file_search_call` output items plus `file_citation` annotations.
- `computer_use` accepts `computer_call_output` context and emits a local `computer_call` output item, but it does not control a browser or desktop.

### Reported But Not Executed

These fields are reported in `metadata.response_proxy.compatibility.ignored_fields` instead of being silently treated as supported:

- `include`
- `max_tool_calls`
- `prompt`
- `service_tier`
- `top_logprobs`
- `truncation`

Set `RESPONSES_PROXY_STRICT_PROTOCOL=true` or `strict_protocol: true` to reject these fields with `400`, which is useful for protocol compatibility testing.

### Not Fully Official-Equivalent

- Official hosted vector-store file search, ranking, filters, and file citations are not natively implemented.
- Official computer use screen actions, safety checks, and coordinate-based actions are not natively implemented.
- `code_interpreter`, `image_generation`, MCP, and remote MCP tool runtimes are not natively executed.
- Native Responses conversations, token counting, and response compact resources are not fully implemented.
- Multimodal behavior depends on whether the upstream `chat/completions` model supports the translated content parts. Images are not forwarded by default to avoid upstream 404/reconnect loops on text-only models.
