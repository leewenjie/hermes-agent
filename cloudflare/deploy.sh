#!/usr/bin/env bash
# Hermes Agent — Cloudflare Deploy Script
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONNECTOR_DIR="$SCRIPT_DIR/connector"
CF_ENV_FILE="$SCRIPT_DIR/.env.cf"
[ -f "$CF_ENV_FILE" ] && { set -a; source "$CF_ENV_FILE"; set +a; }

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[CF]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

check_prereqs() {
  command -v node >/dev/null 2>&1 || err "Node.js required"
  command -v npm >/dev/null 2>&1 || err "npm required"
}

deploy_connector() {
  log "Deploying CF Connector..."
  cd "$CONNECTOR_DIR"
  npm install --production=false
  npx tsc --noEmit
  npx wrangler deploy
  log "Connector deployed!"
}

build_runtime() {
  log "Building runtime image..."
  docker build -f "$SCRIPT_DIR/Dockerfile" -t "${CF_IMAGE_NAME:-hermes-agent:cf}" "$PROJECT_ROOT"
  log "Image: ${CF_IMAGE_NAME:-hermes-agent:cf}"
}

set_secrets() {
  cd "$CONNECTOR_DIR"
  for entry in \
    "TELEGRAM_BOT_TOKEN:Telegram Bot Token" \
    "DISCORD_BOT_TOKEN:Discord Bot Token" \
    "DISCORD_APPLICATION_PUBLIC_KEY:Discord App Public Key" \
    "SLACK_BOT_TOKEN:Slack Bot Token (xoxb-...)" \
    "SLACK_SIGNING_SECRET:Slack Signing Secret" \
    "WHATSAPP_ACCESS_TOKEN:WhatsApp Access Token" \
    "WHATSAPP_PHONE_NUMBER_ID:WhatsApp Phone Number ID" \
    "RELAY_SHARED_SECRET:Relay Shared Secret" \
    "AGENT_RUNTIME_TOKEN:Agent Runtime Token"; do
    key="${entry%%:*}"; desc="${entry#*:}"
    read -r -p "$desc [$key]: " value
    [ -n "$value" ] && { echo "$value" | npx wrangler secret put "$key"; log "Set: $key"; }
  done
}

case "${1:-all}" in
  connector) check_prereqs; deploy_connector ;;
  runtime) build_runtime ;;
  secrets) set_secrets ;;
  all) check_prereqs; deploy_connector; build_runtime; log "Done! Set webhook URLs in platform consoles." ;;
  *) echo "Usage: $0 {connector|runtime|secrets|all}"; exit 1 ;;
esac
