#!/bin/bash
# check-duplicate-sessions.sh

LOGFILE="$(dirname "$0")/logs/freeradius-check.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Check duplikasi
DUPLICATES=$(docker exec -i freeradius-db psql -U radius -d radius -t <<EOSQL
SELECT COUNT(*) FROM (
    SELECT username, callingstationid, COUNT(*) as cnt
    FROM radacct
    WHERE acctstoptime IS NULL
    GROUP BY username, callingstationid
    HAVING COUNT(*) > 1
) AS dupes;
EOSQL
)

DUPLICATES=$(echo $DUPLICATES | tr -d ' ')

if [ "$DUPLICATES" -gt 0 ]; then
    echo "[$TIMESTAMP] ⚠️  Found $DUPLICATES duplicate sessions!" | tee -a $LOGFILE
    
    # Tampilkan detail
    docker exec -i freeradius-db psql -U radius -d radius <<EOSQL
SELECT username, callingstationid, COUNT(*) as session_count,
       STRING_AGG(acctsessionid::text, ', ') as session_ids
FROM radacct
WHERE acctstoptime IS NULL
GROUP BY username, callingstationid
HAVING COUNT(*) > 1;
EOSQL
else
    echo "[$TIMESTAMP] ✓ No duplicate sessions found"
fi
