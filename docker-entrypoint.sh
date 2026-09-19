#!/bin/sh
# Tambahkan route ke subnet NAS dan VPN via gateway Docker host
ip route add 10.0.0.0/8 via 100.204.0.1 2>/dev/null || true
ip route add 172.0.0.0/16 via 100.204.0.1 2>/dev/null || true
ip route add 172.16.0.0/12 via 100.204.0.1 2>/dev/null || true
ip route add 192.168.0.0/16 via 100.204.0.1 2>/dev/null || true
ip route add 203.0.16.0/24 via 100.204.0.1 2>/dev/null || true

echo "[entrypoint] VPN & NAS routes configured"

exec gunicorn --config gunicorn.conf.py app:app
# Note: iptables MASQUERADE untuk container ke NAS dikelola di host
