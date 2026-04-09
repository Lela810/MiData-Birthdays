#!/bin/bash
# Healthcheck: verify cron daemon is running and env is available

# Check cron process is alive
if ! pgrep -x cron > /dev/null 2>&1; then
    echo "UNHEALTHY: cron process not running"
    exit 1
fi

# Check environment file exists (needed for cron jobs)
if [ ! -f /etc/environment ]; then
    echo "UNHEALTHY: /etc/environment missing"
    exit 1
fi

# Check critical env vars are in /etc/environment
if ! grep -q "MIDATA_TOKEN" /etc/environment; then
    echo "UNHEALTHY: MIDATA_TOKEN not found in /etc/environment"
    exit 1
fi

echo "OK: cron running, environment available"
exit 0
