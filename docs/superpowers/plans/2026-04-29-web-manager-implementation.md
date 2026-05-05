# Web Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a password-protected local web manager that stores multiple upstream model presets and starts, stops, and restarts the actual proxy process from a visual dashboard.

**Architecture:** Keep the existing proxy FastAPI app as a separate child process, then add a second FastAPI management app that owns config storage, authentication, process control, runtime config generation, and the UI. The manager writes shareable templates plus local private config, and launches the proxy with a generated runtime launch file instead of manual `.env` editing.

**Tech Stack:** Python 3.12, FastAPI, httpx, pydantic, uvicorn, vanilla HTML/CSS/JavaScript, pytest

---

## File Structure

### Create

- `E:\个人文件\AI\codeX\responses-proxy\app\auth.py`
- `E:\个人文件\AI\codeX\responses-proxy\app\manager_config.py`
- `E:\个人文件\AI\codeX\responses-proxy\app\manager_store.py`
- `E:\个人文件\AI\codeX\responses-proxy\app\process_manager.py`
- `E:\个人文件\AI\codeX\responses-proxy\app\manager_main.py`
- `E:\个人文件\AI\codeX\responses-proxy\app\templates\index.html`
- `E:\个人文件\AI\codeX\responses-proxy\app\static\styles.css`
- `E:\个人文件\AI\codeX\responses-proxy\app\static\app.js`
- `E:\个人文件\AI\codeX\responses-proxy\scripts\run_manager.py`
- `E:\个人文件\AI\codeX\responses-proxy\manager-config.example.json`
- `E:\个人文件\AI\codeX\responses-proxy\model-presets.example.json`
- `E:\个人文件\AI\codeX\responses-proxy\tests\test_manager_auth.py`
- `E:\个人文件\AI\codeX\responses-proxy\tests\test_manager_store.py`
- `E:\个人文件\AI\codeX\responses-proxy\tests\test_process_manager.py`
- `E:\个人文件\AI\codeX\responses-proxy\tests\test_manager_api.py`

### Modify

- `E:\个人文件\AI\codeX\responses-proxy\app\config.py`
- `E:\个人文件\AI\codeX\responses-proxy\app\main.py`
- `E:\个人文件\AI\codeX\responses-proxy\app\upstream.py`
- `E:\个人文件\AI\codeX\responses-proxy\scripts\run_proxy.py`
- `E:\个人文件\AI\codeX\responses-proxy\start-proxy.bat`
- `E:\个人文件\AI\codeX\responses-proxy\start-proxy.ps1`
- `E:\个人文件\AI\codeX\responses-proxy\stop-proxy.bat`
- `E:\个人文件\AI\codeX\responses-proxy\stop-proxy.ps1`
- `E:\个人文件\AI\codeX\responses-proxy\README.md`
- `E:\个人文件\AI\codeX\responses-proxy\.gitignore`

## Task 1: Add manager data models and storage

**Files:**
- Create: `E:\个人文件\AI\codeX\responses-proxy\app\manager_config.py`
- Create: `E:\个人文件\AI\codeX\responses-proxy\app\manager_store.py`
- Create: `E:\个人文件\AI\codeX\responses-proxy\tests\test_manager_store.py`
- Modify: `E:\个人文件\AI\codeX\responses-proxy\.gitignore`
- Create: `E:\个人文件\AI\codeX\responses-proxy\manager-config.example.json`
- Create: `E:\个人文件\AI\codeX\responses-proxy\model-presets.example.json`

- [ ] **Step 1: Write the failing storage tests**

```python
def test_store_bootstraps_example_files(tmp_path: Path) -> None:
    store = ManagerStore(
        manager_config_path=tmp_path / "manager-config.json",
        presets_path=tmp_path / "model-presets.json",
        runtime_dir=tmp_path / "runtime",
    )

    state = store.load_state()

    assert state.manager.manager_port == 8899
    assert state.presets.active_preset_id is None
    assert state.presets.presets == []


def test_upsert_preset_marks_only_one_active(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = store.save_preset(PresetInput(name="DeepSeek", provider="DeepSeek", base_url="https://api.deepseek.com/v1", chat_path="/chat/completions", api_key="key-1", model="deepseek-chat", proxy_host="127.0.0.1", proxy_port=8800, request_timeout_seconds=120, headers={}))
    second = store.save_preset(PresetInput(name="Mimo", provider="Xiaomi", base_url="https://api.mimo.example/v1", chat_path="/chat/completions", api_key="key-2", model="mimo", proxy_host="127.0.0.1", proxy_port=8801, request_timeout_seconds=120, headers={}))

    state = store.set_active_preset(second.id)

    assert state.presets.active_preset_id == second.id
    assert {preset.id for preset in state.presets.presets if preset.is_active} == {second.id}
```

- [ ] **Step 2: Run the store tests to verify they fail**

Run: `& 'E:\个人文件\AI\codeX\monitoring-platform\backend\.venv\Scripts\python.exe' -m pytest -q tests/test_manager_store.py`

Expected: `ImportError` or `ModuleNotFoundError` for `ManagerStore` or related types.

- [ ] **Step 3: Implement manager config and preset models**

```python
class ManagerConfig(BaseModel):
    manager_host: str = "127.0.0.1"
    manager_port: int = 8899
    password_hash: str = ""
    password_salt: str = ""
    session_secret: str = ""
    local_only: bool = True
    log_tail_lines: int = 200
    runtime_dir: str = "runtime"


class ModelPreset(BaseModel):
    id: str
    name: str
    provider: str
    base_url: str
    chat_path: str = "/chat/completions"
    api_key: str
    model: str
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 8800
    request_timeout_seconds: float = 120.0
    headers: dict[str, str] = Field(default_factory=dict)
    description: str = ""
    is_active: bool = False
```

- [ ] **Step 4: Implement file-backed manager store and templates**

```python
class ManagerStore:
    def load_state(self) -> ManagerState:
        manager = self._read_or_initialize_manager_config()
        presets = self._read_or_initialize_presets()
        self._ensure_runtime_dir(manager)
        return ManagerState(manager=manager, presets=presets)

    def set_active_preset(self, preset_id: str) -> ManagerState:
        bundle = self._read_presets()
        found = False
        updated = []
        for preset in bundle.presets:
            is_active = preset.id == preset_id
            found = found or is_active
            updated.append(preset.model_copy(update={"is_active": is_active}))
        if not found:
            raise KeyError(preset_id)
        new_bundle = bundle.model_copy(update={"active_preset_id": preset_id, "presets": updated})
        self._write_json(self._presets_path, new_bundle.model_dump(mode="json"))
        return ManagerState(manager=self._read_manager_config(), presets=new_bundle)
```

- [ ] **Step 5: Run the store tests to verify they pass**

Run: `& 'E:\个人文件\AI\codeX\monitoring-platform\backend\.venv\Scripts\python.exe' -m pytest -q tests/test_manager_store.py`

Expected: `2 passed`

## Task 2: Add authentication helpers and manager session protection

**Files:**
- Create: `E:\个人文件\AI\codeX\responses-proxy\app\auth.py`
- Create: `E:\个人文件\AI\codeX\responses-proxy\tests\test_manager_auth.py`

- [ ] **Step 1: Write the failing auth tests**

```python
def test_verify_password_accepts_correct_password() -> None:
    salt = generate_salt()
    password_hash = hash_password("secret-pass", salt)

    assert verify_password("secret-pass", salt, password_hash) is True
    assert verify_password("wrong-pass", salt, password_hash) is False


def test_session_store_round_trip() -> None:
    sessions = SessionStore()

    token = sessions.create_session()

    assert sessions.is_valid(token) is True
    sessions.destroy_session(token)
    assert sessions.is_valid(token) is False
```

- [ ] **Step 2: Run the auth tests to verify they fail**

Run: `& 'E:\个人文件\AI\codeX\monitoring-platform\backend\.venv\Scripts\python.exe' -m pytest -q tests/test_manager_auth.py`

Expected: `ImportError` for `hash_password`, `verify_password`, or `SessionStore`.

- [ ] **Step 3: Implement password hashing and in-memory session storage**

```python
def hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return base64.b64encode(digest).decode("ascii")


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


class SessionStore:
    def __init__(self) -> None:
        self._tokens: set[str] = set()

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens.add(token)
        return token
```

- [ ] **Step 4: Run the auth tests to verify they pass**

Run: `& 'E:\个人文件\AI\codeX\monitoring-platform\backend\.venv\Scripts\python.exe' -m pytest -q tests/test_manager_auth.py`

Expected: `2 passed`

## Task 3: Expand proxy runtime configuration for per-preset settings

**Files:**
- Modify: `E:\个人文件\AI\codeX\responses-proxy\app\config.py`
- Modify: `E:\个人文件\AI\codeX\responses-proxy\app\upstream.py`
- Modify: `E:\个人文件\AI\codeX\responses-proxy\scripts\run_proxy.py`
- Modify: `E:\个人文件\AI\codeX\responses-proxy\tests\test_responses_api.py`

- [ ] **Step 1: Write the failing proxy config tests**

```python
def test_upstream_client_merges_custom_headers() -> None:
    settings = load_settings(
        {
            "upstream_base_url": "https://example.com/v1",
            "upstream_api_key": "secret",
            "upstream_headers": {"X-Provider": "mimo"},
        }
    )

    headers = UpstreamChatClient(settings)._headers("secret")

    assert headers["Authorization"] == "Bearer secret"
    assert headers["X-Provider"] == "mimo"
```

- [ ] **Step 2: Run the targeted proxy tests to verify they fail**

Run: `& 'E:\个人文件\AI\codeX\monitoring-platform\backend\.venv\Scripts\python.exe' -m pytest -q tests/test_responses_api.py -k custom_headers`

Expected: missing settings field or assertion failure because custom headers are not supported yet.

- [ ] **Step 3: Extend settings and launch config to carry runtime headers and proxy key**

```python
class Settings(BaseSettings):
    upstream_headers: dict[str, str] = {}
    proxy_api_key: str | None = None


class LaunchConfig(BaseModel):
    upstream_api_key: str | None = None
    proxy_api_key: str | None = None
    upstream_headers: dict[str, str] = Field(default_factory=dict)
```

- [ ] **Step 4: Update the upstream client and launcher to consume runtime config files**

```python
def _headers(self, bearer_token: str) -> dict[str, str]:
    headers = dict(self._settings.upstream_headers)
    if bearer_token:
        headers.setdefault("Authorization", f"Bearer {bearer_token}")
    return headers


def apply_launch_config(config: LaunchConfig) -> None:
    for key, value in config.to_env().items():
        os.environ[key] = value
```

- [ ] **Step 5: Run the proxy regression suite**

Run: `& 'E:\个人文件\AI\codeX\monitoring-platform\backend\.venv\Scripts\python.exe' -m pytest -q tests/test_responses_api.py`

Expected: all proxy tests pass, including the new custom header coverage.

## Task 4: Add process manager and runtime log handling

**Files:**
- Create: `E:\个人文件\AI\codeX\responses-proxy\app\process_manager.py`
- Create: `E:\个人文件\AI\codeX\responses-proxy\tests\test_process_manager.py`

- [ ] **Step 1: Write the failing process manager tests**

```python
def test_write_launch_config_snapshot(tmp_path: Path) -> None:
    manager = ProcessManager(project_root=tmp_path, python_executable=sys.executable)
    preset = build_preset()

    launch_path = manager.write_launch_config(preset, proxy_api_key="proxy-key")

    payload = json.loads(launch_path.read_text(encoding="utf-8"))
    assert payload["upstream_model"] == preset.model
    assert payload["proxy_port"] == preset.proxy_port
    assert payload["upstream_headers"] == preset.headers


def test_stop_by_pid_file_returns_stopped_state(tmp_path: Path) -> None:
    manager = ProcessManager(project_root=tmp_path, python_executable=sys.executable)
    process = subprocess.Popen([sys.executable, "-m", "http.server", "0", "--bind", "127.0.0.1"])
    try:
        manager.write_pid(process.pid)
        result = manager.stop_proxy()
        assert result.state == "stopped"
    finally:
        if process.poll() is None:
            process.kill()
```

- [ ] **Step 2: Run the process manager tests to verify they fail**

Run: `& 'E:\个人文件\AI\codeX\monitoring-platform\backend\.venv\Scripts\python.exe' -m pytest -q tests/test_process_manager.py`

Expected: `ImportError` for `ProcessManager`.

- [ ] **Step 3: Implement process start, stop, status, and log tailing**

```python
class ProcessManager:
    def start_proxy(self, launch_config_path: Path) -> ProcessStatus:
        stdout = self.stdout_log_path.open("a", encoding="utf-8")
        stderr = self.stderr_log_path.open("a", encoding="utf-8")
        process = subprocess.Popen(
            [str(self.python_executable), str(self.run_proxy_path), "--config", str(launch_config_path)],
            cwd=self.project_root,
            stdout=stdout,
            stderr=stderr,
        )
        self.write_pid(process.pid)
        return self.status()
```

- [ ] **Step 4: Run the process manager tests to verify they pass**

Run: `& 'E:\个人文件\AI\codeX\monitoring-platform\backend\.venv\Scripts\python.exe' -m pytest -q tests/test_process_manager.py`

Expected: process manager tests pass.

## Task 5: Build the authenticated manager API

**Files:**
- Create: `E:\个人文件\AI\codeX\responses-proxy\app\manager_main.py`
- Create: `E:\个人文件\AI\codeX\responses-proxy\tests\test_manager_api.py`
- Modify: `E:\个人文件\AI\codeX\responses-proxy\app\main.py`

- [ ] **Step 1: Write the failing manager API tests**

```python
def test_login_sets_session_cookie(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": "secret-pass"})

    assert response.status_code == 200
    assert "manager_session=" in response.headers["set-cookie"]


def test_start_proxy_uses_active_preset(client: TestClient) -> None:
    client.post("/api/auth/login", json={"password": "secret-pass"})
    response = client.post("/api/proxy/start")

    assert response.status_code == 200
    assert response.json()["proxy"]["running"] is True
    assert response.json()["active_preset"]["name"] == "DeepSeek"
```

- [ ] **Step 2: Run the manager API tests to verify they fail**

Run: `& 'E:\个人文件\AI\codeX\monitoring-platform\backend\.venv\Scripts\python.exe' -m pytest -q tests/test_manager_api.py`

Expected: app factory or routes are missing.

- [ ] **Step 3: Implement manager app factory and authenticated routes**

```python
@app.post("/api/auth/login")
async def login(payload: LoginPayload, response: Response) -> dict[str, object]:
    if not verify_password(payload.password, manager_config.password_salt, manager_config.password_hash):
        raise HTTPException(status_code=401, detail="Invalid password.")
    token = session_store.create_session()
    response.set_cookie("manager_session", token, httponly=True, samesite="lax")
    return {"ok": True}


@app.post("/api/proxy/start")
async def start_proxy(_: None = Depends(require_session)) -> dict[str, object]:
    active = store.get_active_preset()
    launch_path = process_manager.write_launch_config(active, proxy_api_key=manager_config.proxy_api_key)
    proxy = process_manager.start_proxy(launch_path)
    return build_dashboard_payload(store.load_state(), proxy)
```

- [ ] **Step 4: Run the manager API tests to verify they pass**

Run: `& 'E:\个人文件\AI\codeX\monitoring-platform\backend\.venv\Scripts\python.exe' -m pytest -q tests/test_manager_api.py`

Expected: manager auth, presets, status, and proxy lifecycle tests pass.

## Task 6: Build the web UI and manager launcher

**Files:**
- Create: `E:\个人文件\AI\codeX\responses-proxy\app\templates\index.html`
- Create: `E:\个人文件\AI\codeX\responses-proxy\app\static\styles.css`
- Create: `E:\个人文件\AI\codeX\responses-proxy\app\static\app.js`
- Create: `E:\个人文件\AI\codeX\responses-proxy\scripts\run_manager.py`
- Modify: `E:\个人文件\AI\codeX\responses-proxy\README.md`
- Modify: `E:\个人文件\AI\codeX\responses-proxy\start-proxy.bat`
- Modify: `E:\个人文件\AI\codeX\responses-proxy\start-proxy.ps1`
- Modify: `E:\个人文件\AI\codeX\responses-proxy\stop-proxy.bat`
- Modify: `E:\个人文件\AI\codeX\responses-proxy\stop-proxy.ps1`

- [ ] **Step 1: Add a lightweight UI smoke test**

```python
def test_manager_index_serves_html(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Responses Proxy Manager" in response.text
    assert "manager-root" in response.text
```

- [ ] **Step 2: Run the UI smoke test to verify it fails**

Run: `& 'E:\个人文件\AI\codeX\monitoring-platform\backend\.venv\Scripts\python.exe' -m pytest -q tests/test_manager_api.py -k manager_index`

Expected: HTML shell or static assets are missing.

- [ ] **Step 3: Implement the visual dashboard and launcher**

```html
<main class="shell">
  <section class="hero">
    <div class="status-chip" data-running-state="stopped">Proxy Stopped</div>
    <h1>Responses Proxy Manager</h1>
    <p>Manage presets, test providers, and control the live Codex proxy from one place.</p>
  </section>
  <section id="manager-root"></section>
</main>
```

```python
def main() -> int:
    manager = load_manager_config()
    uvicorn.run("app.manager_main:app", host=manager.manager_host, port=manager.manager_port, reload=False)
    return 0
```

- [ ] **Step 4: Run the full test suite**

Run: `& 'E:\个人文件\AI\codeX\monitoring-platform\backend\.venv\Scripts\python.exe' -m pytest -q`

Expected: all manager and proxy tests pass.

- [ ] **Step 5: Perform manual smoke verification**

Run:

```powershell
& 'E:\个人文件\AI\codeX\monitoring-platform\backend\.venv\Scripts\python.exe' '.\scripts\run_manager.py'
```

Expected:

- manager opens on its configured port
- login page renders
- at least one imported preset appears or can be created
- start and stop buttons visibly change proxy state
- the displayed Codex base URL matches the running proxy port

## Self-Review

- Spec coverage:
  - architecture and process separation covered in Tasks 3 to 6
  - local config and GitHub-safe storage covered in Tasks 1 and 6
  - password protection covered in Tasks 2 and 5
  - visual dashboard and preset workflows covered in Tasks 5 and 6
  - provider connectivity testing covered in Task 5
  - runtime process control and logs covered in Task 4
- Placeholder scan:
  - no `TBD`, `TODO`, or “similar to previous task” placeholders remain
- Type consistency:
  - `ManagerStore`, `ProcessManager`, and `SessionStore` names are consistent across tasks
  - `manager_session` cookie name and `active_preset` payload naming are used consistently
