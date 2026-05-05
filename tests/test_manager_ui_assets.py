from __future__ import annotations

from pathlib import Path
import subprocess


def test_manager_app_js_has_valid_syntax() -> None:
    app_js = Path(__file__).resolve().parents[1] / "app" / "static" / "app.js"

    result = subprocess.run(
        ["node", "--check", str(app_js)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_manager_app_pauses_polling_while_modal_is_open() -> None:
    app_js = Path(__file__).resolve().parents[1] / "app" / "static" / "app.js"
    source = app_js.read_text(encoding="utf-8")

    assert "if (!state.authenticated || state.modalOpen) return;" in source
    assert "stopPolling();" in source


def test_manager_app_uses_dashboard_shell_and_live_region_refreshes() -> None:
    app_js = Path(__file__).resolve().parents[1] / "app" / "static" / "app.js"
    source = app_js.read_text(encoding="utf-8")

    assert "function mountDashboardShell()" in source
    assert "async function refreshLiveRegions()" in source
    assert "syncDashboard();" in source
    assert 'api("/api/settings")' in source
    assert 'id="settings-form"' in source
