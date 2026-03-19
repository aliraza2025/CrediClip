#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  echo "Activate your virtualenv first (source .venv/bin/activate)." >&2
  exit 1
fi

VENV_PY="${VIRTUAL_ENV}/bin/python3"

"${VENV_PY}" -m pip install --no-cache-dir --index-url https://pypi.org/simple playwright

# Install OS dependencies when possible; continue if sudo is unavailable.
if command -v sudo >/dev/null 2>&1; then
  sudo "${VENV_PY}" -m playwright install-deps chromium || true
fi

"${VENV_PY}" -m playwright install chromium
echo "Playwright Chromium installation complete."
