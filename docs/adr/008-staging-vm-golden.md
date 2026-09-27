# Test every change in a disposable full-replica VM

- Status: Accepted (2026-09-28) · Source:
  `plans/completed/staging-vm.md`

## Decision

Every repo change is tested in a disposable QEMU/KVM full replica
before touching the live machine. Base: mmdebstrap trixie golden plus
qcow2 overlays, libvirt/virsh harness, SPICE/VirGL display. Iteration
rides read-only virtiofs; the release gate is a git clone plus manual
provisioning inside the VM, so orchestrator internals stay invisible
to the harness.

## Why

Ark's distinguishing features (nftables, dnsmasq, netns, chattr
lockdown, sudo-group drops) are kernel-level — only a separate kernel
exercises them, which rules out containers and shared-kernel tools.
The golden mirrors a bare-metal netinstall; overlays keep chains to
one checkpoint plus one active disk.
`Rejected: Packer — HCL plus slow builds, only if golden needs
CI-ifying; virt-builder — cannot reproduce the exact package set;
cloud-init — server-only; Vagrant — provider churn; virt-manager —
virsh plus viewer instead; internal snapshots — deprecated; NixOS
driver, distrobox/nspawn — wrong thing or shared kernel; bridged
networking — libvirt NAT instead.`
`Revisit only if: the golden-image build itself needs CI-ifying
(Packer) or UKI migration lands (see staging-vm-uki.md).`

## Consequences

- Good: kernel-level lockdown paths get exercised before they can lock
  out the live machine; the harness survives orchestrator migrations.
- Bad (accepted cost): a full second system to build and maintain;
  VFIO/NVIDIA validation still needs a spare GPU.
