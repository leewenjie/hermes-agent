#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
export HERMES_HOSTED_RUNTIME_SHARED_SECRET="test-shared-secret-for-local-dev-32chars!!"
export HERMES_TUI_DIR="${HERMES_TUI_DIR:-$SCRIPT_DIR/ui-tui}"

# Keep provider credentials in a git-ignored local file, never in this script.
if [[ -f .env.local ]]; then
	set -a
	source .env.local
	set +a
fi

# Source oxaide env vars
set -a
source .env.oxaide
set +a

# Match production's authoritative managed-scope precedence. The project .env
# intentionally contains placeholder tenant pins and would otherwise overwrite
# the values sourced above during Hermes startup.
MANAGED_ENV_DIR="$(mktemp -d)"
trap 'rm -rf "$MANAGED_ENV_DIR"' EXIT
ln -s "$SCRIPT_DIR/.env.oxaide" "$MANAGED_ENV_DIR/.env"
export HERMES_MANAGED_DIR="$MANAGED_ENV_DIR"

.venv/bin/hermes dashboard --port 9119 --host 127.0.0.1 --no-open
