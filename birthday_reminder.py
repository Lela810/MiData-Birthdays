#!/usr/bin/env python3
import os, smtplib, logging, json
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

import re
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

MIDATA_BASE_URL  = os.environ.get("MIDATA_BASE_URL", "https://db.scout.ch")
MIDATA_TOKEN     = os.environ.get("MIDATA_TOKEN")
GROUP_ID         = int(os.environ.get("GROUP_ID"))
FILTER_ID        = int(os.environ.get("FILTER_ID"))
SMTP_HOST        = os.environ.get("SMTP_HOST")
SMTP_PORT        = int(os.environ.get("SMTP_PORT", 25))
MAIL_FROM        = os.environ.get("MAIL_FROM")
MAIL_TO          = os.environ.get("MAIL_TO")
MAIL_SUBJECT_TPL = os.environ.get("MAIL_SUBJECT_TPL", "Geburtstag: {name} hat am {date} Geburtstag!")
FETCH_WORKERS    = int(os.environ.get("FETCH_WORKERS", 5))
REMINDER_DAYS    = [int(d) for d in os.environ.get("REMINDER_DAYS", "7,1").split(",")]
DAYS_AHEAD       = max(REMINDER_DAYS)
STATE_FILE       = os.environ.get("STATE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "reminder_state.json"))


def load_state():
    """Laedt den State (bereits gesendete Reminder) aus JSON-Datei."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("State-Datei konnte nicht gelesen werden: %s", e)
    return {}


def save_state(state):
    """Speichert den State in JSON-Datei."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        log.info("State gespeichert in %s", STATE_FILE)
    except OSError as e:
        log.error("State konnte nicht gespeichert werden: %s", e)


def make_state_key(person_id, bday_date, days_until):
    """Erzeugt einen eindeutigen Key: person_id:geburtsjahr:days_until."""
    return f"{person_id}:{bday_date}:{days_until}"


def filter_already_sent(birthdays, state):
    """Filtert Personen heraus, fuer die bereits ein Reminder gesendet wurde."""
    unsent = []
    for b in birthdays:
        key = make_state_key(b["id"], b["bday_date"], b["days_until"])
        if key in state:
            log.info("  Bereits gesendet: %s %s (days_until=%d) am %s",
                     b["first_name"], b["last_name"], b["days_until"], state[key])
        else:
            unsent.append(b)
    return unsent


def mark_as_sent(birthdays, state):
    """Markiert Reminder als gesendet im State."""
    today_str = date.today().isoformat()
    for b in birthdays:
        key = make_state_key(b["id"], b["bday_date"], b["days_until"])
        state[key] = today_str
    # Alte Eintraege aufraeuemen (aelter als dieses Jahr)
    current_year = str(date.today().year)
    keys_to_remove = [k for k in state if not k.split(":")[1].startswith(current_year)]
    for k in keys_to_remove:
        del state[k]
    return state


def get_headers():
    if not MIDATA_TOKEN:
        raise EnvironmentError("MIDATA_TOKEN nicht gesetzt.")
    return {"X-TOKEN": MIDATA_TOKEN, "Accept": "application/json"}


def fetch_person_details(href):
    """
    Laedt Detailinfos einer Person.
    Verwendet den href-Link aus dem Filter-Response.

    Wichtig: birthday ist NUR hier vorhanden, NICHT im Filter-Response!

    Response-Struktur:
      {
        "people": [{ "id": "51580", "birthday": "2003-04-10", ... }],
        "linked": { "additional_emails": [{"email": "..."}], ... }
      }
    """
    r = requests.get(href, headers=get_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def enrich_person(person):
    """Reichert Person mit birthday + E-Mail aus dem Einzelabruf an."""
    pid = str(person["id"])
    href = person.get("href", f"{MIDATA_BASE_URL}/groups/{GROUP_ID}/people/{pid}.json")
    try:
        details = fetch_person_details(href)
        people_data = details.get("people", [])
        dp = people_data[0] if isinstance(people_data, list) and people_data else people_data

        bday = dp.get("birthday")
        if bday:
            person["birthday"] = bday
            log.debug("  [%s] %s %s -> birthday: %s", pid, person.get("first_name"), person.get("last_name"), bday)
        else:
            log.debug("  [%s] %s %s -> kein birthday eingetragen", pid, person.get("first_name"), person.get("last_name"))

        if not person.get("email"):
            extras = details.get("linked", {}).get("additional_emails", [])
            if extras and extras[0].get("email"):
                person["email"] = extras[0]["email"]

        # Adresse extrahieren
        person["address_street"] = dp.get("address", "")
        person["address_zip"] = dp.get("zip_code", "")
        person["address_town"] = dp.get("town", "")

        # Mobilnummer extrahieren (nur Schweizer Mobilnummern 076-079)
        phone_numbers = details.get("linked", {}).get("phone_numbers", [])
        for pn in phone_numbers:
            label = (pn.get("label") or "").lower()
            number = pn.get("number", "")
            if label in ("mobil", "mobile", "handy", "natel") and number and is_swiss_mobile(number):
                person["mobile"] = number
                break
        # Fallback: erste Nummer die eine CH-Mobilnummer ist
        if not person.get("mobile") and phone_numbers:
            for pn in phone_numbers:
                number = pn.get("number", "")
                if number and is_swiss_mobile(number):
                    person["mobile"] = number
                    break

    except requests.HTTPError as e:
        log.warning("HTTP-Fehler Person %s: %s", pid, e)
    except Exception as e:
        log.warning("Fehler Person %s: %s", pid, e)
    return person


def fetch_people_via_filter(group_id, filter_id):
    """
    Schritt 1: Alle Personen via Filter laden (mit Pagination).
    Schritt 2: Detaildaten jeder Person parallel nachladen.
               -> birthday MUSS einzeln abgerufen werden!
    """
    url = f"{MIDATA_BASE_URL}/groups/{group_id}/people.json"
    params = {"filter_id": filter_id}
    log.info("Lade Personen via Filter %s aus Gruppe %s...", filter_id, group_id)

    people = []
    while url:
        r = requests.get(url, headers=get_headers(), params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        people.extend(data.get("people", []))
        url = data.get("next_page_link")
        params = None

    log.info("%d Personen gefunden - lade Detaildaten (%d Workers)...", len(people), FETCH_WORKERS)

    enriched = [None] * len(people)
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
        futures = {ex.submit(enrich_person, p): i for i, p in enumerate(people)}
        done = 0
        for f in as_completed(futures):
            enriched[futures[f]] = f.result()
            done += 1
            if done % 10 == 0 or done == len(people):
                log.info("  Detaildaten: %d/%d", done, len(people))

    log.info("Alle Detaildaten geladen.")
    return enriched


def birthdays_in_window(people, days_ahead):
    today = date.today()
    upcoming = []
    no_bday = 0

    for person in people:
        bday_str = person.get("birthday")
        if not bday_str:
            no_bday += 1
            continue
        try:
            bday = date.fromisoformat(bday_str)
        except ValueError:
            log.warning("Ungueltig: %s %s: %s", person.get("first_name"), person.get("last_name"), bday_str)
            continue

        try:
            this_year = bday.replace(year=today.year)
        except ValueError:
            this_year = bday.replace(year=today.year, day=28)  # 29. Feb Fallback

        if this_year < today:
            try:
                this_year = bday.replace(year=today.year + 1)
            except ValueError:
                this_year = bday.replace(year=today.year + 1, day=28)

        diff = (this_year - today).days
        if 0 <= diff <= days_ahead:
            upcoming.append({
                "id":         person.get("id"),
                "first_name": person.get("first_name", ""),
                "last_name":  person.get("last_name", ""),
                "pfadiname":  person.get("nickname", ""),
                "bday_raw":   bday_str,
                "bday_date":  this_year.isoformat(),
                "days_until": diff,
                "age":        this_year.year - bday.year,
                "email":      person.get("email", ""),
                "mobile":     person.get("mobile", ""),
                "address_street": person.get("address_street", ""),
                "address_zip":    person.get("address_zip", ""),
                "address_town":   person.get("address_town", ""),
            })

    if no_bday:
        log.warning("%d Person(en) ohne Geburtsdatum in MiData.", no_bday)

    upcoming.sort(key=lambda p: p["days_until"])
    return upcoming


def is_swiss_mobile(number):
    """Prueft ob eine Nummer eine Schweizer Mobilnummer ist (076/077/078/079)."""
    digits = re.sub(r'[^\d]', '', number.lstrip('+'))
    # +41 7X... oder 0041 7X...
    if digits.startswith('41') and len(digits) >= 11:
        prefix = digits[2:4]
        return prefix in ('76', '77', '78', '79')
    # 07X...
    if digits.startswith('0') and len(digits) >= 10:
        prefix = digits[1:3]
        return prefix in ('76', '77', '78', '79')
    return False


def format_whatsapp_link(mobile):
    """Formatiert eine Mobilnummer als wa.me Link."""
    digits = re.sub(r'[^\d+]', '', mobile)
    # +41 beibehalten, fuehrende 0 durch 41 ersetzen
    if digits.startswith('+'):
        digits = digits[1:]
    elif digits.startswith('00'):
        digits = digits[2:]
    elif digits.startswith('0'):
        digits = '41' + digits[1:]
    return f"https://wa.me/{digits}"


def build_mail(birthdays):
    today_str = date.today().strftime("%d.%m.%Y")
    first = birthdays[0]
    name0 = first["pfadiname"] or first["first_name"]
    date0 = date.fromisoformat(first["bday_date"]).strftime("%d.%m.%Y")
    subject = MAIL_SUBJECT_TPL.format(name=name0, date=date0)
    if len(birthdays) > 1:
        subject += f" (+{len(birthdays)-1} weitere)"

    entries_html = ""
    entries_text = ""
    for p in birthdays:
        full = (
            f"{p['pfadiname']} ({p['first_name']} {p['last_name']})"
            if p["pfadiname"]
            else f"{p['first_name']} {p['last_name']}"
        )
        d = date.fromisoformat(p["bday_date"]).strftime("%d.%m.%Y")
        if p["days_until"] == 1:
            wann = "Morgen!"
        else:
            wann = f"in {p['days_until']} Tag(en)"

        show_whatsapp = p["days_until"] <= 1
        has_address = p["address_street"] or p["address_zip"] or p["address_town"]

        # HTML
        entries_html += (
            f"<div style='margin-bottom:18px;padding:12px 16px;border-left:4px solid #2e7d32;background:#f9f9f9'>\n"
            f"<p style='margin:0 0 4px 0;font-size:16px'><strong>{full}</strong> wird am {d} <strong>{p['age']} Jahre</strong> alt ({wann})</p>\n"
        )
        if has_address:
            addr_html = f"<div style='margin:8px 0 0 0;padding:8px 12px;background:#f0f0f0;border-radius:4px;font-size:14px;line-height:1.6'>"
            addr_html += f"{p['first_name']} {p['last_name']}<br>"
            if p["address_street"]:
                addr_html += f"{p['address_street']}<br>"
            zip_town = f"{p['address_zip']} {p['address_town']}".strip()
            if zip_town:
                addr_html += f"{zip_town}"
            addr_html += "</div>\n"
            entries_html += addr_html
        if show_whatsapp and p["mobile"]:
            wa_link = format_whatsapp_link(p["mobile"])
            entries_html += (
                f"<p style='margin:8px 0 0 0'>"
                f"<a href='{wa_link}' style='color:#25D366;text-decoration:none;font-weight:bold'>"
                f"WhatsApp ({p['mobile']})</a></p>\n"
            )
        entries_html += "</div>\n"

        # Plain text
        entries_text += f"{full} wird am {d} {p['age']} Jahre alt ({wann})\n"
        if has_address:
            entries_text += f"  {p['first_name']} {p['last_name']}\n"
            if p["address_street"]:
                entries_text += f"  {p['address_street']}\n"
            zip_town = f"{p['address_zip']} {p['address_town']}".strip()
            if zip_town:
                entries_text += f"  {zip_town}\n"
        if show_whatsapp and p["mobile"]:
            wa_link = format_whatsapp_link(p["mobile"])
            entries_text += f"  WhatsApp: {wa_link}\n"
        entries_text += "\n"

    html = (
        "<!DOCTYPE html>\n<html lang='de'>\n<head><meta charset='utf-8'></head>\n"
        "<body style='font-family:Arial,sans-serif;color:#222;max-width:640px;margin:0 auto;padding:20px'>\n"
        "<h2 style='color:#2e7d32;border-bottom:2px solid #2e7d32;padding-bottom:8px'>Geburtstagsreminder</h2>\n"
        f"<p style='color:#555'>Pfadi Huenenberg - Stand: {today_str}</p>\n"
        f"{entries_html}"
        f"<p style='margin-top:24px;color:#aaa;font-size:11px'>"
        f"Automatisch generiert - Gruppe {GROUP_ID} - Filter {FILTER_ID} - db.scout.ch</p>\n"
        "</body>\n</html>"
    )

    txt = (
        f"Geburtstagsreminder - Pfadi Huenenberg\n"
        f"Stand: {today_str}\n"
        + "=" * 50 + "\n\n"
        + entries_text
        + "=" * 50 + "\n"
        + "Automatisch generiert via db.scout.ch\n"
    )

    return subject, html, txt


def send_mail_smtp(birthdays):
    if not all([SMTP_HOST, MAIL_FROM, MAIL_TO]):
        raise EnvironmentError("SMTP_HOST, MAIL_FROM, MAIL_TO muessen gesetzt sein.")

    subject, html, txt = build_mail(birthdays)

    msg = MIMEMultipart("alternative")
    msg["Subject"]  = subject
    msg["From"]     = MAIL_FROM
    msg["To"]       = MAIL_TO
    msg["X-Mailer"] = "Pfadi-Birthday-Reminder/2.0"
    msg.attach(MIMEText(txt,  "plain", "utf-8"))
    msg.attach(MIMEText(html, "html",  "utf-8"))

    recipients = [r.strip() for r in MAIL_TO.split(",")]
    log.info("Verbinde mit SMTP %s:%s ...", SMTP_HOST, SMTP_PORT)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        # smtp.starttls()  # einkommentieren falls Connector TLS erfordert
        smtp.send_message(msg, MAIL_FROM, recipients)
    log.info("Mail gesendet an: %s", MAIL_TO)


def main():
    log.info("=== Pfadi Birthday Reminder gestartet ===")
    state = load_state()
    people    = fetch_people_via_filter(GROUP_ID, FILTER_ID)
    all_upcoming = birthdays_in_window(people, DAYS_AHEAD)
    log.info("%d Geburtstag(e) in den naechsten %d Tagen:", len(all_upcoming), DAYS_AHEAD)
    for b in all_upcoming:
        log.info("  %s %s (%s): %s - in %d Tag(en), wird %d%s",
                 b["first_name"], b["last_name"], b["pfadiname"] or "-",
                 b["bday_date"], b["days_until"], b["age"],
                 " -> REMINDER" if b["days_until"] in REMINDER_DAYS else "")

    birthdays = [b for b in all_upcoming if b["days_until"] in REMINDER_DAYS]
    log.info("%d davon an Reminder-Tagen (%s).", len(birthdays), ",".join(str(d) for d in sorted(REMINDER_DAYS)))

    if not birthdays:
        log.info("Kein Geburtstag an Reminder-Tagen - kein Mail.")
        return

    birthdays = filter_already_sent(birthdays, state)
    if not birthdays:
        log.info("Alle Reminder bereits gesendet - kein Mail.")
        return

    send_mail_smtp(birthdays)
    state = mark_as_sent(birthdays, state)
    save_state(state)
    log.info("=== Fertig ===")


if __name__ == "__main__":
    main()