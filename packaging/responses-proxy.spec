# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPECPATH).parent
desktop_entry = project_root / "app" / "desktop_launcher.py"

datas = [
    (str(project_root / "app" / "static"), "app/static"),
    (str(project_root / "app" / "templates"), "app/templates"),
    (str(project_root / "README.md"), "."),
    (str(project_root / ".env.example"), "."),
    (str(project_root / "manager-config.example.json"), "."),
    (str(project_root / "model-config.example.json"), "."),
    (str(project_root / "model-presets.example.json"), "."),
]

a = Analysis(
    [str(desktop_entry)],  # entry: app/desktop_launcher.py
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[*collect_submodules("app"), "scripts.run_proxy"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ResponsesProxy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="Responses Proxy.app",
        icon=None,
        bundle_identifier="local.responses-proxy",
        info_plist={
            "CFBundleDisplayName": "Responses Proxy",
            "NSHighResolutionCapable": True,
        },
    )
