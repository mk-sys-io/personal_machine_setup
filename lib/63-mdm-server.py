#!/usr/bin/env python3
"""MDM server scaffold + prerequisites for Headwind MDM v7.

Stages the pinned installer bundle, provisions PostgreSQL role/database,
verifies the storage directory, and hardens ROOT.xml. The web-panel
install itself is a documented manual step (interactive installer with a
destructive re-run branch); the module prints the answer sheet.

Run standalone: python3 lib/63-mdm-server.py
Wired into install.py as 63-mdm-server

Exit codes:
    0  converged
    1  failure
    2  skip (in VM)
    3  partial (prereqs done, manual install pending)
Stdlib only.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))  # for preconditions (ADR-011)

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
BUNDLE_URL = "https://h-mdm.com/files/hmdm-7.04-install-ubuntu.zip"
BUNDLE_SHA256 = "faf4eb53684f6750d7025e22bc5a89fcf9cb9d98815f4c8ad4aa9a8854f70295"
BUNDLE_SIZE = 61_891_881
WAR_NAME = "hmdm-7.04.1-os.war"

CACHE_DIR = Path.home() / ".cache" / "mdm"
BUNDLE_DIR = CACHE_DIR / "hmdm-install"
SECRETS = Path.home() / ".config" / "mdm" / "secrets.env"

TOMCAT_BASE = Path("/var/lib/tomcat10")
STORAGE = (
    TOMCAT_BASE / "work"
)  # Headwind default; symlink → /var/cache/tomcat10, package-owned
ROOT_XML = Path("/etc/tomcat10/Catalina/localhost/ROOT.xml")
REQUIRED_PKGS = ("tomcat10", "postgresql", "aapt")
PG_PORT_CONF = "10-mdm-port.conf"
PASS_SALT = "5YdSYHyg2U"
SEED_MD5 = "21232F297A57A5A743894A0E4A801FC3"  # md5("admin"), uppercased

INSTALLER_PROMPTS = (
    "PostgreSQL port",
    "Headwind MDM storage directory",
    "Headwind MDM scripts directory",
    "Project path on server",
    "Setup HTTPS via LetsEncrypt",
)


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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha1_string(value: str) -> str:
    # mirrors PasswordUtil.getHashFromMd5 -> CryptoUtil.getSHA1String
    return hashlib.sha1(value.encode()).hexdigest().upper()


def _port_holder(port: int) -> str:
    """One-line `ss` description of who holds a TCP port (for error messages)."""
    p = subprocess.run(
        ["ss", "-ltnp", f"sport = :{port}"], capture_output=True, text=True, check=False
    )
    rows = [line.strip() for line in (p.stdout or "").splitlines()[1:] if line.strip()]
    return rows[0] if rows else "no listener"


def _pg_probe(port: int, db_pw: str) -> tuple[bool, str]:
    """Connect as hmdm and prove we reached the intended database."""
    env = {**os.environ, "PGPASSWORD": db_pw}
    p = subprocess.run(
        [
            "psql",
            "-h",
            "127.0.0.1",
            "-p",
            str(port),
            "-U",
            "hmdm",
            "-d",
            "hmdm",
            "-tAc",
            "select current_database(), current_user, inet_server_port()",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if p.returncode != 0:
        return False, (p.stderr or "").strip() or f"psql rc={p.returncode}"
    got = tuple(part.strip() for part in (p.stdout or "").split("|"))
    want = ("hmdm", "hmdm", str(port))
    if got != want:
        return False, f"wrong server: got {got}, expected {want}"
    return True, ""


# ── load domain from config.txt ──────────────────────────────────────────────
def load_domain() -> str:
    sys.path.insert(0, str(REPO_ROOT))
    from install import load_env  # noqa: E402

    d = load_env().get("MDM_DOMAIN", "").strip().lower()
    if not d:
        raise RuntimeError("MDM_DOMAIN not set in config.txt")
    if not re.match(r"^[a-z0-9.-]+\.duckdns\.org$", d):
        raise RuntimeError(f"MDM_DOMAIN must be a duckdns.org subdomain, got: {d}")
    return d


# ── secrets file ─────────────────────────────────────────────────────────────
def ensure_secrets() -> dict[str, str]:
    if SECRETS.is_file():
        vals = {}
        for line in SECRETS.read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                vals[k.strip()] = v.strip()
        if "MDM_DB_PASSWORD" in vals:
            return vals
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    pw = secrets.token_urlsafe(24)
    SECRETS.write_text(f"MDM_DB_PASSWORD={pw}\n")
    SECRETS.chmod(0o600)
    return {"MDM_DB_PASSWORD": pw}


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


# ── postgresql port discovery & reconciliation ───────────────────────────────
def load_pg_port() -> int:
    """Declared native-cluster port (config.txt is the single source)."""
    sys.path.insert(0, str(REPO_ROOT))
    from install import load_env  # noqa: E402

    raw = load_env().get("PG_NATIVE_PORT", "").strip()
    if not raw.isdigit():
        raise RuntimeError(
            f"PG_NATIVE_PORT must be an integer in config.txt, got {raw!r}"
        )
    port = int(raw)
    if not 1 <= port <= 65535:
        raise RuntimeError(f"PG_NATIVE_PORT out of range: {port}")
    return port


def _clusters() -> list[dict[str, str]]:
    p = subprocess.run(
        ["pg_lsclusters", "-h"], capture_output=True, text=True, check=False
    )
    if p.returncode != 0:
        raise RuntimeError(f"pg_lsclusters failed: {(p.stderr or '').strip()}")
    rows = []
    for line in (p.stdout or "").splitlines():
        f = line.split()
        if len(f) >= 7:
            rows.append({"ver": f[0], "name": f[1], "port": f[2], "status": f[3]})
    return rows


def _published_ports() -> set[int]:
    """Host-side publish ports declared by container units in this repo."""
    ports: set[int] = set()
    for unit in sorted((REPO_ROOT / "scout" / "deploy").glob("*.container")):
        for line in unit.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*PublishPort=\s*(?:[^:\s]*:)?(\d+):(\d+)", line)
            if m:
                ports.add(int(m.group(1)))
    return ports


def _server_port() -> int | None:
    """Port the running cluster reports (authoritative; None if unreachable)."""
    rc, out = _run_priv("-u", "postgres", "psql", "-tAc", "show port")
    val = out.strip()
    return int(val) if rc == 0 and val.isdigit() else None


def _wait_ready(port: int) -> None:
    for _ in range(30):
        rc, _ = _run_priv("pg_isready", "-h", "127.0.0.1", "-p", str(port))
        if rc == 0:
            return
        time.sleep(1)
    raise RuntimeError(
        f"postgresql did not become ready on 127.0.0.1:{port} (holder: {_port_holder(port)})"
    )


def ensure_cluster_port(desired: int) -> int:
    """Reconcile the native cluster's TCP port with PG_NATIVE_PORT."""
    rows = _clusters()
    if len(rows) != 1:
        raise RuntimeError(
            f"expected exactly one postgresql cluster, found {len(rows)}: {rows}"
        )
    ver, name, status = rows[0]["ver"], rows[0]["name"], rows[0]["status"]

    if desired in _published_ports():
        raise RuntimeError(
            f"PG_NATIVE_PORT={desired} collides with a PublishPort in "
            "scout/deploy/*.container — choose another port"
        )

    conf = Path(f"/etc/postgresql/{ver}/{name}/conf.d/{PG_PORT_CONF}")
    want = (
        "# managed by lib/63-mdm-server.py — PG_NATIVE_PORT from config.txt\n"
        f"port = {desired}\n"
    )
    rc, out = _run_priv("cat", str(conf))
    if rc != 0 or out != want:
        log_step(f"declaring postgresql port {desired} in {conf}")
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            tf.write(want)
            tmp = Path(tf.name)
        try:
            rc, out = _run_priv(
                "install",
                "-D",
                "-m",
                "0644",
                "-o",
                "postgres",
                "-g",
                "postgres",
                str(tmp),
                str(conf),
            )
            if rc != 0:
                raise RuntimeError(f"writing {conf} failed: {out.strip()}")
        finally:
            tmp.unlink(missing_ok=True)

    if status != "online":
        log_step(f"starting postgresql cluster {ver}/{name}")
        rc, out = _run_priv("pg_ctlcluster", ver, name, "start")
        if rc != 0:
            raise RuntimeError(f"pg_ctlcluster start failed: {out.strip()}")
        _wait_ready(desired)

    live = _server_port()
    if live is None:
        raise RuntimeError(
            "cannot read the running cluster port (psql 'show port' failed)"
        )
    if live != desired:
        log_step(f"restarting cluster {ver}/{name}: live port {live} → {desired}")
        rc, out = _run_priv("pg_ctlcluster", ver, name, "restart")
        if rc != 0:
            raise RuntimeError(f"pg_ctlcluster restart failed: {out.strip()}")
        _wait_ready(desired)
        live = _server_port()

    if live != desired:
        raise RuntimeError(
            f"cluster reports port {live}, declared {desired} — check {conf} and "
            f"/etc/postgresql/{ver}/{name}/postgresql.conf"
        )
    return desired


def ensure_postgres(port: int) -> None:
    rc, _ = _run_priv("pg_isready", "-h", "127.0.0.1", "-p", str(port))
    if rc == 0:
        return
    log_step("starting postgresql")
    _run_priv("systemctl", "start", "postgresql")
    _wait_ready(port)


def ensure_db(port: int, db_pw: str) -> None:
    # role
    rc, out = _run_priv(
        "-u", "postgres", "psql", "-tAc", "SELECT 1 FROM pg_roles WHERE rolname='hmdm'"
    )
    if "1" not in out:
        log_step("creating postgresql role hmdm")
        esc = db_pw.replace("'", "''")
        rc, out = _run_priv(
            "-u", "postgres", "psql", "-c", f"CREATE ROLE hmdm LOGIN PASSWORD '{esc}';"
        )
        if rc != 0:
            raise RuntimeError(f"create role failed: {out.strip()}")

    # database
    rc, out = _run_priv(
        "-u",
        "postgres",
        "psql",
        "-tAc",
        "SELECT 1 FROM pg_database WHERE datname='hmdm'",
    )
    if "1" not in out:
        log_step("creating postgresql database hmdm")
        rc, out = _run_priv(
            "-u", "postgres", "psql", "-c", "CREATE DATABASE hmdm OWNER hmdm;"
        )
        if rc != 0:
            raise RuntimeError(f"create database failed: {out.strip()}")

    ok, err = _pg_probe(port, db_pw)
    if not ok:
        raise RuntimeError(
            f"postgresql identity check failed on 127.0.0.1:{port}: {err} — "
            f"holder: {_port_holder(port)}"
        )


# ── storage directory (verify-only: package-owned) ───────────────────────────
def ensure_storage() -> None:
    rc, out = _run_priv(
        "stat", "-L", "-c", "%U:%G:%a", str(STORAGE)
    )  # -L: follow the work → /var/cache/tomcat10 symlink
    if rc != 0 or out.strip() != "tomcat:tomcat:750":
        raise RuntimeError(
            f"storage dir {STORAGE} is not tomcat:tomcat 750 (got: "
            f"{out.strip() or 'missing'}) — check the tomcat10 package"
        )


# ── bundle staging ───────────────────────────────────────────────────────────
def ensure_bundle() -> bool:
    zip_path = CACHE_DIR / Path(BUNDLE_URL).name
    need_dl = True
    if zip_path.is_file():
        sz = zip_path.stat().st_size
        if sz == BUNDLE_SIZE:
            sha = _sha256_file(zip_path)
            if sha == BUNDLE_SHA256:
                need_dl = False
    if need_dl:
        log_step("downloading installer bundle")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = zip_path.with_suffix(".part")
        try:
            with (
                urllib.request.urlopen(BUNDLE_URL, timeout=120) as resp,
                tmp.open("wb") as f,
            ):
                for chunk in iter(lambda: resp.read(65536), b""):
                    f.write(chunk)
            if tmp.stat().st_size != BUNDLE_SIZE:
                raise RuntimeError(f"download size mismatch: {tmp.stat().st_size}")
            if _sha256_file(tmp) != BUNDLE_SHA256:
                raise RuntimeError("download sha256 mismatch")
            tmp.replace(zip_path)
        finally:
            tmp.unlink(missing_ok=True)

    changed = need_dl
    if not (BUNDLE_DIR / "hmdm_install.sh").is_file():
        log_step("extracting installer bundle")
        import zipfile

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(CACHE_DIR)
        changed = True

    script_path = BUNDLE_DIR / "hmdm_install.sh"
    if not script_path.is_file():
        raise RuntimeError(f"installer script missing: {script_path}")
    if not (BUNDLE_DIR / WAR_NAME).is_file():
        raise RuntimeError(f"WAR not found in bundle: {BUNDLE_DIR / WAR_NAME}")

    text = script_path.read_text(encoding="utf-8", errors="replace")
    missing = [p for p in INSTALLER_PROMPTS if p not in text]
    if missing:
        raise RuntimeError(
            f"installer prompt drift: {missing} not found in {script_path} — "
            "re-verify the answer sheet before running the installer"
        )
    return changed


# ── harden ROOT.xml ──────────────────────────────────────────────────────────
def harden_root_xml() -> bool:
    rc, out = _run_priv("stat", "-c", "%a %U:%G", str(ROOT_XML))
    if rc == 0 and out.strip() == "640 root:tomcat":
        return False
    log_step("hardening ROOT.xml to 640 root:tomcat")
    _run_priv("chown", "root:tomcat", str(ROOT_XML))
    _run_priv("chmod", "640", str(ROOT_XML))
    return True


# ── convergence check ────────────────────────────────────────────────────────
def converged(port: int, db_pw: str, domain: str) -> tuple[bool, list[str]]:
    msgs: list[str] = []
    ok = True

    # 1. ROOT.xml points at this cluster and port
    rc, out = _run_priv("cat", str(ROOT_XML))
    want_url = f"jdbc:postgresql://localhost:{port}/hmdm"
    if rc != 0:
        msgs.append(f"ROOT.xml missing ({ROOT_XML})")
        ok = False
    elif want_url not in out:
        found = re.search(r"jdbc:postgresql://[^\"]+", out)
        msgs.append(
            f"ROOT.xml jdbc url is not {want_url} "
            f"(found {found.group(0) if found else 'nothing'})"
        )
        ok = False
    else:
        msgs.append(f"ROOT.xml points at {want_url}")

    # 2. JDBC.password matches the generated secret
    if ok:
        m = re.search(r'JDBC\.password["\s]*value="([^"]*)"', out)
        if not m or m.group(1) != db_pw:
            msgs.append("JDBC.password mismatch")
            ok = False
        else:
            msgs.append("JDBC.password matches generated")

    # 3. base.url contains the domain
    if ok:
        if f"://{domain}" not in out:
            msgs.append(f"base.url missing domain {domain}")
            ok = False
        else:
            msgs.append(f"base.url contains {domain}")

    env = {**os.environ, "PGPASSWORD": db_pw}
    base = [
        "psql",
        "-h",
        "127.0.0.1",
        "-p",
        str(port),
        "-U",
        "hmdm",
        "-d",
        "hmdm",
        "-tAc",
    ]

    # 4. users table populated
    if ok:
        res = subprocess.run(
            [*base, "SELECT count(*) FROM users"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        n = res.stdout.strip()
        if res.returncode != 0 or not n.isdigit() or int(n) == 0:
            msgs.append(
                f"users table empty ({(res.stderr or '').strip() or 'rc=' + str(res.returncode)})"
            )
            ok = False
        else:
            msgs.append(f"users populated ({n} rows)")

    # 5. admin password is no longer the seeded 'admin'
    if ok:
        seed_hash = _sha1_string(SEED_MD5 + PASS_SALT)
        res = subprocess.run(
            [*base, "SELECT password FROM users WHERE login='admin'"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        got = res.stdout.strip()
        if res.returncode != 0:
            msgs.append(f"admin password query failed: {(res.stderr or '').strip()}")
            ok = False
        elif not got:
            msgs.append("no admin row in users")
            ok = False
        elif got.upper() in (seed_hash, SEED_MD5):
            msgs.append(
                "admin password is still the seeded 'admin' (change it in the web UI)"
            )
            ok = False
        else:
            msgs.append("admin password differs from seed")

    # 6. the MDM web panel answers on 8080 (not Debian's stock Tomcat page)
    if ok:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/", timeout=5) as resp:
                code = resp.getcode()
                body = resp.read(65536).decode("utf-8", "replace")
            if code not in (200, 302):
                msgs.append(f"http 8080 returned {code} (holder: {_port_holder(8080)})")
                ok = False
            elif "MDM web panel" not in body:
                msgs.append(
                    "http 8080 serves the stock Tomcat page — ROOT.war not deployed"
                )
                ok = False
            else:
                msgs.append(f"http 8080 serves the MDM web panel ({code})")
        except Exception as e:
            msgs.append(f"http 8080 failed: {e} (holder: {_port_holder(8080)})")
            ok = False

    return ok, msgs


# ── print answer sheet ───────────────────────────────────────────────────────
def print_answer_sheet(domain: str, port: int) -> None:
    rows = [
        ("Please choose the installation language (en/ru) [en]:", "<Enter>"),
        ("PostgreSQL host [localhost]:", "<Enter>"),
        ("PostgreSQL port [5432]:", str(port)),
        ("PostgreSQL database [hmdm]:", "<Enter>"),
        ("PostgreSQL user [hmdm]:", "<Enter>"),
        (
            "PostgreSQL password:",
            "value of MDM_DB_PASSWORD in ~/.config/mdm/secrets.env",
        ),
        (
            "Headwind MDM storage directory [/var/lib/tomcat10/work]:",
            "<Enter>",
        ),
        ("Headwind MDM scripts directory [/opt/hmdm]:", "<Enter>"),
        ("Protocol (http|https) [https]:", "<Enter>"),
        ("Domain name or public IP (e.g. example.com):", domain),
        (
            "Port (e.g. 8080, leave empty for default ports 80 or 443):",
            "<Enter> (empty)",
        ),
        ("Project path on server (e.g. /hmdm) or ROOT:", "<Enter> (ROOT)"),
        ("Setup SMTP credentials [Y/n]?:", "n"),
        ("Is this information correct [Y/n]?:", "Y"),
        ("Setup HTTPS via LetsEncrypt [Y/n]?:", "n   (B2 owns TLS)"),
        (
            "Use iptables to redirect port 443 to 8443 [Y/n]?:",
            "n   (B2 binds 443 directly)",
        ),
        ("Move required APKs from h-mdm.com to your server [Y/n]?:", "y"),
    ]
    width = max(len(q) for q, _ in rows) + 2
    print("\n" + "=" * 80)
    print("Manual install (one time):")
    print(f"  cd {BUNDLE_DIR}")
    print("  sudo ./hmdm_install.sh")
    print("\nAnswers (<Enter> = accept the default shown in brackets):")
    for q, a in rows:
        print(f"  {q:<{width}}{a}")
    print("\nThen change the admin password in the web UI (admin:admin), and re-run:")
    print("  ./install.py --only 63")
    print("=" * 80 + "\n")


# ── main ─────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    if {"-h", "--help"} & set(argv[1:]):
        print("Usage: python3 lib/63-mdm-server.py [--answers|--verify]")
        return 0

    if not require_user("lib/63-mdm-server.py", "python3 lib/63-mdm-server.py"):
        return 1

    if is_vm():
        log_step("63-mdm-server: running in VM, skipping")
        _result("SKIP", False, "in VM")
        return 2

    try:
        domain = load_domain()
        db_pw = ensure_secrets()["MDM_DB_PASSWORD"]
        desired = load_pg_port()

        if "--answers" in argv[1:]:
            print_answer_sheet(domain, desired)
            return 0

        if "--verify" in argv[1:]:
            if _run_priv("true")[0] != 0:
                log_error(
                    "no cached sudo ticket — run `sudo -v` (or ./install.py --only 63) "
                    "in your terminal, then retry --verify"
                )
                _result("FAIL", False, "sudo unavailable for --verify")
                return 1
            live = _server_port()
            msgs = [f"declared PG_NATIVE_PORT={desired}", f"cluster reports {live}"]
            ok = live == desired
            if ok and live is not None:
                ok, more = converged(live, db_pw, domain)
                msgs += more
            for m in msgs:
                log(f"  {m}")
            return 0 if ok else 3

        verify_prereqs()

        port = ensure_cluster_port(desired)
        log_step(f"63-mdm-server (postgresql 127.0.0.1:{port})")
        changed = False

        ensure_postgres(port)
        ensure_storage()
        ensure_db(port, db_pw)
        changed |= ensure_bundle()

        ok, msgs = converged(port, db_pw, domain)
        for m in msgs:
            log(f"  {m}")
        if ok:
            changed |= harden_root_xml()
            _result("OK", changed, "mdm-server converged")
            return 0

        log_warn("not converged — manual install required")
        print_answer_sheet(domain, port)
        _result("PARTIAL", changed, "manual install pending")
        return 3

    except Exception as exc:
        log_error(f"63-mdm-server: {exc}")
        _result("FAIL", False, str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
