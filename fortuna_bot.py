"""Fortuna Düsseldorf News-Bot für Telegram.

Liest News-Feeds (RSS) zu Fortuna Düsseldorf und postet neue Artikel
in einen Telegram-Channel. Benötigt nur die Python-Standardbibliothek.

Start:
    python fortuna_bot.py           # läuft dauerhaft, prüft regelmäßig
    python fortuna_bot.py --once    # prüft einmal und beendet sich (für Aufgabenplanung)
    python fortuna_bot.py --test    # zeigt gefundene Artikel an, ohne zu senden
    python fortuna_bot.py --find-channel  # zeigt die ID eines privaten Channels
"""

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.env"
SEEN_FILE = BASE_DIR / "seen.json"
MAX_SEEN = 2000
USER_AGENT = "Mozilla/5.0 (FortunaNewsBot/1.0)"

DEFAULT_FEEDS = [
    # Google News: deutschsprachige Artikel aller großen Medien (kicker, RP, WZ, Sport1, f95.de ...)
    "https://news.google.com/rss/search?q=%22Fortuna+D%C3%BCsseldorf%22+when:2d&hl=de&gl=DE&ceid=DE:de",
]


def load_config():
    """Liest config.env; Umgebungsvariablen (z. B. GitHub-Secrets) haben Vorrang."""
    cfg = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            cfg[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL", "CHECK_INTERVAL_MINUTES",
                "FIRST_RUN_POSTS", "EXTRA_FEEDS"):
        if os.environ.get(key):
            cfg[key] = os.environ[key]

    feeds = [f.strip() for f in cfg.get("EXTRA_FEEDS", "").split(",") if f.strip()]
    return {
        "token": cfg.get("TELEGRAM_BOT_TOKEN", ""),
        "channel": cfg.get("TELEGRAM_CHANNEL", ""),
        "interval": int(cfg.get("CHECK_INTERVAL_MINUTES", "15")),
        "first_run_posts": int(cfg.get("FIRST_RUN_POSTS", "3")),
        "feeds": DEFAULT_FEEDS + feeds,
    }


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_feed(data):
    """Liefert eine Liste von Artikeln (dict) aus RSS 2.0 oder Atom."""
    root = ET.fromstring(data)
    items = []

    for item in root.iter("item"):  # RSS 2.0
        source_el = item.find("source")
        items.append({
            "id": (item.findtext("guid") or item.findtext("link") or "").strip(),
            "title": (item.findtext("title") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "source": source_el.text.strip() if source_el is not None and source_el.text else "",
            "date": item.findtext("pubDate") or "",
        })

    atom = "{http://www.w3.org/2005/Atom}"
    for entry in root.iter(f"{atom}entry"):  # Atom
        link_el = entry.find(f"{atom}link")
        link = link_el.get("href", "") if link_el is not None else ""
        items.append({
            "id": (entry.findtext(f"{atom}id") or link).strip(),
            "title": (entry.findtext(f"{atom}title") or "").strip(),
            "link": link.strip(),
            "source": "",
            "date": entry.findtext(f"{atom}updated") or "",
        })
    return items


def timestamp(item):
    try:
        return parsedate_to_datetime(item["date"]).timestamp()
    except (TypeError, ValueError):
        return 0.0


def clean_title(item):
    """Google News hängt ' - Quelle' an den Titel; das entfernen wir, weil die Quelle extra steht."""
    title = item["title"]
    if item["source"] and title.endswith(" - " + item["source"]):
        title = title[: -len(" - " + item["source"])]
    return title


def normalize(title):
    return re.sub(r"\W+", " ", title.lower()).strip()


def fetch_all(feeds):
    articles = []
    for url in feeds:
        try:
            articles.extend(parse_feed(http_get(url)))
        except (urllib.error.URLError, ET.ParseError, TimeoutError) as e:
            print(f"[WARN] Feed konnte nicht geladen werden: {url}\n       {e}")
    articles.sort(key=timestamp)  # älteste zuerst, damit der Channel chronologisch bleibt
    return [a for a in articles if a["id"] and a["title"]]


def send_message(token, channel, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": channel,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "false",
    }).encode()
    for _ in range(3):
        try:
            req = urllib.request.Request(url, data=payload)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.load(resp).get("ok", False)
        except urllib.error.HTTPError as e:
            body = json.loads(e.read() or b"{}")
            retry = body.get("parameters", {}).get("retry_after")
            if e.code == 429 and retry:
                time.sleep(retry + 1)
                continue
            print(f"[ERROR] Telegram: {e.code} {body.get('description', '')}")
            return False
        except urllib.error.URLError as e:
            print(f"[ERROR] Telegram nicht erreichbar: {e}")
            time.sleep(5)
    return False


def format_message(item):
    text = f"🔴⚪ <b>{html.escape(clean_title(item))}</b>"
    if item["source"]:
        text += f"\n📰 {html.escape(item['source'])}"
    text += f"\n\n<a href=\"{html.escape(item['link'], quote=True)}\">Zum Artikel</a>"
    return text


def load_seen():
    if SEEN_FILE.exists():
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return data.get("ids", []), data.get("titles", [])
    return None, None


def save_seen(ids, titles):
    SEEN_FILE.write_text(json.dumps({
        "ids": ids[-MAX_SEEN:],
        "titles": titles[-MAX_SEEN:],
    }, ensure_ascii=False), encoding="utf-8")


def check_once(cfg, dry_run=False):
    articles = fetch_all(cfg["feeds"])
    if dry_run:
        for a in articles[-15:]:
            print(f"- {clean_title(a)}  [{a['source']}]\n  {a['link']}")
        print(f"\n{len(articles)} Artikel gefunden.")
        return

    ids, titles = load_seen()
    first_run = ids is None
    ids, titles = ids or [], titles or []
    seen_ids, seen_titles = set(ids), set(titles)

    new = []
    for a in articles:
        key = normalize(clean_title(a))
        if a["id"] in seen_ids or key in seen_titles:  # gleicher Artikel oder gleiche Meldung aus anderem Feed
            continue
        new.append(a)
        seen_ids.add(a["id"])
        seen_titles.add(key)
        ids.append(a["id"])
        titles.append(key)

    # Beim allerersten Start nicht den ganzen Feed in den Channel kippen
    to_send = new[-cfg["first_run_posts"]:] if first_run else new
    if first_run and cfg["first_run_posts"] == 0:
        to_send = []

    sent = 0
    for a in to_send:
        if send_message(cfg["token"], cfg["channel"], format_message(a)):
            sent += 1
        time.sleep(1.5)  # Telegram-Limit für Channels respektieren

    save_seen(ids, titles)
    print(f"[{time.strftime('%H:%M:%S')}] {len(new)} neue Artikel, {sent} gesendet.")


def find_channel_id(token):
    """Zeigt die IDs der Channels, in denen kürzlich etwas gepostet wurde (für private Channels)."""
    data = json.loads(http_get(f"https://api.telegram.org/bot{token}/getUpdates"))
    if not data.get("ok"):
        sys.exit(f"Telegram-Fehler: {data.get('description')} – stimmt der Token?")
    chats = {}
    for upd in data["result"]:
        msg = upd.get("channel_post") or upd.get("my_chat_member") or upd.get("message") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat.get("title") or chat.get("username") or ""
    if not chats:
        print("Nichts gefunden. Schreib eine Nachricht in den Channel und starte den Befehl erneut.")
    for cid, title in chats.items():
        print(f"{title}:  TELEGRAM_CHANNEL={cid}")


def main():
    cfg = load_config()
    if "--find-channel" in sys.argv:
        if not cfg["token"]:
            sys.exit("Bitte zuerst TELEGRAM_BOT_TOKEN in config.env eintragen.")
        find_channel_id(cfg["token"])
        return
    if "--test" in sys.argv:
        check_once(cfg, dry_run=True)
        return
    if not cfg["token"] or not cfg["channel"]:
        sys.exit("TELEGRAM_BOT_TOKEN und TELEGRAM_CHANNEL müssen in config.env "
                 "(oder als Umgebungsvariablen) gesetzt sein.")

    if "--once" in sys.argv:
        check_once(cfg)
        return

    print(f"Fortuna-Bot läuft. Prüfe alle {cfg['interval']} Minuten. Beenden mit Strg+C.")
    while True:
        try:
            check_once(cfg)
        except Exception as e:  # Bot soll bei einzelnen Fehlern weiterlaufen
            print(f"[ERROR] {e}")
        time.sleep(cfg["interval"] * 60)


if __name__ == "__main__":
    main()
