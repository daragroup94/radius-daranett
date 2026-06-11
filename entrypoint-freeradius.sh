#!/bin/bash
set -e

echo "==================================================="
echo "FreeRADIUS Container Starting..."
echo "==================================================="

# Remove old symlink if exists
if [ -L /etc/freeradius/3.0/mods-enabled/sql ]; then
    echo "Removing old SQL symlink..."
    rm -f /etc/freeradius/3.0/mods-enabled/sql
fi

# Create symlink for SQL module
if [ -f /etc/freeradius/3.0/mods-available/sql ]; then
    echo "Creating SQL module symlink..."
    ln -sf /etc/freeradius/3.0/mods-available/sql /etc/freeradius/3.0/mods-enabled/sql
    echo "✓ SQL module enabled"
else
    echo "✗ WARNING: SQL config not found at /etc/freeradius/3.0/mods-available/sql"
fi

# Create symlink for SQL Hotspot module
if [ -f /etc/freeradius/3.0/mods-available/sql_hotspot ]; then
    echo "Creating SQL Hotspot module symlink..."
    ln -sf /etc/freeradius/3.0/mods-available/sql_hotspot /etc/freeradius/3.0/mods-enabled/sql_hotspot
    echo "✓ SQL Hotspot module enabled"
fi

# Verify symlink
if [ -L /etc/freeradius/3.0/mods-enabled/sql ]; then
    echo "✓ SQL symlink verified: $(readlink -f /etc/freeradius/3.0/mods-enabled/sql)"
else
    echo "✗ ERROR: SQL symlink not created!"
fi

# Test database connection
echo ""
echo "Testing database connection..."
for i in {1..10}; do
    if PGPASSWORD=$DB_PASS psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c "SELECT COUNT(*) as users FROM radcheck;" 2>/dev/null; then
        echo "✓ Database connection successful"
        break
    else
        echo "⏳ Waiting for database... (attempt $i/10)"
        sleep 2
    fi
done

echo ""
echo "==================================================="
echo "Starting FreeRADIUS in debug mode..."
echo "==================================================="
echo ""

# Add routes to NAS subnets via host (VPN gateway)
echo "Adding routes to NAS subnets..."
ip route add 10.0.0.0/8 via 100.204.0.1 2>/dev/null && echo "✓ Route 10.0.0.0/8 added" || echo "⚠ Route 10.0.0.0/8 already exists"
ip route add 203.0.16.0/24 via 100.204.0.1 2>/dev/null && echo "✓ Route 203.0.16.0/24 added" || echo "⚠ Route 203.0.16.0/24 already exists"

# Start FreeRADIUS
exec "$@"
