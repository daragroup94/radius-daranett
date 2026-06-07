#!/bin/bash
set -e

echo "Starting IKEv2 VPN Server Setup..."

# Setup IP forwarding
echo "Enabling IP Forwarding..."
echo 1 > /proc/sys/net/ipv4/ip_forward || true

# Setup iptables for NAT
echo "Configuring NAT for VPN subnet 10.10.10.0/24..."
iptables -t nat -C POSTROUTING -s 10.10.10.0/24 -o eth0 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s 10.10.10.0/24 -o eth0 -j MASQUERADE
iptables -C FORWARD -s 10.10.10.0/24 -j ACCEPT 2>/dev/null || iptables -A FORWARD -s 10.10.10.0/24 -j ACCEPT

# Prepare certificate directories
mkdir -p /etc/ipsec.d/private /etc/ipsec.d/certs /etc/ipsec.d/cacerts

# Get Public IP (Needed for config even if certs exist)
echo "Getting Public IP..."
PUBLIC_IP=${VPN_DOMAIN_OR_IP:-$(curl -s ifconfig.me)}
echo "Public IP / Domain: $PUBLIC_IP"

# Generate Certificates if they don't exist
if [ ! -f /etc/ipsec.d/cacerts/caCert.pem ]; then
    echo "Generating new Certificates..."
    
    echo "1. Generating CA Key & Certificate..."
    pki --gen --type rsa --size 4096 --outform pem > /etc/ipsec.d/private/caKey.pem
    pki --self --ca --lifetime 3650 --in /etc/ipsec.d/private/caKey.pem --type rsa --dn "C=ID, O=RadiusVPN, CN=RadiusVPN Root CA" --outform pem > /etc/ipsec.d/cacerts/caCert.pem
    
    echo "2. Generating Server Key & Certificate for $PUBLIC_IP..."
    pki --gen --type rsa --size 4096 --outform pem > /etc/ipsec.d/private/serverKey.pem
    pki --pub --in /etc/ipsec.d/private/serverKey.pem --type rsa | pki --issue --lifetime 1825 --cacert /etc/ipsec.d/cacerts/caCert.pem --cakey /etc/ipsec.d/private/caKey.pem --dn "C=ID, O=RadiusVPN, CN=$PUBLIC_IP" --san "$PUBLIC_IP" --san "@$PUBLIC_IP" --flag serverAuth --flag ikeIntermediate --outform pem > /etc/ipsec.d/certs/serverCert.pem
    
    echo "Certificates generated successfully!"
    
    # Copy CA cert to uploads volume for Dashboard to serve
    if [ -d "/app-certs" ]; then
        echo "Copying CA Certificate to Dashboard uploads folder..."
        cp /etc/ipsec.d/cacerts/caCert.pem /app-certs/RadiusVPN-CA.pem
        cp /etc/ipsec.d/cacerts/caCert.pem /app-certs/RadiusVPN-CA.crt
        chmod 644 /app-certs/RadiusVPN-CA.pem /app-certs/RadiusVPN-CA.crt
    fi
fi

# Write ipsec.secrets
echo "Writing ipsec.secrets..."
cat > /etc/ipsec.secrets <<EOF
: RSA serverKey.pem
EOF

# Write ipsec.conf
echo "Writing ipsec.conf..."
cat > /etc/ipsec.conf <<EOF
config setup
    charondebug="ike 2, knl 1, cfg 2"
    uniqueids=never

conn ikev2-vpn
    auto=add
    compress=no
    type=tunnel
    keyexchange=ikev2
    fragmentation=yes
    forceencaps=yes
    dpdaction=clear
    dpddelay=300s
    rekey=no
    left=%any
    leftid=$PUBLIC_IP
    leftcert=serverCert.pem
    leftsendcert=always
    leftsubnet=0.0.0.0/0
    right=%any
    rightid=%any
    rightauth=eap-radius
    rightsourceip=%radius
    rightdns=8.8.8.8,1.1.1.1
    rightsendcert=never
    eap_identity=%identity
EOF

# Write strongswan.conf to connect to FreeRADIUS
echo "Writing strongswan.conf (EAP-RADIUS plugin)..."
cat > /etc/strongswan.conf <<EOF
charon {
    load_modular = yes
    plugins {
        include strongswan.d/charon/*.conf
        eap-radius {
            servers {
                freeradius {
                    address = 100.204.0.54
                    secret = radiussecret
                    auth_port = 31812
                    acct_port = 31813
                }
            }
            dae {
                enable = yes
            }
            accounting = yes
            nas_identifier = strongswan-ikev2
        }
    }
}
EOF

echo "Starting StrongSwan service..."
exec "$@"
