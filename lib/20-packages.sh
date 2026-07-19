#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 20-packages.sh — Multi-method package installer
#
# Installs packages from declarative inventory files in packages/.
# Each method is idempotent — skips already-installed items.
# Exit 0 = all passed, exit 1 = all failed, exit 3 = partial success
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

PACKAGES_DIR="$REPO_ROOT/packages"
INSTALLED=0
FAILED=0

require_pkg_file() {
    local file="$PACKAGES_DIR/$1"
    if [[ ! -f "$file" ]]; then
        log_warn "$1 not found, skipping"
        return 0
    fi
    echo "$file"
}

# ---------------------------------------------------------------------------
# Bootstrap prerequisites (curl + gnupg needed for repo keys and downloads)
# ---------------------------------------------------------------------------

if ! pkg_installed curl || ! pkg_installed gnupg; then
    log "Bootstrapping curl + gnupg..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq curl gnupg
fi

# ---------------------------------------------------------------------------
# 1. install_apt_list — packages/apt.txt
# ---------------------------------------------------------------------------

install_apt_list() {
    local file
    file=$(require_pkg_file "apt.txt") || return 0

    log_step "APT packages"
    if ! sudo apt-get update -qq 2>/dev/null; then
        log_warn "apt-get update had errors — some packages may fail to install"
    fi

    local total=0
    local already=0
    local installed=0
    local failed=0

    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ ^# ]] && continue
        total=$(( total + 1 ))

        if pkg_installed "$line"; then
            already=$(( already + 1 ))
            continue
        fi

        if sudo apt-get install -y -qq "$line" >/dev/null 2>&1; then
            log_ok "$line"
            installed=$(( installed + 1 ))
        else
            log_error "$line failed to install"
            failed=$(( failed + 1 ))
        fi
    done < "$file"

    log "APT: $total total, $already existing, $installed installed, $failed failed"
    INSTALLED=$(( INSTALLED + installed ))
    FAILED=$(( FAILED + failed ))
}

# ---------------------------------------------------------------------------
# 2. install_apt_repos — packages/apt_repos.txt
# Format: name|check_cmd|key_url|keyring|repo_line|repo_file
# ---------------------------------------------------------------------------

install_apt_repos() {
    local file
    file=$(require_pkg_file "apt_repos.txt") || return 0

    log_step "APT repositories"

    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ ^# ]] && continue

        IFS='|' read -r name check_cmd key_url keyring repo_line repo_file <<< "$line"

        if cmd_exists "$check_cmd"; then
            log_ok "$name already installed"
            INSTALLED=$(( INSTALLED + 1 ))
            continue
        fi

        log "Adding repo: $name..."
        if curl -fsSL --connect-timeout "$CURL_TIMEOUT_CONNECT" --max-time "$CURL_TIMEOUT_API" "$key_url" | sudo gpg --batch --yes --dearmor -o "$keyring" 2>/dev/null \
           && echo "$repo_line" | sudo tee "$repo_file" >/dev/null \
           && sudo apt-get update -qq >/dev/null 2>&1; then
            log_ok "$name repo added"
            INSTALLED=$(( INSTALLED + 1 ))
        else
            log_error "$name repo setup failed"
            FAILED=$(( FAILED + 1 ))
        fi
    done < "$file"
}

# ---------------------------------------------------------------------------
# 3. install_github_debs — packages/github_deb.txt
# Format: name|repo|pattern|deps|version
# ---------------------------------------------------------------------------

install_github_debs() {
    local file
    file=$(require_pkg_file "github_deb.txt") || return 0

    log_step "GitHub .deb releases"

    local auth_header=()
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        auth_header=(-H "Authorization: token $GITHUB_TOKEN")
    fi

    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ ^# ]] && continue

        IFS='|' read -r name repo pattern deps _version <<< "$line"

        if pkg_installed "$name"; then
            log_ok "$name already installed"
            INSTALLED=$(( INSTALLED + 1 ))
            continue
        fi

        log "Installing $name..."
        local url
        url=$(curl -s --connect-timeout "$CURL_TIMEOUT_CONNECT" --max-time "$CURL_TIMEOUT_API" "${auth_header[@]}" "https://api.github.com/repos/$repo/releases/latest" \
            | grep "browser_download_url.*$pattern" \
            | head -1 \
            | cut -d '"' -f 4) || true

        if [[ -z "$url" ]]; then
            log_warn "$name: could not determine download URL, skipping"
            FAILED=$(( FAILED + 1 ))
            continue
        fi

        local tmp_deb
        tmp_deb=$(mktemp)
        if curl -fsSL --max-time "$CURL_TIMEOUT_DOWNLOAD" -o "$tmp_deb" "$url"; then
            if [[ -n "$deps" ]]; then
                sudo apt-get install -y -qq $deps >/dev/null 2>&1 || true
            fi
            if sudo dpkg -i "$tmp_deb" >/dev/null 2>&1; then
                log_ok "$name installed"
                INSTALLED=$(( INSTALLED + 1 ))
            else
                log_error "$name dpkg install failed"
                FAILED=$(( FAILED + 1 ))
            fi
        else
            log_error "$name: download failed"
            FAILED=$(( FAILED + 1 ))
        fi
        rm -f "$tmp_deb"
    done < "$file"
}

# ---------------------------------------------------------------------------
# 4. install_github_binaries — packages/github_binary.txt
# Format: name|repo|pattern|dest|version
# ---------------------------------------------------------------------------

install_github_binaries() {
    local file
    file=$(require_pkg_file "github_binary.txt") || return 0

    log_step "GitHub binaries"

    local auth_header=()
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        auth_header=(-H "Authorization: token $GITHUB_TOKEN")
    fi

    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ ^# ]] && continue

        IFS='|' read -r name repo pattern dest _version <<< "$line"

        if cmd_exists "$name"; then
            log_ok "$name already installed"
            INSTALLED=$(( INSTALLED + 1 ))
            continue
        fi

        log "Installing $name..."
        local url
        url=$(curl -s --connect-timeout "$CURL_TIMEOUT_CONNECT" --max-time "$CURL_TIMEOUT_API" "${auth_header[@]}" "https://api.github.com/repos/$repo/releases/latest" \
            | grep "browser_download_url.*$pattern" \
            | head -1 \
            | cut -d '"' -f 4) || true

        if [[ -z "$url" ]]; then
            log_warn "$name: could not determine download URL, skipping"
            FAILED=$(( FAILED + 1 ))
            continue
        fi

        local tmp_bin
        tmp_bin=$(mktemp)
        if curl -fsSL --max-time "$CURL_TIMEOUT_DOWNLOAD" -o "$tmp_bin" "$url"; then
            sudo cp "$tmp_bin" "$dest"
            sudo chmod 755 "$dest"
            log_ok "$name installed to $dest"
            INSTALLED=$(( INSTALLED + 1 ))
        else
            log_error "$name: download failed"
            FAILED=$(( FAILED + 1 ))
        fi
        rm -f "$tmp_bin"
    done < "$file"
}

# ---------------------------------------------------------------------------
# 5. install_github_fonts — packages/github_fonts.txt
# Format: name|repo|pattern
# Downloads Nerd Font tarballs, extracts .ttf/.otf to ~/.local/share/fonts/
# ---------------------------------------------------------------------------

install_github_fonts() {
    local file
    file=$(require_pkg_file "github_fonts.txt") || return 0

    log_step "GitHub Nerd Fonts"

    local font_dir="$HOME/.local/share/fonts"
    mkdir -p "$font_dir"

    local auth_header=()
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        auth_header=(-H "Authorization: token $GITHUB_TOKEN")
    fi

    local fonts_installed=0

    # Rebuild font cache before detection — ensures prior installs are registered
    fc-cache -f "$font_dir" >/dev/null 2>&1 || true

    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ ^# ]] && continue

        IFS='|' read -r name repo pattern <<< "$line"

        # Check if font family is already registered in fontconfig
        if fc-list : family 2>/dev/null | grep -qi "$name"; then
            log_ok "$name already installed"
            INSTALLED=$(( INSTALLED + 1 ))
            continue
        fi

        log "Installing $name..."
        local url
        url=$(curl -s --connect-timeout "$CURL_TIMEOUT_CONNECT" --max-time "$CURL_TIMEOUT_API" "${auth_header[@]}" "https://api.github.com/repos/$repo/releases/latest" \
            | grep "browser_download_url.*$pattern" \
            | head -1 \
            | cut -d '"' -f 4) || true

        if [[ -z "$url" ]]; then
            log_warn "$name: could not determine download URL, skipping"
            FAILED=$(( FAILED + 1 ))
            continue
        fi

        local tmp_archive
        tmp_archive=$(mktemp --suffix=".tar.xz")
        if curl -fsSL --max-time "$CURL_TIMEOUT_DOWNLOAD" -o "$tmp_archive" "$url"; then
            if tar -xJf "$tmp_archive" -C "$font_dir" --strip-components=0 \
                --wildcards '*.ttf' --wildcards '*.otf' 2>/dev/null \
               || tar -xJf "$tmp_archive" -C "$font_dir" 2>/dev/null; then
                log_ok "$name extracted to $font_dir"
                INSTALLED=$(( INSTALLED + 1 ))
                fonts_installed=1
            else
                log_error "$name: extraction failed"
                FAILED=$(( FAILED + 1 ))
            fi
        else
            log_error "$name: download failed"
            FAILED=$(( FAILED + 1 ))
        fi
        rm -f "$tmp_archive"
    done < "$file"

    # Always rebuild font cache — ensures fonts from prior interrupted runs get registered
    if fc-cache -fv "$font_dir" >/dev/null 2>&1; then
        log_ok "Font cache updated"
    else
        log_warn "fc-cache failed — fonts may not be detected until cache is rebuilt"
    fi
}

# ---------------------------------------------------------------------------
# 6. install_go_installs — packages/go_installs.txt
# Format: name|import_path|version
# ---------------------------------------------------------------------------

install_go_installs() {
    local file
    file=$(require_pkg_file "go_installs.txt") || return 0

    if ! cmd_exists go; then
        log_error "go not found — install golang-go first"
        FAILED=$(( FAILED + 1 ))
        return 0
    fi

    log_step "Go tools"

    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ ^# ]] && continue

        IFS='|' read -r name import_path version <<< "$line"

        if [[ -x "$HOME/go/bin/$name" ]]; then
            log_ok "$name already installed"
            INSTALLED=$(( INSTALLED + 1 ))
            continue
        fi

        log "Installing $name..."
        if go install "${import_path}@${version}" 2>/dev/null; then
            log_ok "$name installed"
            INSTALLED=$(( INSTALLED + 1 ))
        else
            log_error "$name failed to install"
            FAILED=$(( FAILED + 1 ))
            continue
        fi

        # Copy to system path for root usage
        if [[ -x "$HOME/go/bin/$name" && ! -f "/usr/local/bin/$name" ]]; then
            sudo cp "$HOME/go/bin/$name" "/usr/local/bin/$name"
            log_ok "$name copied to /usr/local/bin/$name"
        fi
    done < "$file"
}

# ---------------------------------------------------------------------------
# 7. install_source_builds — Generic builder for git-cloned source projects
# Format: name|repo|bin|version
# tool: "cargo" or "make"
# ---------------------------------------------------------------------------

install_source_builds() {
    local tool="$1"
    local file="$2"

    case "$tool" in
        cargo)
            [[ -d "$HOME/.cargo/bin" ]] && export PATH="$HOME/.cargo/bin:$PATH"
            if ! cmd_exists rustc; then
                log "Installing Rust toolchain..."
                if curl --proto '=https' --tlsv1.2 -sSf \
                    --connect-timeout "$CURL_TIMEOUT_CONNECT" \
                    --max-time "$CURL_TIMEOUT_INSTALL" \
                    https://sh.rustup.rs | sh -s -- -y 2>/dev/null; then
                    source "$HOME/.cargo/env"
                    log_ok "Rust toolchain installed"
                else
                    log_error "Rust toolchain installation failed"
                    FAILED=$(( FAILED + 1 ))
                    return 0
                fi
            fi ;;
        make)
            if ! cmd_exists make; then
                log_error "make not found"
                FAILED=$(( FAILED + 1 ))
                return 0
            fi ;;
    esac

    log_step "$tool builds"

    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ ^# ]] && continue

        IFS='|' read -r name repo bin version <<< "$line"

        if cmd_exists "$bin"; then
            log_ok "$name already installed"
            INSTALLED=$(( INSTALLED + 1 ))
            continue
        fi

        log "Building $name from source..."
        local build_dir
        build_dir=$(mktemp -d)

        if retry 3 git clone "https://github.com/$repo" "$build_dir" 2>/dev/null; then
            local build_ok=false
            case "$tool" in
                cargo)
                    local args=("--release")
                    [[ "$version" != "latest" && -n "$version" ]] && args+=("-p" "$name")
                    (cd "$build_dir" && cargo build "${args[@]}") 2>/dev/null && build_ok=true ;;
                make)
                    # Fix upstream link order: LDFLAGS must come after object files
                    sed -i 's/$(CC) $(CFLAGS) $(LDFLAGS)/$(CC) $(CFLAGS)/' "$build_dir/makefile"
                    sed -i 's/\$@ \$\^/\$@ \$^ $(LDFLAGS)/' "$build_dir/makefile"
                    (cd "$build_dir" && make) 2>/dev/null && build_ok=true ;;
            esac

            if [[ "$build_ok" == true ]]; then
                local bin_path
                case "$tool" in
                    cargo) bin_path=$(find "$build_dir/target/release" -maxdepth 1 -name "$bin" -type f | head -1) ;;
                    make)  bin_path=$(find "$build_dir" -maxdepth 1 -name "$bin" -type f | head -1) ;;
                esac
                if [[ -n "$bin_path" ]]; then
                    sudo cp "$bin_path" "/usr/local/bin/$bin"
                    sudo chmod 755 "/usr/local/bin/$bin"
                    log_ok "$name installed to /usr/local/bin/$bin"
                    INSTALLED=$(( INSTALLED + 1 ))
                else
                    log_error "$name: binary not found after build"
                    FAILED=$(( FAILED + 1 ))
                fi
            else
                log_error "$name: $tool build failed"
                FAILED=$(( FAILED + 1 ))
            fi
        else
            log_error "$name: git clone failed"
            FAILED=$(( FAILED + 1 ))
        fi

        rm -rf "$build_dir"
    done < "$file"
}

# ---------------------------------------------------------------------------
# 8. install_curl_scripts — packages/curl_scripts.txt
# Format: name|check_cmd|url|shell
# ---------------------------------------------------------------------------

install_curl_scripts() {
    local file
    file=$(require_pkg_file "curl_scripts.txt") || return 0

    log_step "Curl-script tools"

    [[ -n "${OPENCODE_PATH:-}" ]] && export PATH="$OPENCODE_PATH/bin:$PATH"

    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ ^# ]] && continue

        IFS='|' read -r name check_cmd url shell <<< "$line"

        if cmd_exists "$check_cmd"; then
            log_ok "$name already installed"
            INSTALLED=$(( INSTALLED + 1 ))
            continue
        fi

        if [[ -z "$url" ]]; then
            log_error "$name: URL is empty, skipping"
            FAILED=$(( FAILED + 1 ))
            continue
        fi

        if [[ "$shell" != "sh" && "$shell" != "bash" ]]; then
            log_error "$name: invalid shell '$shell' (must be sh or bash)"
            FAILED=$(( FAILED + 1 ))
            continue
        fi

        log "Installing $name..."
        local tmp_script
        tmp_script=$(mktemp)

        if curl -fsSL --max-time "$CURL_TIMEOUT_DOWNLOAD" -o "$tmp_script" "$url"; then
            if "$shell" "$tmp_script" 2>/dev/null; then
                log_ok "$name installed"
                INSTALLED=$(( INSTALLED + 1 ))
            else
                log_error "$name: install script failed"
                FAILED=$(( FAILED + 1 ))
            fi
        else
            log_error "$name: download failed (possible 404)"
            FAILED=$(( FAILED + 1 ))
        fi
        rm -f "$tmp_script"
    done < "$file"
}

# ---------------------------------------------------------------------------
# 9. enable_services — hardcoded list
# ---------------------------------------------------------------------------

enable_services() {
    log_step "Enabling services"
    for svc in NetworkManager nftables; do
        if systemctl is-enabled "$svc" &>/dev/null; then
            log_ok "$svc already enabled"
        else
            sudo systemctl enable --now "$svc" 2>/dev/null && log_ok "$svc enabled" || log_warn "$svc enable failed"
        fi
    done
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log_step "Package installation"

install_apt_list
install_apt_repos
install_github_debs
install_github_binaries
install_github_fonts
install_go_installs
install_source_builds cargo "$PACKAGES_DIR/cargo_builds.txt"
install_source_builds make  "$PACKAGES_DIR/make_builds.txt"
install_curl_scripts
enable_services

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

log_step "Packages complete: $INSTALLED installed, $FAILED failed"

if (( FAILED > 0 && INSTALLED > 0 )); then
    exit 3
elif (( FAILED > 0 )); then
    exit 1
fi

exit 0
