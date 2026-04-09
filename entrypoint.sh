#!/bin/bash
set -e

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [entrypoint] $*"; }

log "=== MiData Birthday Reminder starting ==="
log "Timezone:       ${TZ:-UTC}"
log "Group ID:       ${GROUP_ID:-not set}"
log "Filter ID:      ${FILTER_ID:-not set}"
log "Reminder days:  ${REMINDER_DAYS:-7,1}"
log "SMTP host:      ${SMTP_HOST:-not set}"
log "SMTP port:      ${SMTP_PORT:-25}"
log "Mail from:      ${MAIL_FROM:-not set}"
log "Mail to:        ${MAIL_TO:-not set}"
log "State file:     ${STATE_FILE:-reminder_state.json}"

# Validate required env vars
missing=0
for var in MIDATA_TOKEN GROUP_ID FILTER_ID SMTP_HOST MAIL_FROM MAIL_TO; do
    val=$(eval echo "\${$var:-}")
    if [ -z "$val" ]; then
        log "ERROR: Required variable $var is not set!"
        missing=1
    fi
done
if [ "$missing" -eq 1 ]; then
    log "Aborting due to missing environment variables."
    exit 1
fi

# Dump all environment variables so cron jobs can access them
printenv | grep -v "no_proxy" > /etc/environment

log "Cron schedule:  daily at 05:00 UTC (07:00 CEST)"
log "Container ready. Waiting for cron..."
log "============================================"

# Start cron in foreground
exec cron -f
