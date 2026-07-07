#!/bin/bash
# Pause/resume the knowledge pipeline automation (launchd watchers + sentinel).
# Usage: toggle_automation.sh {pause|resume|status}
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SENTINEL="$SCRIPT_DIR/.pipeline_paused"
UID_N="$(id -u)"
# com.dataofmen.wikiwatcher (duplicate watcher) was permanently retired 2026-07-07:
# two KeepAlive jobs racing for the single-instance lock made launchd respawn the
# loser every 10s forever. Its plist is parked at
# ~/Library/LaunchAgents/com.dataofmen.wikiwatcher.plist.disabled
LABELS=(com.hmkwon.dataofmen.watcher)
LA="$HOME/Library/LaunchAgents"

cmd="${1:-status}"

case "$cmd" in
  pause)
    printf 'paused %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$SENTINEL"
    for lbl in "${LABELS[@]}"; do
      launchctl bootout "gui/$UID_N/$lbl" 2>/dev/null || true
      launchctl disable "gui/$UID_N/$lbl" 2>/dev/null || true
    done
    echo "PAUSED: sentinel created, launchd watchers unloaded."
    ;;
  resume)
    rm -f "$SENTINEL"
    for lbl in "${LABELS[@]}"; do
      plist="$LA/$lbl.plist"
      if [ -f "$plist" ]; then
        launchctl enable "gui/$UID_N/$lbl" 2>/dev/null || true
        launchctl bootstrap "gui/$UID_N" "$plist" 2>/dev/null || true
        echo "  loaded $lbl"
      fi
    done
    echo "RESUMED: sentinel removed, launchd watchers loaded."
    ;;
  status)
    if [ -f "$SENTINEL" ]; then echo "sentinel: PRESENT (auto-trigger PAUSED)"; else echo "sentinel: absent (auto-trigger active)"; fi
    for lbl in "${LABELS[@]}"; do
      if launchctl list 2>/dev/null | grep -q "$lbl"; then echo "  launchd $lbl: LOADED"; else echo "  launchd $lbl: not loaded"; fi
    done
    ;;
  *)
    echo "Usage: $0 {pause|resume|status}"; exit 2;;
esac
