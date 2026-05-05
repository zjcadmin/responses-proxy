# Responses Proxy Web Manager Design

Date: 2026-04-29

## Summary

Upgrade `responses-proxy` from a script-driven local proxy into a two-part local tool:

1. a password-protected management console that stays online on its own configurable port
2. a separately managed proxy process that still exposes the OpenAI-style `POST /v1/responses` endpoint for Codex

The management console becomes the primary user interface for:

- storing multiple model presets
- switching the active preset
- starting, stopping, and restarting the proxy process
- testing upstream connectivity
- viewing recent status and logs

This redesign replaces the current workflow of manually editing `.env` and `model-config.json` for each provider. It is specifically intended to make switching between providers such as DeepSeek and Mimo fast, visible, and low-risk.

## Goals

- Provide a polished, local-first web UI that is easy to understand without reading docs first.
- Support multiple saved model presets with per-preset upstream settings and proxy listen settings.
- Protect the management console with a single access password.
- Start and stop the actual proxy service from the web UI instead of relying on separate terminal windows.
- Keep secrets and runtime artifacts out of GitHub-friendly files.
- Preserve the existing proxy protocol translation behavior for Codex.

## Non-Goals

- Multi-user accounts or role-based permissions
- Encrypting API keys at rest in the first version
- Cloud deployment or public internet hardening
- Replacing the proxy translation logic with a different upstream protocol
- Implementing real hosted tools such as `web_search` execution

## Current Problems

- Switching providers requires manual file edits and process restarts.
- The start and stop flow depends on batch or PowerShell scripts and gives limited visibility.
- Users cannot see current active configuration, last failure reason, or recent logs in one place.
- Sensitive configuration is easy to mix with shareable project files.
- Provider differences such as custom paths or custom headers are awkward to manage manually.

## Recommended Architecture

### High-Level Shape

The project will contain two runtimes in the same repository:

- `manager`
  A FastAPI-based local management service with HTML, CSS, and JavaScript assets for the web UI.
- `proxy`
  The existing FastAPI `responses` proxy, still run as a separate child process.

The management console is always-on and owns lifecycle control of the proxy process.

### Process Model

- The manager listens on a dedicated management host and port such as `127.0.0.1:8899`.
- The proxy listens on the host and port defined by the currently active preset, such as `127.0.0.1:8800`.
- Starting the proxy from the manager launches a child Python process.
- Stopping the proxy uses the tracked child PID first, then falls back to port-based lookup if needed.
- Restarting the proxy stops the current child process, regenerates the runtime config snapshot, and launches a new child process.

### Separation of Responsibilities

The manager is responsible for:

- password-based access control
- storing and editing presets
- storing manager settings
- tracking the active preset
- testing provider connectivity
- tracking proxy status
- launching and stopping the proxy process
- collecting recent events and logs

The proxy remains responsible for:

- validating downstream requests
- converting OpenAI-style `responses` requests to upstream `chat/completions`
- streaming SSE responses
- maintaining short-lived conversation memory

## Configuration and Storage Design

### Private Local Files

#### `manager-config.json`

Stores management console settings:

- `manager_host`
- `manager_port`
- `session_secret`
- `password_hash`
- `password_salt`
- `local_only`
- `log_tail_lines`
- `runtime_dir`

This file is local-only and must be ignored by Git.

#### `model-presets.json`

Stores all local model presets and the current active preset id.

Each preset contains:

- `id`
- `name`
- `provider`
- `base_url`
- `chat_path`
- `api_key`
- `model`
- `proxy_host`
- `proxy_port`
- `request_timeout_seconds`
- `headers`
- `description`
- `enabled`

Optional future-friendly fields may also be supported from the start:

- `body_overrides`
- `auth_mode`
- `api_key_header_name`

These fields make it easier to adapt to providers that are close to OpenAI compatibility but still need custom headers or request tweaks.

#### `runtime/`

Stores transient runtime artifacts:

- `proxy.pid`
- `proxy-launch.json`
- `manager-events.log`
- `proxy.stdout.log`
- `proxy.stderr.log`
- session state files if needed

The whole directory is Git-ignored.

### Shareable Files

#### `manager-config.example.json`

Template for management settings without real password material.

#### `model-presets.example.json`

Template that documents all preset fields without real API keys.

#### `.env.example`

May remain for legacy compatibility, but it is no longer the primary user workflow.

### GitHub Safety

The repository should include:

- code
- example configs
- docs
- scripts
- static assets

The repository should not include:

- real API keys
- real password hashes generated from the user password
- local session data
- runtime logs
- pid files

## Authentication Design

### Login Model

- Single password login only
- No username field in the first version
- Password stored as hash plus salt in `manager-config.json`
- Session cookie issued on successful login
- All management routes except the login page and static assets require authentication

### Session Behavior

- Local-only cookie by default
- HTTP-only cookie
- SameSite set to `Lax`
- Session invalidated on logout

## Web UI Design

### UI Approach

Use FastAPI to serve a custom single-page management console composed of:

- one HTML shell
- one dedicated CSS file
- one dedicated JavaScript file

This keeps the stack simple while still allowing a polished interface. A full SPA framework is not required for the first version.

### Visual Direction

- Dark, tool-like workspace with warm accent colors instead of generic admin purple
- Strong information hierarchy with bold panels and status chips
- Soft gradients and subtle glass effects to avoid a flat admin look
- Large primary action buttons for start, stop, and restart
- Card-based preset layout with clear active and running states
- Responsive layout that still prioritizes desktop clarity

### Screens and Panels

#### Login Screen

- App title and short description
- Single password field
- Show or hide password toggle
- Inline error message on failure

#### Dashboard Header

Shows:

- current proxy running state
- active preset name
- proxy base URL for Codex
- manager version or status marker

#### Proxy Control Panel

Primary actions:

- `Start Proxy`
- `Stop Proxy`
- `Restart Proxy`

Status details:

- current PID
- current proxy host and port
- last started time
- last stop reason or startup failure

#### Preset Library

Each preset card shows:

- preset name
- provider name
- model name
- base URL
- chat path
- proxy port
- active badge
- running badge if currently live

Each preset card actions:

- `Activate`
- `Edit`
- `Delete`
- `Test Connection`
- `Start With This Preset`

#### Preset Editor

Open as modal or side panel instead of full page navigation.

Fields:

- preset name
- provider
- base URL
- chat path
- model
- API key
- proxy host
- proxy port
- request timeout
- custom headers as JSON
- optional description

Validation rules:

- valid URL
- valid integer port range
- required fields present
- headers parse as object JSON
- no duplicate preset names unless intentionally allowed

#### Logs and Events Panel

Displays:

- recent manager events
- recent proxy stdout lines
- recent proxy stderr lines
- latest connection test result

### User Journey

1. Open manager URL
2. Enter password
3. Create or import a preset
4. Activate the preset
5. Test the connection
6. Start the proxy
7. Copy the proxy base URL into Codex

## Process and Runtime Flow

### Start Proxy

When the user clicks start:

1. verify manager session
2. ensure an active preset exists
3. validate required preset fields
4. ensure target proxy port is free
5. write `runtime/proxy-launch.json`
6. launch the proxy child process with the runtime config
7. begin tailing child stdout and stderr into runtime logs
8. update manager state and UI

### Stop Proxy

When the user clicks stop:

1. verify manager session
2. attempt to terminate the tracked child PID
3. if PID is missing or stale, resolve by configured proxy port
4. mark proxy as stopped
5. record the event in the manager event log

### Restart Proxy

Restart follows stop then start, preserving the currently active preset unless the user explicitly starts with another preset.

### Activate Preset While Running

The UI should make the distinction explicit:

- `Activate Only`
- `Activate and Restart Proxy`

This prevents false expectations that a saved preset change is already live.

## Upstream Compatibility Strategy

The manager redesign alone does not solve all provider differences. To support a wider set of upstream providers cleanly, the proxy runtime config should be expanded so each preset can control:

- upstream base URL
- upstream chat path
- upstream model
- request timeout
- additional headers

The manager should also support a lightweight connection test endpoint that sends a minimal upstream request using the selected preset and reports:

- success
- authentication failure
- timeout
- DNS or network failure
- invalid model
- invalid upstream path
- unexpected response format

This is important for providers such as Mimo where the problem may be the provider settings rather than the `responses` translation logic itself.

## Backend API Surface for the Manager

Suggested internal endpoints:

- `GET /`
  Serve the management UI shell
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/session`
- `GET /api/status`
- `GET /api/presets`
- `POST /api/presets`
- `PUT /api/presets/{preset_id}`
- `DELETE /api/presets/{preset_id}`
- `POST /api/presets/{preset_id}/activate`
- `POST /api/presets/{preset_id}/test`
- `POST /api/proxy/start`
- `POST /api/proxy/stop`
- `POST /api/proxy/restart`
- `GET /api/logs`

All `/api/*` routes except login should require a valid session.

## File and Module Plan

Suggested additions:

- `app/manager_main.py`
  Manager FastAPI app entrypoint
- `app/manager_config.py`
  Manager config and preset models
- `app/manager_store.py`
  Read and write manager config and presets
- `app/process_manager.py`
  Child process launch, stop, restart, status, and log helpers
- `app/auth.py`
  Password hashing, cookie session helpers
- `app/templates/index.html`
  HTML shell
- `app/static/styles.css`
  Dashboard styling
- `app/static/app.js`
  Client-side dashboard logic
- `scripts/run_manager.py`
  Start the management console

Existing modules to keep and adapt:

- `app/main.py`
  Remains the proxy app
- `app/config.py`
  Split or extend so proxy runtime config can be loaded from generated runtime files instead of only static local files
- `scripts/run_proxy.py`
  Updated to accept runtime config paths generated by the manager

## Migration Plan

### User-Facing Migration

- Existing `start-proxy.*` and `stop-proxy.*` scripts can remain temporarily for backward compatibility.
- The new primary entrypoint becomes the manager launcher.
- The README should be updated to emphasize the web manager flow first.

### Data Migration

On first run, the manager can optionally:

- read existing `.env`
- read existing `model-config.json`
- offer to import them as an initial preset

This reduces friction for the current DeepSeek setup.

## Testing Strategy

### Backend Tests

- login success and failure
- session-protected routes
- preset CRUD
- active preset switching
- config persistence
- proxy start success
- proxy stop success
- proxy restart success
- occupied port handling
- connection test success and failure mapping
- log retrieval

### UI Tests

At minimum:

- login flow
- create preset flow
- activate preset flow
- start and stop actions reflected in UI state
- validation errors shown clearly

### Regression Coverage

Keep the current proxy test suite so the web manager work does not break the existing translation behavior.

## Open Risks

- Provider quirks beyond headers and path may still require targeted proxy changes.
- Password-protected local management is sufficient for local use, but not enough for public deployment.
- Process state can become stale after crashes, so start and stop flows must re-check actual PID and port state rather than trusting only in-memory flags.

## First Version Acceptance Criteria

- User can launch a management console on a configurable local port.
- User can log in with a single password.
- User can create, edit, delete, and activate multiple presets from the web UI.
- User can store preset-specific API key, URL, path, model, timeout, proxy host and port, and custom headers.
- User can test provider connectivity from the web UI.
- User can start, stop, and restart the actual proxy process from the web UI.
- User can see whether the proxy is running and which preset is active.
- User can read recent manager and proxy logs from the web UI.
- Sensitive runtime and local config files are excluded from GitHub-ready project files.

## Implementation Recommendation

Implement this in one focused pass, but in layered order:

1. introduce manager config and preset storage
2. introduce process management for the proxy
3. add authenticated manager API routes
4. build the management UI
5. add import flow from current config
6. update docs and launcher scripts

This order keeps the critical behavior testable before the UI is finalized.
