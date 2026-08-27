#!/usr/bin/env bash
# Container entrypoint - Hermes host agent
set -euo pipefail

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [start] $*"
}

APP_HOME="${APP_HOME:-/app}"
HERMES_HOME="${HERMES_HOME:-/home/appuser/.hermes}"
export APP_HOME HERMES_HOME
export LOG_DIR="${LOG_DIR:-$APP_HOME/logs}"
export PYTHONUNBUFFERED=1
export HERMES_ENABLE_PROJECT_PLUGINS="${HERMES_ENABLE_PROJECT_PLUGINS:-true}"
export HERMES_SYSTEM_PROMPT_PATH="${HERMES_SYSTEM_PROMPT_PATH:-$APP_HOME/prompts/hermes_coordinator.md}"

mkdir -p "$LOG_DIR" "$HERMES_HOME/plugins" "$HERMES_HOME/logs"

# Named volumes often mount as root — fix ownership when we can (root entrypoint)
if [[ "$(id -u)" -eq 0 ]]; then
  chown -R appuser:appuser "$LOG_DIR" "$HERMES_HOME" 2>/dev/null || true
  chown -R appuser:appuser "$APP_HOME/data" 2>/dev/null || true
fi

# Hermes config. Re-seeded on every start, like SOUL.md below: the repo copy
# is the source of truth. Seeding only when absent meant an edit here -- say
# enabling a plugin -- silently never reached an existing volume.
if [[ -f "$APP_HOME/config/hermes_config.yaml" ]]; then
  cp "$APP_HOME/config/hermes_config.yaml" "$HERMES_HOME/config.yaml"
  log "Installed hermes config.yaml"
fi

# Hermes plugins. A standalone plugin is only loaded from $HERMES_HOME/plugins
# (or ./.hermes/plugins), never from the app tree, so each one is copied in.
# Replaced rather than merged so a file deleted from the repo also disappears
# here. This is how a custom tool reaches the real Hermes backend at all --
# AIAgent has no parameter for injecting Python tools.
if [[ -d "$APP_HOME/plugins" ]]; then
  for plugin_dir in "$APP_HOME"/plugins/*/; do
    [[ -d "$plugin_dir" ]] || continue
    plugin_name="$(basename "$plugin_dir")"
    rm -rf "${HERMES_HOME:?}/plugins/$plugin_name"
    cp -a "$plugin_dir" "$HERMES_HOME/plugins/$plugin_name"
    log "Installed Hermes plugin $plugin_name"
  done
fi

# Agent identity. Hermes reads $HERMES_HOME/SOUL.md as system-prompt slot #1;
# with no SOUL.md it falls back to its own "You are Hermes Agent" identity.
# Re-seeded on every start so the deployed identity is the one in version
# control rather than one the agent drifted into.
if [[ -f "$APP_HOME/prompts/soul.md" ]]; then
  cp "$APP_HOME/prompts/soul.md" "$HERMES_HOME/SOUL.md"
  log "Installed SOUL.md (agent identity)"
fi

# The copies above run as root; hand them back so Hermes can read and rewrite.
if [[ "$(id -u)" -eq 0 ]]; then
  chown appuser:appuser "$HERMES_HOME/config.yaml" "$HERMES_HOME/SOUL.md" 2>/dev/null || true
  chown -R appuser:appuser "$HERMES_HOME/plugins" 2>/dev/null || true
fi

log "Starting Hermes host service"

cd "$APP_HOME"
if [[ "$(id -u)" -eq 0 ]]; then
  exec runuser -u appuser -- python -m app.main
fi
exec python -m app.main
