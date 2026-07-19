# Known Issues

---

## 1. opencode API timeout in internet namespace

**Status**: Open

**Symptom**: After a period of app inactivity or Sway reload, opencode API
requests fail indefinitely.

**Cause**: Namespace DNS uses a single external resolver (`1.1.1.1`) with no
fallback. Host dnsmasq is unreachable from inside the namespace (`127.0.0.1`
loopback isolation). The external DNS path through veth+masquerade degrades
over time with no failover.

**Why it can't be fixed**: Namespace must provide unrestricted DNS — routing
through dnsmasq would apply the locked-mode allowlist, defeating its purpose.

**Workaround**: Relaunch opencode.
