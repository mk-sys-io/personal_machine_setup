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
    local file="$PACKAGES_DIR/apt.txt"
    if [[ ! -f "$file" ]]; then
        log_warn "apt.txt not found, skipping"
        return 0
    fi

    log_step "APT packages"
    sudo apt-get update -qq

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
    local file="$PACKAGES_DIR/apt_repos.txt"
    if [[ ! -f "$file" ]]; then
        log_warn "apt_repos.txt not found, skipping"
        return 0
    fi

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
    local file="$PACKAGES_DIR/github_deb.txt"
    if [[ ! -f "$file" ]]; then
        log_warn "github_deb.txt not found, skipping"
        return 0
    fi

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
    local file="$PACKAGES_DIR/github_binary.txt"
    if [[ ! -f "$file" ]]; then
        log_warn "github_binary.txt not found, skipping"
        return 0
    fi

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
# 5. install_go_installs — packages/go_installs.txt
# Format: name|import_path|version
# ---------------------------------------------------------------------------

install_go_installs() {
    local file="$PACKAGES_DIR/go_installs.txt"
    if [[ ! -f "$file" ]]; then
        log_warn "go_installs.txt not found, skipping"
        return 0
    fi

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
# 6. install_cargo_builds — packages/cargo_builds.txt
# Format: name|repo|bin|version
# ---------------------------------------------------------------------------

install_cargo_builds() {
    local file="$PACKAGES_DIR/cargo_builds.txt"
    if [[ ! -f "$file" ]]; then
        log_warn "cargo_builds.txt not found, skipping"
        return 0
    fi

    # Ensure Rust toolchain is available
    if ! cmd_exists rustc; then
        log "Installing Rust toolchain..."
        if curl --proto '=https' --tlsv1.2 -sSf --connect-timeout "$CURL_TIMEOUT_CONNECT" --max-time "$CURL_TIMEOUT_INSTALL" https://sh.rustup.rs | sh -s -- -y 2>/dev/null; then
            source "$HOME/.cargo/env"
            log_ok "Rust toolchain installed"
        else
            log_error "Rust toolchain installation failed"
            FAILED=$(( FAILED + 1 ))
            return 0
        fi
    fi

    # Ensure cargo is in PATH
    if ! cmd_exists cargo; then
        export PATH="$HOME/.cargo/bin:$PATH"
    fi

    log_step "Cargo builds"

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
            local build_args=("--release")
            if [[ "$version" != "latest" && -n "$version" ]]; then
                build_args+=("-p" "$name")
            fi

            if (cd "$build_dir" && cargo build "${build_args[@]}") 2>/dev/null; then
                local binary_path
                binary_path=$(find "$build_dir/target/release" -maxdepth 1 -name "$bin" -type f | head -1)
                if [[ -n "$binary_path" ]]; then
                    sudo cp "$binary_path" "/usr/local/bin/$bin"
                    sudo chmod 755 "/usr/local/bin/$bin"
                    log_ok "$name installed to /usr/local/bin/$bin"
                    INSTALLED=$(( INSTALLED + 1 ))
                else
                    log_error "$name: binary not found after build"
                    FAILED=$(( FAILED + 1 ))
                fi
            else
                log_error "$name: cargo build failed"
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
# 7. install_curl_scripts — packages/curl_scripts.txt
# Format: name|check_cmd|url|shell
# ---------------------------------------------------------------------------

install_curl_scripts() {
    local file="$PACKAGES_DIR/curl_scripts.txt"
    if [[ ! -f "$file" ]]; then
        log_warn "curl_scripts.txt not found, skipping"
        return 0
    fi

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
# 8. enable_services — hardcoded list
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
install_go_installs
install_cargo_builds
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
