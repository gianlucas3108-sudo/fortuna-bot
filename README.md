# Fortuna Düsseldorf News-Bot 🔴⚪

Postet neue Artikel über Fortuna Düsseldorf automatisch in einen Telegram-Channel.
Quelle: Google News (deutschsprachig: RP Online, kicker, NRZ, WZ, Sport1, …). Nur Python nötig, keine Zusatzpakete.

## Einrichtung

1. **Bot erstellen:** In Telegram `@BotFather` öffnen → `/newbot` → Namen vergeben → Token kopieren.
2. **Channel vorbereiten:** Channel öffnen → Administratoren → Bot hinzufügen (Recht „Nachrichten senden“).
3. **config.env ausfüllen:** `TELEGRAM_BOT_TOKEN` und `TELEGRAM_CHANNEL` (z. B. `@meinfortunachannel`) eintragen.
4. **Starten:** `python fortuna_bot.py`

## Befehle

| Befehl | Wirkung |
|---|---|
| `python fortuna_bot.py` | läuft dauerhaft, prüft alle 15 Min. |
| `python fortuna_bot.py --once` | prüft einmal (für Windows-Aufgabenplanung) |
| `python fortuna_bot.py --test` | zeigt aktuelle Artikel an, sendet nichts |

Bereits gepostete Artikel stehen in `seen.json` – löschen setzt den Bot zurück.
