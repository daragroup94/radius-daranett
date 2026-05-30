#!/bin/sh
# Tambahkan route ke subnet NAS via gateway Docker host
ip route add 10.100.0.0/24 via 10.204.0.1 2>/dev/null || true
ip route add 10.20.0.0/24 via 10.204.0.1 2>/dev/null || true

exec gunicorn --config gunicorn.conf.py app:app
# Note: iptables MASQUERADE untuk container ke NAS dikelola di host
