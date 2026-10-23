#!/usr/bin/env bash
# rofi app launcher — explicit whitelist, strict (no custom entries).
set -euo pipefail

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/sway"
LIST="$CONFIG_DIR/rofi/apps.list"
RASI="$CONFIG_DIR/rofi/config.rasi"
SELF="$(readlink -f "$0")"

find_desktop() {
    local id="$1" dir
    local data_dirs=()
    IFS=: read -r -a data_dirs <<< "${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
    data_dirs=("${XDG_DATA_HOME:-$HOME/.local/share}" "${data_dirs[@]}")
    for dir in "${data_dirs[@]}"; do
        if [[ -f "$dir/applications/$id" ]]; then
            printf '%s\n' "$dir/applications/$id"
            return 0
        fi
    done
    return 1
}

desktop_field() {
    local file="$1" key="$2"
    awk -v key="$key" '
        /^\[Desktop Entry\]/ { in_group = 1; next }
        /^\[/ { in_group = 0 }
        in_group && index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }
    ' "$file"
}

clean_exec() {
    printf '%s' "$1" | sed -E 's/%[fFuUickdDnNvm]//g; s/^[[:space:]]+//; s/[[:space:]]+$//'
}

trim() {
    printf '%s' "$1" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//'
}

cmd_binary() {
    local cmd="$1" bin i=0
    local -a words=()
    read -r -a words <<< "$cmd"
    [[ ${words[0]:-} == "env" ]] && i=1
    while [[ $i -lt ${#words[@]} && ${words[$i]} == *=* ]]; do
        i=$((i + 1))
    done
    [[ $i -lt ${#words[@]} ]] || return 1
    bin="${words[$i]}"
    bin="${bin#\"}"
    bin="${bin%\"}"
    bin="${bin#\'}"
    bin="${bin%\'}"
    printf '%s\n' "$bin"
}

desktop_binary() {
    local file="$1" tryexec
    tryexec="$(desktop_field "$file" TryExec)"
    if [[ -n "$tryexec" ]]; then
        printf '%s\n' "$tryexec"
        return 0
    fi
    cmd_binary "$(clean_exec "$(desktop_field "$file" Exec)")"
}

binary_available() {
    local bin="$1"
    if [[ "$bin" == /* ]]; then
        [[ -x "$bin" ]]
    else
        command -v "$bin" >/dev/null 2>&1
    fi
}

list_override() {
    local want="$1" line entry over
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
        entry="${line%%|*}"
        over=""
        [[ "$line" == *"|"* ]] && over="${line#*|}"
        entry="${entry//[[:space:]]/}"
        if [[ "$entry" == "$want" ]]; then
            printf '%s' "$over"
            return 0
        fi
    done < "$LIST"
    return 0
}

launch() {
    local id="$1" path cmd terminal override bin
    [[ -n "$id" ]] || exit 0
    path="$(find_desktop "$id")" || exit 0
    override="$(trim "$(list_override "$id")")"
    if [[ -n "$override" ]]; then
        cmd="$override"
        bin="$(cmd_binary "$override")" || exit 0
    else
        cmd="$(clean_exec "$(desktop_field "$path" Exec)")"
        bin="$(desktop_binary "$path")" || exit 0
    fi
    [[ -n "$cmd" ]] || exit 0
    binary_available "$bin" || exit 0
    terminal="$(desktop_field "$path" Terminal)"
    if [[ "$terminal" == "true" ]]; then
        setsid -f kitty -e bash -c "$cmd" >/dev/null 2>&1
    else
        setsid -f bash -c "$cmd" >/dev/null 2>&1
    fi
}

if [[ -z "${ROFI_RETV:-}" ]]; then
    exec rofi -show apps -modi "apps:$SELF" -show-icons -config "$RASI"
fi

if [[ "$ROFI_RETV" == "1" ]]; then
    launch "${ROFI_INFO:-}"
    exit 0
fi

if [[ ! -r "$LIST" ]]; then
    printf 'app-launcher: cannot read %s\n' "$LIST" >&2
    exit 0
fi

printf '\0prompt\x1fLaunch\n'
printf '\0no-custom\x1ftrue\n'

while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    entry="${line%%|*}"
    entry="${entry//[[:space:]]/}"
    [[ -n "$entry" ]] || continue
    if ! path="$(find_desktop "$entry")"; then
        printf 'app-launcher: missing desktop entry: %s\n' "$entry" >&2
        printf '⚠ missing: %s\0nonselectable\x1ftrue\n' "$entry"
        continue
    fi
    override="$(trim "$(list_override "$entry")")"
    if [[ -n "$override" ]]; then
        bin="$(cmd_binary "$override")" || bin=""
    else
        bin="$(desktop_binary "$path")" || bin=""
    fi
    if [[ -z "$bin" ]] || ! binary_available "$bin"; then
        printf 'app-launcher: missing binary: %s (%s)\n' "$entry" "${bin:-unknown}" >&2
        printf '⚠ missing binary: %s\0nonselectable\x1ftrue\n' "$entry"
        continue
    fi
    name="$(desktop_field "$path" Name)"
    icon="$(desktop_field "$path" Icon)"
    keywords="$(desktop_field "$path" Keywords | tr ';' ',')"
    [[ -n "$name" ]] || name="$entry"
    [[ -n "$icon" ]] || icon="application-x-executable"
    printf '%s\0icon\x1f%s\0info\x1f%s\0meta\x1f%s %s\n' \
        "$name" "$icon" "$entry" "$entry" "$keywords"
done < "$LIST"
