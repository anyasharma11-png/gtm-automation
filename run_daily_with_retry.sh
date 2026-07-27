#!/bin/bash
# run_daily_with_retry.sh
#
# Wraps the full daily pipeline with retry logic, since scheduled runs can
# fire right as the Mac wakes from sleep -- before WiFi has reconnected.
#
# Strategy: wait 30s upfront (let WiFi settle), then try the full chain.
# If it fails, wait 2 minutes and retry, up to 3 total attempts.

cd "$(dirname "$0")"
source venv/bin/activate

MAX_ATTEMPTS=3
ATTEMPT=1

# Give WiFi a moment to reconnect if we just woke from sleep
sleep 30

while [ $ATTEMPT -le $MAX_ATTEMPTS ]; do
    echo "=== Attempt $ATTEMPT of $MAX_ATTEMPTS ($(date)) ==="

    python3 run_all.py && \
    python3 create_deals.py && \
    python3 deal_coaching.py && \
    python3 harvest_to_drive.py

    if [ $? -eq 0 ]; then
        echo "=== Succeeded on attempt $ATTEMPT ==="

        echo "=== Syncing to GitHub ==="
        if [ -n "$(git status --porcelain)" ]; then
            git add -A
            git commit -m "Auto-sync: daily pipeline run $(date '+%Y-%m-%d %H:%M')"
            git push origin main
            if [ $? -eq 0 ]; then
                echo "=== Pushed to GitHub successfully ==="
            else
                echo "=== GitHub push FAILED -- check your internet connection or git credentials ==="
            fi
        else
            echo "=== No changes to sync -- skipping commit/push ==="
        fi

        exit 0
    fi

    echo "=== Attempt $ATTEMPT failed, waiting 2 minutes before retry ==="
    ATTEMPT=$((ATTEMPT + 1))
    sleep 120
done

echo "=== All $MAX_ATTEMPTS attempts failed. Giving up for today. ==="
exit 1
