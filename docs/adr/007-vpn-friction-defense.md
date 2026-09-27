# VPN defense is friction, not a wall

- Status: Accepted (2026-09-28) · Source:
  `plans/completed/vpn-usage-mitigation.md`

## Decision

VPN defense is six friction layers, not a completeness claim: DNS
blocklist, browser policy, enable-time artifact plus package-drift
gate, tun/tap/wg interface block, sudo removal, VPN sites in the
blocklist. Each layer makes casual use impractical; a determined user
bypasses any single control, and that is accepted.

## Why

Detection without enforcement is noise in a self-control system — the
user already knows the tunnel is up — so Layer 3 blocks tunnel traffic
instead of logging it, and Layer 2 fail-closes `ark enable` on VPN
artifacts or dpkg drift.
`Rejected: timer-based interface detection — noise, no enforcement;
detection-only logging — passive; egress allowlisting — closes the
ssh -D residual but disproportionate; ELF-header scanning —
disproportionate, premeditation threshold accepted; banning ssh —
essential for dev; website-blocking-as-primary — theater, kept only
as L5 defense-in-depth.`
`Revisit only if: none named — Known Gaps are accepted residuals
(ssh -D, raw-IP curl|sh, renamed binaries, Firefox downloads), not
triggers.`

## Consequences

- Good: impulsive bypass dies at enable time or on arrival; residuals
  require deliberate premeditation plus evidence in `ark status`.
- Bad (accepted cost): no wall exists — raw-IP installs and userspace
  proxies stay open by design; download blocking needs Chrome.
