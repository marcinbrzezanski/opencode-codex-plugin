#!/usr/bin/env bash
set -euo pipefail

PLUGIN_CHECKOUT="$HOME/.local/share/opencode-codex-plugin"

uv pip install --system -r mini_plm/requirements.txt
if [ -d "$PLUGIN_CHECKOUT/.git" ]; then
  git -C "$PLUGIN_CHECKOUT" fetch --depth 1 origin main
  git -C "$PLUGIN_CHECKOUT" checkout --force origin/main
fi
