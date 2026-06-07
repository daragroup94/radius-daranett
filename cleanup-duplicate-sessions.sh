#!/bin/bash
# cleanup-duplicate-sessions.sh

LOGFILE="$(dirname "$0")/logs/freeradius-cleanup.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] Starting cleanup..." >> $LOGFILE

RESULT=$(docker exec -i freeradius-db psql -U radius -d radius <<EOSQL
WITH dupe AS (
    SELECT radacctid,
           ROW_NUMBER() OVER (
               PARTITION BY username, callingstationid, calledstationid
               ORDER BY acctstarttime DESC
           ) AS rn
    FROM radacct
    WHERE acctstoptime IS NULL
      AND LOWER(calledstationid) LIKE '%pppoe%'
)
UPDATE radacct
SET acctstoptime = COALESCE(acctupdatetime, acctstarttime, NOW()), acctterminatecause = 'Lost-Carrier'
WHERE radacctid IN (
    SELECT radacctid FROM dupe WHERE rn > 1
);
EOSQL
)

echo "[$TIMESTAMP] $RESULT" >> $LOGFILE

# Kirim notifikasi jika ada duplikasi yang dihapus
if echo "$RESULT" | grep -q "UPDATE [1-9]"; then
    echo "[$TIMESTAMP] WARNING: Duplicate sessions detected and cleaned!" >> $LOGFILE
fi
