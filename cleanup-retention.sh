#!/bin/bash
# cleanup-retention.sh
# Pembersihan data historis berkala (radpostauth, ont_signal_history, radacct lama)

LOGFILE="$(dirname "$0")/logs/freeradius-cleanup.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] Starting data retention pruning..." >> "$LOGFILE"

RESULT=$(docker exec -i freeradius-db psql -U radius -d radius <<EOSQL
-- 1. Bersihkan log otentikasi lebih dari 60 hari
DELETE FROM public.radpostauth 
WHERE authdate < NOW() - INTERVAL '60 days';

-- 2. Bersihkan riwayat sinyal optik lebih dari 30 hari
DELETE FROM public.ont_signal_history 
WHERE recorded_at < NOW() - INTERVAL '30 days';

-- 3. Bersihkan sesi akuntansi yang sudah closed lebih dari 180 hari
DELETE FROM public.radacct 
WHERE acctstoptime IS NOT NULL 
  AND acctstoptime < NOW() - INTERVAL '180 days';
EOSQL
)

echo "[$TIMESTAMP] Retention pruning result: $RESULT" >> "$LOGFILE"
