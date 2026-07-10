#!/bin/bash
# setup-internet-netns.sh — create the internet network namespace
# Idempotent: safe to run multiple times.
set -euo pipefail

NETNS="internet-netns"
VETH_HOST="veth-inet-host"
VETH_NS="veth-inet-ns"
HOST_IP="@NETNS_HOST@/30"
NS_IP="@NETNS_CLIENT@/30"
SUBNET="@NETNS_SUBNET@"

# Remove stale namespace/veth (e.g. from unclean shutdown)
if ip netns list | grep -q "^$NETNS\$"; then
    if ! ip link show "$VETH_HOST" &>/dev/null; then
        echo "Stale namespace $NETNS (no veth) — removing"
        ip netns del "$NETNS"
    fi
fi
if ip link show "$VETH_HOST" &>/dev/null; then
    if ! ip netns list | grep -q "^$NETNS\$"; then
        echo "Stale veth $VETH_HOST (no namespace) — removing"
        ip link del "$VETH_HOST"
    fi
fi

# Create namespace
if ! ip netns list | grep -q "^$NETNS\$"; then
    ip netns add "$NETNS"
    echo "Created namespace $NETNS"
else
    echo "Namespace $NETNS already exists"
fi

# Create veth pair
if ! ip link show "$VETH_HOST" &>/dev/null; then
    ip link add "$VETH_HOST" type veth peer name "$VETH_NS"
    echo "Created veth pair $VETH_HOST <-> $VETH_NS"
else
    echo "Veth pair already exists"
fi

# Move ns end into namespace (idempotent — may already be inside)
ip link set "$VETH_NS" netns "$NETNS" 2>/dev/null || true

# Assign IPs
ip addr add "$HOST_IP" dev "$VETH_HOST" 2>/dev/null || true
ip netns exec "$NETNS" ip addr add "$NS_IP" dev "$VETH_NS" 2>/dev/null || true

# Bring links up
ip link set "$VETH_HOST" up
ip netns exec "$NETNS" ip link set "$VETH_NS" up
ip netns exec "$NETNS" ip link set lo up

# Add default route inside namespace (via host)
ip netns exec "$NETNS" ip route del default 2>/dev/null || true
ip netns exec "$NETNS" ip route add default via "$(echo $HOST_IP | cut -d/ -f1)"

# Verify
if ip netns exec "$NETNS" ip route get @DNS_PRIMARY@ | grep -q "$VETH_NS"; then
    echo "internet-netns: routing OK ($SUBNET)"
else
    echo "ERROR: internet-netns routing check failed" >&2
    exit 1
fi
