# MiData Birthday Reminder

Automatischer Geburtstagsreminder fuer Personen aus MiData (db.scout.ch).  
Das Script laedt taeglich alle Personen via MiData-API, prueft anstehende Geburtstage und versendet Erinnerungsmails via SMTP.

---

## Features

- **MiData-Integration** – Liest Personen via gespeichertem Filter inkl. Untergruppen
- **Konfigurierbare Reminder-Tage** – z.B. 7 Tage und 1 Tag vor dem Geburtstag (beliebig erweiterbar)
- **Duplikat-Schutz** – State-Datei verhindert doppelte Erinnerungen pro Person und Jahr
- **WhatsApp-Links** – Direktlink zur Mobilnummer (nur verifizierte Schweizer Mobilnummern 076–079)
- **HTML- und Plaintext-Mail** – Uebersichtliche Darstellung mit Name, Datum, Alter und Countdown
- **Docker-ready** – Laeuft containerisiert mit integriertem Cron-Job
- **Automatische .env-Unterstuetzung** – Fuer lokale Entwicklung

---

## Quickstart

### 1. Repository klonen

```bash
git clone https://github.com/dein-user/MiData-Birthdays.git
cd MiData-Birthdays
```

### 2. `.env` Datei erstellen

```bash
cp .env.example .env
nano .env
```

### 3. Container starten

```bash
docker compose up -d --build
```

### 4. Soforttest

```bash
docker compose exec birthday-reminder python birthday_reminder.py
```

### 5. Logs verfolgen

```bash
docker compose logs -f
```

---

## Umgebungsvariablen

| Variable           | Pflicht | Standard                                       | Beschreibung                                              |
| ------------------ | ------- | ---------------------------------------------- | --------------------------------------------------------- |
| `MIDATA_TOKEN`     | Ja      | –                                              | API-Token von db.scout.ch (Profil → Einstellungen)        |
| `GROUP_ID`         | Ja      | –                                              | ID der MiData-Gruppe                                      |
| `FILTER_ID`        | Ja      | –                                              | ID des gespeicherten MiData-Personenfilters               |
| `SMTP_HOST`        | Ja      | –                                              | SMTP-Host (z.B. `tenant.mail.protection.outlook.com`)     |
| `MAIL_FROM`        | Ja      | –                                              | Absender-Adresse (muss im Mail-System verifiziert sein)   |
| `MAIL_TO`          | Ja      | –                                              | Empfaenger (mehrere kommagetrennt)                        |
| `MIDATA_BASE_URL`  | Nein    | `https://db.scout.ch`                          | MiData-Instanz-URL                                        |
| `REMINDER_DAYS`    | Nein    | `7,1`                                          | Kommaseparierte Tage vor dem Geburtstag fuer Erinnerungen |
| `SMTP_PORT`        | Nein    | `25`                                           | SMTP-Port                                                 |
| `MAIL_SUBJECT_TPL` | Nein    | `Geburtstag: {name} hat am {date} Geburtstag!` | Betreff-Vorlage (`{name}` und `{date}` werden ersetzt)    |
| `FETCH_WORKERS`    | Nein    | `5`                                            | Anzahl parallele API-Abfragen fuer Personendetails        |
| `STATE_FILE`       | Nein    | `reminder_state.json`                          | Pfad zur State-Datei (Duplikat-Schutz)                    |

---

## MiData API-Token

1. [db.scout.ch](https://db.scout.ch) oeffnen und einloggen
2. Oben rechts auf den eigenen Namen → **Einstellungen**
3. Tab **Token** → API-Token generieren und kopieren
4. Token als `MIDATA_TOKEN` in die `.env` eintragen

> Das Token benoetigt Leserechte auf die konfigurierte Gruppe.

---

## Exchange Online Connector (optional)

Falls der Mailversand via Microsoft Exchange Online Direct Send erfolgt:

1. **Exchange Admin Center** → Nachrichtenfluss → Connectors
2. Neuer eingehender Connector (Partner → Office 365)
3. Authentifizierung: **IP-Adresse** des Docker-Hosts eintragen
4. Den MX-Hostname als `SMTP_HOST` verwenden (`<tenant>.mail.protection.outlook.com`)

> **Port 25** wird verwendet. Falls TLS erforderlich ist,  
> `smtp.starttls()` im Script einkommentieren und ggf. `SMTP_PORT=587` setzen.

---

## Cron-Zeitplan

Der Cron-Job im Container laeuft taeglich um **05:00 UTC = 07:00 CEST**:

```
0 5 * * * python birthday_reminder.py
```

Kein Geburtstag an einem Reminder-Tag → kein Mail wird gesendet.

---

## Beispiel-Mail

**Betreff:** `Geburtstag: Baer hat am 15.04.2026 Geburtstag!`

**Inhalt:**

> **Baer (Hans Muster)** wird am 15.04.2026 **23 Jahre** alt (in 7 Tagen)  
> [WhatsApp (079 123 45 67)](https://wa.me/41791234567)
>
> **Adler (Maria Beispiel)** wird am 09.04.2026 **28 Jahre** alt (Morgen!)  
> [WhatsApp (078 987 65 43)](https://wa.me/41789876543)

---

## State-Datei

Die Datei `reminder_state.json` speichert, welche Reminder bereits gesendet wurden.  
Dadurch werden pro Person und Jahr maximal so viele Mails gesendet, wie `REMINDER_DAYS` Eintraege hat (Standard: 2).

Alte Eintraege werden automatisch beim Jahreswechsel bereinigt.

In Docker wird die State-Datei ueber ein Volume (`reminder-data`) persistiert.

---

## Lokale Entwicklung (ohne Docker)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# .env Datei erstellen und befuellen
cp .env.example .env
nano .env

# Script ausfuehren
python birthday_reminder.py
```

Die `.env`-Datei wird automatisch geladen (via `python-dotenv`).

---

## Docker manuell (ohne Compose)

```bash
docker build -t birthday-reminder .

docker run -d \
  --name birthday-reminder \
  --restart unless-stopped \
  -e TZ=Europe/Zurich \
  -e MIDATA_TOKEN=dein-token \
  -e GROUP_ID=123 \
  -e FILTER_ID=456 \
  -e SMTP_HOST=tenant.mail.protection.outlook.com \
  -e MAIL_FROM=noreply@example.com \
  -e MAIL_TO=empfaenger@example.com \
  -v reminder-data:/app/data \
  birthday-reminder
```

---

## Technologien

- **Python 3.12** – `requests`, `smtplib`, `python-dotenv`
- **Docker** + **Docker Compose**
- **MiData JSON API** (`db.scout.ch`)
- **SMTP** (z.B. Microsoft Exchange Online Relay)

---

## Lizenz

MIT – frei verwendbar fuer andere Pfadi-Abteilungen.
