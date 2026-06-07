#!/bin/bash
# cleanup-stale-sessions.sh

LOGFILE="$(dirname "$0")/logs/freeradius-cleanup.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] Starting stale sessions cleanup..." >> $LOGFILE

# Mark sessions as stale if no update received in the last 5 minutes (ONLY for PPPoE)
RESULT=$(docker exec -i freeradius-db psql -U radius -d radius <<EOSQL
UPDATE radacct
SET acctstoptime = COALESCE(acctupdatetime, acctstarttime, NOW()), acctterminatecause = 'Lost-Carrier'
WHERE acctstoptime IS NULL
  AND LOWER(calledstationid) LIKE '%pppoe%'
  AND acctupdatetime < NOW() - INTERVAL '10 minutes';
EOSQL
)

echo "[$TIMESTAMP] $RESULT" >> $LOGFILE

# Log warning if any stale sessions were cleaned up
if echo "$RESULT" | grep -q "UPDATE [1-9]"; then
    echo "[$TIMESTAMP] WARNING: Stale sessions detected and marked as offline!" >> $LOGFILE
fi
