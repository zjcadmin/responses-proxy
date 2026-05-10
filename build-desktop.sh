#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_ROOT"

if [ -n "${PYTHON_BIN:-}" ]; then
  PYTHON="$PYTHON_BIN"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  PYTHON=python
fi

"$PYTHON" -m pip install -e ".[desktop]"
"$PYTHON" -m PyInstaller --noconfirm packaging/responses-proxy.spec

if [ -f "$PROJECT_ROOT/dist/ResponsesProxy" ]; then
  cp "$PROJECT_ROOT/packaging/responses-proxy.desktop" "$PROJECT_ROOT/dist/responses-proxy.desktop"
fi

printf '\nDesktop app build completed.\n'
printf 'Output directory: %s/dist\n' "$PROJECT_ROOT"
