FROM python:3.12-slim

LABEL maintainer="Pfadi Huenenberg" \
    description="MiData Geburtstagsreminder fuer Leiter"

WORKDIR /app

# Abhaengigkeiten zuerst (besseres Layer-Caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Script + Entrypoint kopieren
COPY birthday_reminder.py .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# Cron installieren
RUN apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

# Healthcheck script
COPY healthcheck.sh .
RUN chmod +x healthcheck.sh

# Crontab: taeglich 05:00 UTC = 07:00 CEST
# Loads env vars from /etc/environment so Docker env is available to cron
RUN echo '0 5 * * * root . /etc/environment; cd /app && python birthday_reminder.py >> /proc/1/fd/1 2>> /proc/1/fd/2' \
    > /etc/cron.d/birthday-reminder \
    && chmod 0644 /etc/cron.d/birthday-reminder \
    && crontab /etc/cron.d/birthday-reminder

HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD /app/healthcheck.sh

ENTRYPOINT ["/app/entrypoint.sh"]