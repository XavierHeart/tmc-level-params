#!/usr/bin/env bash
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v codex >/dev/null 2>&1; then
  echo "Codex CLI was not found. Install or open Codex and make sure the codex command is available." >&2
  exit 1
fi

codex plugin marketplace add "$BUNDLE_DIR"
codex plugin add tmc-level-params@tmc-level-params

echo "Installation complete. Start a new Codex task and invoke: \$level-params"
