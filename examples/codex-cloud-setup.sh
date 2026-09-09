#!/usr/bin/env bash
set -euo pipefail

PLUGIN_REPO="https://github.com/marcinbrzezanski/opencode-codex-plugin.git"
PLUGIN_CHECKOUT="$HOME/.local/share/opencode-codex-plugin"
AUTH_FILE="$HOME/.local/share/opencode/auth.json"

python3 -m pip install --upgrade "uv==0.8.15"
uv pip install --system -r mini_plm/requirements.txt
npm install --global opencode-ai

if [ -d "$PLUGIN_CHECKOUT/.git" ]; then
  git -C "$PLUGIN_CHECKOUT" fetch --depth 1 origin main
  git -C "$PLUGIN_CHECKOUT" checkout --force origin/main
else
  git clone --depth 1 "$PLUGIN_REPO" "$PLUGIN_CHECKOUT"
fi

mkdir -p "$HOME/.agents/skills"
ln -sfn \
  "$PLUGIN_CHECKOUT/plugins/opencode-codex-plugin/skills/opencode-orchestrator" \
  "$HOME/.agents/skills/opencode-orchestrator"

if [ -n "${OPENCODE_API_KEY:-}" ]; then
  mkdir -p "$(dirname "$AUTH_FILE")"
  AUTH_FILE="$AUTH_FILE" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["AUTH_FILE"])
data = {}
if path.exists():
    data = json.loads(path.read_text(encoding="utf-8"))
data["opencode"] = {"type": "api", "key": os.environ["OPENCODE_API_KEY"]}
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
fi

opencode --version
python3 -m compileall -q mini_plm
