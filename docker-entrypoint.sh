#!/bin/sh
# Tambahkan route ke subnet NAS dan VPN via gateway Docker host
ip route add 10.100.0.0/24 via 100.204.0.1 2>/dev/null || true
ip route add 10.20.0.0/24 via 100.204.0.1 2>/dev/null || true
ip route add 10.200.0.0/24 via 100.204.0.1 2>/dev/null || true
ip route add 10.50.0.0/24 via 100.204.0.1 2>/dev/null || true  
ip route add 10.30.0.0/24 via 100.204.0.1 2>/dev/null || true
ip route add 172.0.0.0/16 via 100.204.0.1 2>/dev/null || true
ip route add 10.206.0.0/24 via 100.204.0.1 2>/dev/null || true

echo "[entrypoint] VPN routes configured"

exec gunicorn --config gunicorn.conf.py app:app
# Note: iptables MASQUERADE untuk container ke NAS dikelola di host
