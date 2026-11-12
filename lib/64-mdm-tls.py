#!/usr/bin/env python3
"""MDM TLS — Domain + HTTPS + on-demand cert renewal for Headwind MDM v7.

Configures Tomcat 10.1 with a TLS connector on port 443 using Let's Encrypt
certificates obtained via lego (DNS-01, DuckDNS provider). Certificates are
written as direct PEM files (no JKS keystore); Tomcat's OpenSSL implementation
reads them directly. Renewal is on-demand only (manual + boot hook), no timer.

Run standalone: python3 lib/64-mdm-tls.py
Wired into install.py as 64-mdm-tls

Exit codes:
    0  converged
    1  failure
    2  skip (in VM)
    3  partial (prereqs done, token missing)
Stdlib only.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))

from preconditions import require_user  # noqa: E402


# ── log facades ──────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    print(msg, flush=True)


def log_step(msg: str) -> None:
    print(f">>> {msg}", flush=True)


def log_ok(msg: str) -> None:
    print(f"  OK    {msg}", flush=True)


def log_warn(msg: str) -> None:
    print(f"  WARN  {msg}", file=sys.stderr, flush=True)


def log_error(msg: str) -> None:
    print(f"  ERROR {msg}", file=sys.stderr, flush=True)


def _result(status: str, changed: bool, message: str) -> None:
    safe = message.replace('"', "'").replace("\n", " ")
    print(
        f'RESULT {{"status": "{status}", "changed": {str(changed).lower()}, "message": "{safe}"}}'
    )


# ── constants ────────────────────────────────────────────────────────────────
SECRETS = Path.home() / ".config" / "mdm" / "secrets.env"

TOMCAT_BASE = Path("/var/lib/tomcat10")
SERVER_XML = Path("/etc/tomcat10/server.xml")
ROOT_XML = Path("/etc/tomcat10/Catalina/localhost/ROOT.xml")
SSL_DIR = Path("/etc/tomcat10/ssl")

REQUIRED_PKGS = ("lego",)

# lego path for durable state (account + certs)
LEGO_PATH = Path("/var/lib/mdm-le")

# Systemd oneshot unit for boot-time renewal
UNIT_NAME = "mdm-cert-renew.service"
UNIT_PATH = Path("/etc/systemd/system") / UNIT_NAME
WRAPPER_PATH = Path("/usr/local/sbin/mdm-cert-renew.sh")


# ── helpers ──────────────────────────────────────────────────────────────────
def is_vm() -> bool:
    return subprocess.run(["systemd-detect-virt", "-q"], check=False).returncode == 0


def _run_priv(*args: str, input_text: str | None = None) -> tuple[int, str]:
    p = subprocess.run(
        ["sudo", "-n", *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _port_holder(port: int) -> str:
    """One-line `ss` description of who holds a TCP port (for error messages)."""
    p = subprocess.run(
        ["ss", "-ltnp", f"sport = :{port}"], capture_output=True, text=True, check=False
    )
    rows = [line.strip() for line in (p.stdout or "").splitlines()[1:] if line.strip()]
    return rows[0] if rows else "no listener"


# ── load domain and email from config.txt ────────────────────────────────────
def load_domain() -> str:
    sys.path.insert(0, str(REPO_ROOT))
    from install import load_env  # noqa: E402

    d = load_env().get("MDM_DOMAIN", "").strip().lower()
    if not d:
        raise RuntimeError("MDM_DOMAIN not set in config.txt")
    if not re.match(r"^[a-z0-9.-]+\.duckdns\.org$", d):
        raise RuntimeError(f"MDM_DOMAIN must be a duckdns.org subdomain, got: {d}")
    return d


def load_acme_email() -> str:
    """Use GIT_USER_EMAIL from config.txt as the ACME account contact."""
    sys.path.insert(0, str(REPO_ROOT))
    from install import load_env  # noqa: E402

    email = load_env().get("GIT_USER_EMAIL", "").strip()
    # load_env already strips surrounding quotes; validate minimal shape
    if not email or "@" not in email:
        raise RuntimeError("GIT_USER_EMAIL not set or invalid in config.txt")
    return email


# ── secrets file ─────────────────────────────────────────────────────────────
def ensure_secrets_exist() -> None:
    """Ensure ~/.config/mdm/secrets.env exists (created by 63-mdm-server)."""
    if not SECRETS.is_file():
        raise RuntimeError(
            f"secrets file not found: {SECRETS} — run lib/63-mdm-server.py first"
        )


def load_duckdns_token() -> str | None:
    """Load DUCKDNS_TOKEN from secrets.env; returns None if absent."""
    if not SECRETS.is_file():
        return None
    for line in SECRETS.read_text().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            if k.strip() == "DUCKDNS_TOKEN":
                return v.strip()
    return None


# ── apt prerequisites verification ───────────────────────────────────────────
def verify_prereqs() -> None:
    missing = []
    for pkg in REQUIRED_PKGS:
        rc, out = _run_priv("dpkg-query", "-W", "-f=${Status}", pkg)
        if rc != 0 or "install ok installed" not in out:
            missing.append(pkg)
    if missing:
        raise RuntimeError(
            f"missing packages: {', '.join(missing)} — "
            f"run ./install.py --only 20 or add them to packages/apt.txt"
        )


# ── /etc/hosts entry ─────────────────────────────────────────────────────────
def ensure_hosts_entry(domain: str) -> bool:
    """Ensure 127.0.0.1 <domain> exists in /etc/hosts (idempotent)."""
    line = f"127.0.0.1 {domain}"
    rc, out = _run_priv("grep", "-Fx", line, "/etc/hosts")
    if rc == 0:
        return False
    log_step(f"adding {domain} to /etc/hosts")
    try:
        rc, out = _run_priv("tee", "-a", "/etc/hosts", input_text=line + "\n")
        if rc != 0:
            raise RuntimeError(f"tee /etc/hosts failed: {out.strip()}")
    finally:
        pass  # tmp created but unused, kept for symmetry
    return True


# ── lego --path directory ────────────────────────────────────────────────────
def ensure_lego_path() -> bool:
    """Ensure /var/lib/mdm-le exists with correct permissions."""
    rc, out = _run_priv("stat", "-c", "%U:%G:%a", str(LEGO_PATH))
    if rc == 0 and out.strip() == "root:root:700":
        return False
    log_step("creating lego state directory")
    _run_priv("mkdir", "-p", str(LEGO_PATH))
    _run_priv("chown", "root:root", str(LEGO_PATH))
    _run_priv("chmod", "0700", str(LEGO_PATH))
    return True


# ── renewal wrapper ──────────────────────────────────────────────────────────
RENEWAL_WRAPPER = """#!/bin/bash
# mdm-cert-renew.sh — on-demand LE cert renewal for Headwind MDM (Phase B2)
# Generated by lib/64-mdm-tls.py — do not edit manually.
# Runs as root; token sourced from ~/.config/mdm/secrets.env at runtime.

set -euo pipefail

SECRETS_FILE="/home/mike/.config/mdm/secrets.env"
LEGO_PATH="/var/lib/mdm-le"
DOMAIN="__DOMAIN__"
EMAIL="__EMAIL__"

# Load DUCKDNS_TOKEN from secrets.env (exact key match, no sourcing)
if [[ ! -f "$SECRETS_FILE" ]]; then
    echo "ERROR: $SECRETS_FILE not found" >&2
    exit 1
fi
DUCKDNS_TOKEN=$(grep -E '^DUCKDNS_TOKEN=' "$SECRETS_FILE" | cut -d= -f2-)
if [[ -z "$DUCKDNS_TOKEN" ]]; then
    echo "ERROR: DUCKDNS_TOKEN not set in $SECRETS_FILE" >&2
    exit 1
fi
export DUCKDNS_TOKEN

# Propagation margins per go-acme/lego#1744 (defaults 60s/2s time out)
export DUCKDNS_PROPAGATION_TIMEOUT=340
export DUCKDNS_POLLING_INTERVAL=10

lego_bin=$(command -v lego)
if [[ -z "$lego_bin" ]]; then
    echo "ERROR: lego not in PATH" >&2
    exit 1
fi

# Determine if first issuance or renewal
cert_file="$LEGO_PATH/certificates/$DOMAIN.crt"
if [[ ! -f "$cert_file" ]]; then
    echo "First issuance for $DOMAIN"
    "$lego_bin" --dns duckdns --email "$EMAIL" --domains "$DOMAIN" --path "$LEGO_PATH" \
        --accept-tos --key-type rsa2048 run --preferred-chain "ISRG Root X1"
else
    echo "Renewing cert for $DOMAIN"
    "$lego_bin" --dns duckdns --email "$EMAIL" --domains "$DOMAIN" --path "$LEGO_PATH" \
        --accept-tos --key-type rsa2048 renew --preferred-chain "ISRG Root X1"
fi

# Copy PEM to Tomcat ssl dir only if content changed
SSL_DIR="/etc/tomcat10/ssl"
mkdir -p "$SSL_DIR"
changed=0
if ! cmp -s "$LEGO_PATH/certificates/$DOMAIN.crt" "$SSL_DIR/$DOMAIN.crt"; then
    cp "$LEGO_PATH/certificates/$DOMAIN.crt" "$SSL_DIR/$DOMAIN.crt"
    changed=1
fi
if ! cmp -s "$LEGO_PATH/certificates/$DOMAIN.key" "$SSL_DIR/$DOMAIN.key"; then
    cp "$LEGO_PATH/certificates/$DOMAIN.key" "$SSL_DIR/$DOMAIN.key"
    changed=1
fi

if [[ $changed -eq 1 ]]; then
    chown root:tomcat "$SSL_DIR/$DOMAIN.crt" "$SSL_DIR/$DOMAIN.key"
    chmod 0640 "$SSL_DIR/$DOMAIN.crt" "$SSL_DIR/$DOMAIN.key"
    systemctl restart tomcat10
    echo "Cert updated; tomcat10 restarted"
else
    echo "Cert unchanged; no restart"
fi
"""


def render_renewal_wrapper(domain: str, email: str) -> bool:
    """Render /usr/local/sbin/mdm-cert-renew.sh with domain/email substituted."""
    content = RENEWAL_WRAPPER.replace("__DOMAIN__", domain).replace("__EMAIL__", email)
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write(content)
        tmp = Path(tf.name)
    try:
        rc, out = _run_priv(
            "install",
            "-D",
            "-m",
            "0755",
            "-o",
            "root",
            "-g",
            "root",
            str(tmp),
            str(WRAPPER_PATH),
        )
        if rc != 0:
            raise RuntimeError(f"installing renewal wrapper failed: {out.strip()}")
    finally:
        tmp.unlink(missing_ok=True)
    return True


# ── systemd oneshot unit ────────────────────────────────────────────────────
RENEWAL_UNIT = """[Unit]
Description=MDM TLS cert renewal (on-demand, boot hook)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/mdm-cert-renew.sh
# Runs as root (token read from secrets.env, lego state in /var/lib/mdm-le)

[Install]
WantedBy=multi-user.target
"""


def render_renewal_unit() -> bool:
    """Render /etc/systemd/system/mdm-cert-renew.service."""
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write(RENEWAL_UNIT)
        tmp = Path(tf.name)
    try:
        rc, out = _run_priv(
            "install",
            "-D",
            "-m",
            "0644",
            "-o",
            "root",
            "-g",
            "root",
            str(tmp),
            str(UNIT_PATH),
        )
        if rc != 0:
            raise RuntimeError(f"installing systemd unit failed: {out.strip()}")
    finally:
        tmp.unlink(missing_ok=True)
    return True


def enable_renewal_unit() -> bool:
    """Enable the systemd unit (idempotent)."""
    rc, out = _run_priv("systemctl", "is-enabled", UNIT_NAME)
    if rc == 0 and out.strip() == "enabled":
        return False
    log_step("enabling mdm-cert-renew.service")
    rc, out = _run_priv("systemctl", "daemon-reload")
    if rc != 0:
        raise RuntimeError(f"systemctl daemon-reload failed: {out.strip()}")
    rc, out = _run_priv("systemctl", "enable", UNIT_NAME)
    if rc != 0:
        raise RuntimeError(f"systemctl enable {UNIT_NAME} failed: {out.strip()}")
    return True


# ── server.xml TLS connector ─────────────────────────────────────────────────
def read_server_xml() -> str:
    rc, out = _run_priv("cat", str(SERVER_XML))
    if rc != 0:
        raise RuntimeError(f"reading {SERVER_XML} failed: {out.strip()}")
    return out


def connector_exists(xml: str, domain: str) -> bool:
    """Check if a TLS connector for this domain already exists."""
    # Look for the certificateFile pointing at our PEM
    needle = f'certificateFile="/etc/tomcat10/ssl/{domain}.crt"'
    return needle in xml


def insert_tls_connector(xml: str, domain: str) -> str:
    """Insert a TLS connector before </Service> in server.xml."""
    # Tomcat 10.1 JSSE connector (direct PEM, no JKS). JSSE pinned over
    # OpenSSLImplementation: Debian libtcnative-1 1.3.1 is below Tomcat's
    # 1.3.4 floor -> JVM SIGSEGV crash loop at connector init.
    connector = f"""    <Connector port="443" protocol="org.apache.coyote.http11.Http11NioProtocol"
           SSLEnabled="true"
           sslImplementationName="org.apache.tomcat.util.net.jsse.JSSEImplementation">
        <SSLHostConfig>
            <Certificate certificateFile="/etc/tomcat10/ssl/{domain}.crt"
                         certificateKeyFile="/etc/tomcat10/ssl/{domain}.key"
                         type="RSA" />
        </SSLHostConfig>
    </Connector>"""
    # Insert before the closing </Service> tag
    return xml.replace("</Service>", f"{connector}\n</Service>")


def backup_server_xml() -> None:
    """Create a root-only backup of server.xml before first edit."""
    backup = SERVER_XML.with_suffix(".xml.b2")
    rc, out = _run_priv("test", "-f", str(backup))
    if rc == 0:
        return
    log_step("backing up server.xml")
    _run_priv("cp", str(SERVER_XML), str(backup))
    _run_priv("chown", "root:tomcat", str(backup))
    _run_priv("chmod", "0640", str(backup))


def write_server_xml(content: str) -> None:
    """Write server.xml via temp file + install."""
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write(content)
        tmp = Path(tf.name)
    try:
        rc, out = _run_priv(
            "install",
            "-D",
            "-m",
            "0640",
            "-o",
            "root",
            "-g",
            "tomcat",
            str(tmp),
            str(SERVER_XML),
        )
        if rc != 0:
            raise RuntimeError(f"writing {SERVER_XML} failed: {out.strip()}")
    finally:
        tmp.unlink(missing_ok=True)


def verify_8080_untouched(xml: str) -> bool:
    """Assert the 8080 connector is still present and unchanged."""
    return 'port="8080"' in xml and 'protocol="HTTP/1.1"' in xml


# ── SSL directory and cert placement ─────────────────────────────────────────
def ensure_ssl_dir() -> bool:
    """Ensure /etc/tomcat10/ssl exists with correct perms."""
    rc, out = _run_priv("stat", "-c", "%U:%G:%a", str(SSL_DIR))
    if rc == 0 and out.strip() == "root:tomcat:750":
        return False
    log_step("creating Tomcat SSL directory")
    _run_priv("mkdir", "-p", str(SSL_DIR))
    _run_priv("chown", "root:tomcat", str(SSL_DIR))
    _run_priv("chmod", "0750", str(SSL_DIR))
    return True


def place_certs(domain: str) -> bool:
    """Copy certs from lego path to Tomcat ssl dir if content differs."""
    src_crt = LEGO_PATH / "certificates" / f"{domain}.crt"
    src_key = LEGO_PATH / "certificates" / f"{domain}.key"
    dst_crt = SSL_DIR / f"{domain}.crt"
    dst_key = SSL_DIR / f"{domain}.key"

    rc_crt, _ = _run_priv("test", "-s", str(src_crt))
    rc_key, _ = _run_priv("test", "-s", str(src_key))
    if rc_crt != 0 or rc_key != 0:
        return False

    changed = False
    for src, dst in [(src_crt, dst_crt), (src_key, dst_key)]:
        rc, out = _run_priv("cmp", "-s", str(src), str(dst))
        if rc != 0:
            _run_priv("cp", str(src), str(dst))
            changed = True
    if changed:
        _run_priv("chown", "root:tomcat", str(dst_crt), str(dst_key))
        _run_priv("chmod", "0640", str(dst_crt), str(dst_key))
    return changed


# ── issuance / renewal via wrapper ──────────────────────────────────────────
def run_renewal(domain: str) -> None:
    """Run the renewal wrapper (as root via run_priv)."""
    log_step(f"running cert issuance/renewal for {domain}")
    rc, out = _run_priv(str(WRAPPER_PATH))
    if rc != 0:
        raise RuntimeError(f"cert renewal failed (rc={rc}): {out.strip()}")


# ── ROOT.xml base.url https assertion ───────────────────────────────────────
def assert_root_xml_https(domain: str) -> None:
    """Assert ROOT.xml base.url uses https scheme."""
    rc, out = _run_priv("cat", str(ROOT_XML))
    if rc != 0:
        raise RuntimeError(f"reading {ROOT_XML} failed: {out.strip()}")
    if f"https://{domain}" not in out:
        raise RuntimeError(
            f"ROOT.xml base.url does not contain https://{domain} — "
            "the manual installer must have set this; check the answer sheet"
        )


# ── live HTTPS verification ──────────────────────────────────────────────────
def verify_https(domain: str) -> tuple[bool, str]:
    """Verify TLS on 443 via /etc/hosts resolution."""
    try:
        # curl via /etc/hosts (domain resolves to 127.0.0.1)
        with urllib.request.urlopen(f"https://{domain}/", timeout=10) as resp:
            code = resp.getcode()
            if code not in (200, 302):
                return False, f"https 443 returned {code} (holder: {_port_holder(443)})"
            body = resp.read(65536).decode("utf-8", "replace")
            if "MDM web panel" not in body:
                return False, "https 443 does not serve the MDM web panel"
            return True, f"https 443 serves the MDM web panel ({code})"
    except Exception as e:
        return False, f"https 443 failed: {e} (holder: {_port_holder(443)})"


def verify_http_8080() -> tuple[bool, str]:
    """Verify 8080 still serves the MDM panel (unchanged)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/", timeout=5) as resp:
            code = resp.getcode()
            body = resp.read(65536).decode("utf-8", "replace")
            if code not in (200, 302):
                return (
                    False,
                    f"http 8080 returned {code} (holder: {_port_holder(8080)})",
                )
            if "MDM web panel" not in body:
                return False, "http 8080 serves stock Tomcat page"
            return True, f"http 8080 serves the MDM web panel ({code})"
    except Exception as e:
        return False, f"http 8080 failed: {e} (holder: {_port_holder(8080)})"


def wait_for_panel(timeout: int = 120, interval: int = 2) -> None:
    """Bounded wait until tomcat has actually finished deploying the MDM webapp.

    `systemctl restart` returns when the service unit starts, not when the ROOT
    webapp finishes deploying (Liquibase + app init can take minutes). Probing
    sooner than that false-fails convergence. Success = 8080 answers with the
    MDM web panel (see verify_http_8080()).
    """
    log_step(f"waiting up to {timeout}s for the MDM web panel on 8080")
    deadline = time.monotonic() + timeout
    while True:
        ok, msg = verify_http_8080()
        if ok:
            log_ok(f"web panel ready: {msg}")
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"web panel not ready after {timeout}s — last probe: {msg}"
            )
        time.sleep(interval)


def verify_cert_valid(domain: str) -> tuple[bool, str]:
    """Check cert is valid and not expiring soon (openssl x509 -checkend)."""
    crt = SSL_DIR / f"{domain}.crt"
    rc, _ = _run_priv("test", "-s", str(crt))
    if rc != 0:
        return False, f"cert file missing: {crt}"
    rc, out = _run_priv("openssl", "x509", "-in", str(crt), "-checkend", "0", "-noout")
    if rc != 0:
        return False, f"cert expired or invalid: {out.strip()}"
    return True, "cert valid and not expired"


# ── convergence check (--verify) ────────────────────────────────────────────
def converged(domain: str) -> tuple[bool, list[str]]:
    msgs: list[str] = []
    ok = True

    # a) unit enabled + wrapper executable
    rc, out = _run_priv("systemctl", "is-enabled", UNIT_NAME)
    if rc == 0 and out.strip() == "enabled":
        msgs.append("mdm-cert-renew.service enabled")
    else:
        msgs.append(f"mdm-cert-renew.service not enabled (rc={rc}, out={out.strip()})")
        ok = False
    if WRAPPER_PATH.is_file() and os.access(WRAPPER_PATH, os.X_OK):
        msgs.append("renewal wrapper executable")
    else:
        msgs.append("renewal wrapper missing or not executable")
        ok = False

    # b) /etc/hosts entry
    line = f"127.0.0.1 {domain}"
    rc, _ = _run_priv("grep", "-Fx", line, "/etc/hosts")
    if rc == 0:
        msgs.append(f"/etc/hosts contains {domain}")
    else:
        msgs.append(f"/etc/hosts missing {domain}")
        ok = False

    # c) APR listener must be removed
    xml = read_server_xml()
    if re.search(
        r'<\s*Listener\s+className="org\.apache\.catalina\.core\.AprLifecycleListener"',
        xml,
    ):
        msgs.append("AprLifecycleListener still present in server.xml")
        ok = False
    else:
        msgs.append("APR listener removed")

    # d) connector present + 8080 untouched
    if connector_exists(xml, domain):
        msgs.append("TLS connector present in server.xml")
    else:
        msgs.append("TLS connector missing in server.xml")
        ok = False
    if verify_8080_untouched(xml):
        msgs.append("8080 connector unchanged")
    else:
        msgs.append("8080 connector missing or altered")
        ok = False

    # d) HTTPS 443 valid
    https_ok, https_msg = verify_https(domain)
    msgs.append(https_msg)
    ok &= https_ok

    # e) HTTP 8080 still serves MDM
    http_ok, http_msg = verify_http_8080()
    msgs.append(http_msg)
    ok &= http_ok

    # f) cert valid
    cert_ok, cert_msg = verify_cert_valid(domain)
    msgs.append(cert_msg)
    ok &= cert_ok

    # g) lego --path state durable
    rc_certs, _ = _run_priv("test", "-d", str(LEGO_PATH / "certificates"))
    rc_accts, _ = _run_priv("test", "-d", str(LEGO_PATH / "accounts"))
    if rc_certs == 0 and rc_accts == 0:
        msgs.append("lego --path state present")
    else:
        msgs.append("lego --path state missing or empty")
        ok = False

    return ok, msgs


# ── main ─────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    if {"-h", "--help"} & set(argv[1:]):
        print("Usage: python3 lib/64-mdm-tls.py [--verify]")
        return 0

    if not require_user("lib/64-mdm-tls.py", "python3 lib/64-mdm-tls.py"):
        return 1

    if is_vm():
        log_step("64-mdm-tls: running in VM, skipping")
        _result("SKIP", False, "in VM")
        return 2

    try:
        domain = load_domain()
        email = load_acme_email()

        if "--verify" in argv[1:]:
            if _run_priv("true")[0] != 0:
                log_error(
                    "no cached sudo ticket — run `sudo -v` (or ./install.py --only 64) "
                    "in your terminal, then retry --verify"
                )
                _result("FAIL", False, "sudo unavailable for --verify")
                return 1
            ok, msgs = converged(domain)
            for m in msgs:
                log(f"  {m}")
            return 0 if ok else 3

        verify_prereqs()
        ensure_secrets_exist()

        # Preflight: ROOT.xml must have https base.url before we enable the connector
        log_step("checking ROOT.xml base.url scheme")
        try:
            assert_root_xml_https(domain)
            log_ok(f"ROOT.xml base.url contains https://{domain}")
        except RuntimeError as e:
            log_error(str(e))
            log_error(
                "Owner must run the manual installer with HTTPS answers first, or edit ROOT.xml"
            )
            _result("FAIL", False, "ROOT.xml base.url not https")
            return 1

        # Idempotent system setup
        changed = False
        changed |= ensure_hosts_entry(domain)
        changed |= ensure_lego_path()
        changed |= render_renewal_wrapper(domain, email)
        changed |= render_renewal_unit()
        changed |= enable_renewal_unit()
        changed |= ensure_ssl_dir()

        # Token present?
        token = load_duckdns_token()
        if not token:
            log_warn(
                "DUCKDNS_TOKEN not in ~/.config/mdm/secrets.env — owner must add it"
            )
            log_warn("Then re-run: python3 lib/64-mdm-tls.py")
            _result("PARTIAL", changed, "DUCKDNS_TOKEN missing — owner action required")
            return 3

        # Issuance / renewal must PRECEDE connector insert: tomcat fails to
        # start on a TLS connector whose PEM files do not exist yet.
        run_renewal(domain)
        changed |= place_certs(domain)
        if changed:
            log_step("restarting tomcat10 after cert placement")
            rc, out = _run_priv("systemctl", "restart", "tomcat10")
            if rc != 0:
                raise RuntimeError(f"tomcat10 restart failed: {out.strip()}")
            wait_for_panel()

        # server.xml invariants (Debian stock baseline):
        #  1. No AprLifecycleListener — it loads tcnative 1.3.1 unconditionally,
        #     below Tomcat's 1.3.4 floor -> JVM SIGSEGV at TLS init.
        #  2. JSSE-pinned TLS connector on 443, direct PEM, inserted when absent.
        # Converge by whole-file diff: one backup + one write + one restart no
        # matter how many of the two invariants are missing.
        orig_xml = xml = read_server_xml()
        listener_re = re.compile(
            r'<\s*Listener\s+className="org\.apache\.catalina\.core\.AprLifecycleListener"[^>]*/>'
        )
        listener_comment = (
            "<!-- APR listener disabled by mdm-tls: tcnative 1.3.1 is below "
            "Tomcat's 1.3.4 floor and crashes the JVM at TLS init; JSSE needs no APR -->"
        )
        xml, n_subs = listener_re.subn(listener_comment, xml)
        if n_subs:
            log_step("removing AprLifecycleListener from server.xml")
        if not connector_exists(xml, domain):
            log_step("inserting TLS connector into server.xml")
            xml = insert_tls_connector(xml, domain)
        if xml == orig_xml:
            log_ok("server.xml already converged")
            xml = None

        if xml is not None:
            if not verify_8080_untouched(xml):
                raise RuntimeError("8080 connector lost after TLS edit -- aborting")
            write_server_xml(xml)
            changed = True
            log_step("restarting tomcat10 for connector change")
            rc, out = _run_priv("systemctl", "restart", "tomcat10")
            if rc != 0:
                raise RuntimeError(f"tomcat10 restart failed: {out.strip()}")
            wait_for_panel()

        # Final convergence check
        ok, msgs = converged(domain)
        for m in msgs:
            log(f"  {m}")
        if ok:
            _result("OK", True, "mdm-tls converged")
            return 0
        else:
            log_warn("convergence check failed after issuance")
            _result("FAIL", False, "post-issuance verification failed")
            return 1

    except Exception as exc:
        log_error(f"64-mdm-tls: {exc}")
        _result("FAIL", False, str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
