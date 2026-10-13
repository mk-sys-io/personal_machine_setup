#!/usr/bin/env bash
set -euo pipefail

# graceful-close.sh [poweroff|reboot] — close all Sway views politely, then power action.
# Called directly from the rofi `power` menu. Queues sudo systemctl ONLY
# after the tree is empty (the Ark POWER allowlist covers it NOPASSWD).
#
# ABORT POLICY: unsaved work is undetectable (no Wayland dirty-bit) — a
# window surviving 20s close + 5s TERM means needs-attention (save dialog /
# hang). Abort shutdown, focus it, notify. Never SIGKILL user data away;
# re-run after saving proceeds. Shutdown/Reboot only.

MODE="${1:-}"
case "$MODE" in
    poweroff|reboot) ;;
    *) echo "graceful-close.sh: usage: $0 [poweroff|reboot]" >&2; exit 2 ;;
esac

# File log: rofi swallows stderr, so every run appends here (never silent).
LOG_DIR="${HOME}/.local/state"
mkdir -p "$LOG_DIR" 2>/dev/null || true
exec >> "${LOG_DIR}/graceful-close.log" 2>&1 || true
echo "graceful-close.sh: --- $(date '+%F %T') mode=$MODE ---"

command -v swaymsg >/dev/null 2>&1 || { echo "graceful-close.sh: swaymsg missing" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "graceful-close.sh: jq missing" >&2; exit 2; }
[ -n "${SWAYSOCK:-}" ] || { echo "graceful-close.sh: SWAYSOCK unset (run inside Sway)" >&2; exit 2; }

# SIGNAL-DISCIPLINE (do not "simplify" — every rule below is load-bearing;
# precedents cited so a future reader can verify instead of trusting):
# - Signal ONLY con PIDs. sway sets view->pid from wl_client_get_credentials
#   on the surface owner, so the con PID IS the UI-connection owner = the
#   main/supervisor process (Chromium browser, Firefox GeckoMain, Electron
#   main, game main, foot server). It supervises its own children teardown.
# - NEVER signal by process name (pkill/killall). All Chromium/Electron
#   processes share one comm, so name-matching hits renderer/GPU/zygote and
#   guarantees exit_type=Crashed (Bonus: cross-prefix kills for Wine).
#   Precedents for main-only: Chromium SIGTERM->GracefulShutdownHandler
#   (chrome/browser/shutdown_signal_handlers_posix.cc; Normal is stamped in
#   ~ExitTypeService, i.e. very late — kill before it = Crashed); Firefox
#   parent-only SIGTERM handler (XRE_IsParentProcess; blanket kills race
#   sessionstore-final-state-write-complete, bmo#1908293); Electron main-only
#   handler (blanket kills bypass before-quit/hot-exit flush); Wine ordered
#   `wineserver -k` per prefix (never blanket). Model copied: ChromeOS
#   session_manager (SIGTERM main -> 3s -> SIGKILL).
# - Children are NEVER signalled directly: they exit via their supervisor or
#   are reaped-waited (Phase D); only true stragglers get KILL, and only by
#   PID from the live tree, never by name.
# - Known limits: setuid-reparented orphans (Chromium zygote) escape the
#   /proc descent — stateless launchers, systemd reaps them; harmless.
#   Wine servers aren't cons and exit ~3s after their last client; no -k needed.
#
# Snapshot once: all view cons (native Wayland + XWayland). One snapshot for
# kill list avoids PID-reuse / new-window races mid-loop.
list_ids() {
    swaymsg -t get_tree 2>/dev/null | jq -r '.. | objects | select(.type == "con" and ((.app_id != null) or (.window_properties != null))) | .id' 2>/dev/null || true
}

# Human label per con id (for logs/notifications only, never for signals).
con_name() {
    swaymsg -t get_tree 2>/dev/null | jq -r --argjson id "$1" '.. | objects | select(.id == $id) | (.app_id // .window_properties.class // "?")' 2>/dev/null | head -n 1
}

# con PIDs, re-resolved live (never trust a pre-wait snapshot for signals).
con_pids() {
    swaymsg -t get_tree 2>/dev/null | jq -r '.. | objects | select(.type == "con" and .pid != null and .pid > 1) | .pid' 2>/dev/null | sort -u || true
}

# Recursive descendants of given PIDs via /proc (no name matching, so no
# collisions with same-named daemons and no missed renamed processes).
descendants() {
    local queue=("$@") pid child seen
    seen=" $(printf '%s ' "$@")"
    while [ "${#queue[@]}" -gt 0 ]; do
        pid="${queue[0]}"
        queue=("${queue[@]:1}")
        for child in /proc/[0-9]*; do
            child="${child#/proc/}"
            case "$child" in ''|*[!0-9]*) continue ;; esac
            [ "$(awk '{print $4}' "/proc/$child/stat" 2>/dev/null)" = "$pid" ] || continue
            case "$seen" in *" $child "*) continue ;; esac
            seen="$seen$child "
            printf '%s\n' "$child"
            queue+=("$child")
        done
    done
}

# Full GUI set: con PIDs + their live descendant trees. Recomputed every
# poll tick, so forks/exits mid-wait are tracked.
gui_pids() {
    local roots topsPid
    roots="$(con_pids)"
    [ -n "$roots" ] || return 0
    printf '%s\n' "$roots"
    read -ra topsPid <<< "$roots"
    descendants "${topsPid[@]}" 2>/dev/null | sort -u || true
}

alive_pids() {
    local pid out=""
    for pid in $1; do
        [ -n "$pid" ] && [ -d "/proc/$pid" ] && out="$out $pid"
    done
    printf '%s' "$out"
}

count_views() {
    list_ids | grep -c . || true
}

# Phase A — polite: xdg_toplevel.close / WM_DELETE (= close button) per con.
start_ids="$(list_ids)"
if [ -z "$start_ids" ]; then
    echo "graceful-close.sh: no views open, nothing to close"
else
    start_names="$(printf '%s\n' "$start_ids" | while IFS= read -r id; do [ -n "$id" ] && con_name "$id"; done | sort -u | paste -sd, -)"
    echo "graceful-close.sh: closing $(printf '%s\n' "$start_ids" | grep -c .) views: $start_names"
fi
while IFS= read -r id; do
    [ -n "$id" ] || continue
    swaymsg "[con_id=$id] kill" >/dev/null 2>&1 || true
done < <(printf '%s\n' "$start_ids")

# Phase B — bounded wait: 20s polite window, single clock, 0.5s tick.
for _ in $(seq 1 40); do
    [ "$(count_views)" = "0" ] && break
    sleep 0.5
done
mid_ids="$(list_ids)"
if [ -z "$mid_ids" ]; then
    echo "graceful-close.sh: polite close clean: all views gone"
else
    mid_names="$(printf '%s\n' "$mid_ids" | while IFS= read -r id; do [ -n "$id" ] && con_name "$id"; done | sort -u | paste -sd, -)"
    echo "graceful-close.sh: polite survivors: $mid_names"
fi

if [ -n "$mid_ids" ]; then
    # Phase C — tiered fallback, con-PID-only per SIGNAL-DISCIPLINE:
    # re-resolve AFTER the wait (never trust the pre-wait snapshot),
    # TERM the UI owners so each supervisor tears down its own children.
    pids="$(con_pids)"
    for pid in $pids; do
        echo "graceful-close.sh: TERM con pid $pid ($(ps -o comm= -p "$pid" 2>/dev/null || echo '?'))"
        kill -TERM "$pid" 2>/dev/null || true
    done
    sleep 5
fi

survivors="$(list_ids)"
if [ -n "$survivors" ]; then
    first="$(printf '%s\n' "$survivors" | head -n 1)"
    name="$(con_name "$first")"
    swaymsg "[con_id=$first] focus" >/dev/null 2>&1 || true
    if command -v notify-send >/dev/null 2>&1; then
        notify-send "Shutdown aborted" "Save or close: ${name:-unknown window} — re-run after saving." || true
    fi
    echo "graceful-close.sh: aborted — survivor con $first (${name:-?})" | systemd-cat -t graceful-close 2>/dev/null || true
    echo "graceful-close.sh: aborted — survivor con $first (${name:-?}), shutdown NOT queued"
    exit 1
fi

# Phase D — reap wait: window-gone != process-exited-and-flushed. Chromium
# stamps Normal in ~ExitTypeService (very late); queueing power action before
# the browser PID is reaped kills it mid-flush -> Crashed next launch.
# Wait on the full GUI set (cons + descendants), ≤15s.
reap_start="$SECONDS"
stragglers=""
for _ in $(seq 1 30); do
    stragglers="$(alive_pids "$(gui_pids)")"
    [ -z "$stragglers" ] && break
    sleep 0.5
done
stragglers="$(alive_pids "$(gui_pids)")"
if [ -z "$stragglers" ]; then
    echo "graceful-close.sh: all GUI processes reaped in $((SECONDS - reap_start))s"
else
    echo "graceful-close.sh: WARN reap stragglers after 15s (pids:$stragglers), proceeding to $MODE"
fi

# Pre-reboot evidence: captured BEFORE the power action so a post-boot
# relaunch can't overwrite the diagnosis (Preferences is rewritten at startup).
for profile in "$HOME/.config/BraveSoftware/Brave-Browser/Default/Preferences" "$HOME/.config/google-chrome/Default/Preferences"; do
    [ -f "$profile" ] || continue
    echo "graceful-close.sh: pre-reboot exit_type ($profile): $(jq -r .profile.exit_type "$profile" 2>/dev/null)"
done

# Final action: direct sudo systemctl — the Ark POWER allowlist covers
# exactly these paths NOPASSWD, so no loginctl/inhibit layer is needed.
# Total failure notifies — never exits silently.
echo "graceful-close.sh: tree empty, queueing $MODE via sudo systemctl"
if [ "$MODE" = "poweroff" ]; then
    sudo /usr/bin/systemctl poweroff 2>&1 || {
        echo "graceful-close.sh: ERROR sudo systemctl poweroff failed"
        if command -v notify-send >/dev/null 2>&1; then
            notify-send "Shutdown failed" "See ~/.local/state/graceful-close.log" || true
        fi
        exit 2
    }
else
    sudo /usr/bin/systemctl reboot 2>&1 || {
        echo "graceful-close.sh: ERROR sudo systemctl reboot failed"
        if command -v notify-send >/dev/null 2>&1; then
            notify-send "Reboot failed" "See ~/.local/state/graceful-close.log" || true
        fi
        exit 2
    }
fi
