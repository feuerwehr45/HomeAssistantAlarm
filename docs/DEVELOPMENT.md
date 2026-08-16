# Entwicklungsstand & Weiterarbeit

Diese Datei richtet sich an Entwickler (auch an eine zukünftige Claude-Code-
Session ohne Kenntnis dieses Chatverlaufs), die an dieser HACS-Integration
weiterarbeiten. Sie hält fest, was gebaut wurde, warum, und was als
Nächstes ansteht. Nutzerdoku (Installation/Einrichtung) steht in der
[README](../README.md).

## Status (Stand: 2026-08-16)

Erstimplementierung abgeschlossen, **aber noch nie gegen eine echte
Home-Assistant-Instanz getestet** – auf der Entwicklungsmaschine war kein
Python verfügbar, es gab nur eine statische Prüfung des Codes (Lesen +
manuelle Konsistenzprüfung der Importe/Signaturen). Vor produktivem Einsatz
unbedingt in einer echten HA-Dev-Umgebung durchspielen (siehe "Nächste
Schritte" unten).

Die Implementierung folgt strikt [`docs/homeassistant-integration-api.md`](homeassistant-integration-api.md)
(Server-API-Referenz, vom Nutzer vorgegeben – nicht verändern, ohne die
Server-Seite abzugleichen).

## Architektur-Entscheidungen & Begründung

- **`entry.runtime_data` statt `hass.data[DOMAIN][entry_id]`.** Modernes
  HA-Pattern für typisierte Config-Entry-Daten. Erfordert neuere HA-Core-
  APIs – deshalb `hacs.json` → `"homeassistant": "2024.12.0"` als
  Mindestversion gesetzt.
- **Reauth über `entry.async_start_reauth()`, `self._get_reauth_entry()`,
  `self.async_update_reload_and_abort()`.** Diese Helper kamen mit dem
  Reauth-Flow-Umbau in HA-Core ca. 2024.8–2024.12. Falls beim Testen
  `AttributeError` auf einer dieser Methoden auftritt, war die
  HA-Testversion zu alt – dann entweder HA aktualisieren oder auf den
  älteren manuellen Reauth-Flow (`hass.config_entries.flow.async_init(...,
  context={"source": SOURCE_REAUTH})`) zurückrüsten.
- **Push (SSE) primär, Poll nur zum Aufholen.** Direkt aus der Server-API-
  Doku übernommen (Abschnitt "Empfohlene Strategie"). `coordinator.py` →
  `GroupAlarmConnection._async_run()`: Aufhol-Poll → Stream öffnen →
  Backoff-Reconnect (1s bis max. 30s) bei Abbruch.
- **Dedupe über `id`.** `deque(maxlen=50)` der zuletzt gesehenen IDs in
  `GroupAlarmConnection`, wie in der Server-Doku empfohlen (Alarme können
  durch SSE **und** den nachfolgenden Aufhol-Poll doppelt ankommen).
- **`latest_id` wird persistiert** (`homeassistant.helpers.storage.Store`,
  ein Store pro Config-Entry), damit nach einem HA-Neustart nahtlos beim
  letzten bekannten Alarm weitergemacht wird, statt erneut die letzten 50
  Alarme zu bekommen.
- **Sensoren nur für Organisationen, die beim Setup in
  `subscribedOrganizations` standen** (`sensor.py` liest das aus der
  `/status`-Antwort, die beim Config-Entry-Setup einmal geholt wird).
  Bewusste Vereinfachung: Wird eine Organisation *nach* dem Einrichten neu
  im GroupAlarm-Profil zugeordnet, entsteht dafür **kein** automatischer
  neuer Sensor. Workaround für Nutzer: Integration neu laden. Falls das
  stört, wäre der nächste Schritt, `subscribedOrganizations` regelmäßig
  neu abzufragen (z. B. bei jedem Aufhol-Poll) und neue Sensoren dynamisch
  per `async_add_entities` nachzuschieben.
- **Event `groupalarm_alarm` ist der primäre Automatisierungs-Mechanismus**,
  nicht die Sensor-States – auch das eine explizite Empfehlung aus der
  Server-Doku (State-Changes lösen bei identischen Folge-Updates nicht
  zuverlässig aus).

## Dateiübersicht

```
custom_components/groupalarm/
├── api.py           # GroupAlarmClient: /status, /poll, /stream (SSE-Parsing)
├── coordinator.py    # GroupAlarmConnection: Aufhol-Poll + Stream-Loop, Dedupe, Persistenz
├── config_flow.py    # Setup-Dialog + Reauth-Flow
├── __init__.py       # async_setup_entry/async_unload_entry, runtime_data
├── sensor.py          # Letzter-Alarm-Sensor gesamt + je Organisation
├── const.py            # Domain, Signalnamen, Defaults/Timeouts
├── manifest.json
├── strings.json / translations/{en,de}.json
```

## Bekannte Lücken / offene TODOs

1. **Ungetestet.** Noch nie in einer laufenden HA-Instanz geladen worden.
   Vor allem prüfen: Config-Flow (inkl. Fehlerfälle 401/404/Timeout),
   SSE-Parsing gegen einen echten oder gemockten Stream, Reconnect-
   Verhalten, Reauth-Flow, Sensor-Attribute.
2. **`manifest.json` enthält Platzhalter** (`your-github-username`) bei
   `codeowners`, `documentation`, `issue_tracker` – vor Veröffentlichung
   durch echte GitHub-Angaben ersetzen.
3. **Keine automatisierten Tests** (kein `tests/`-Verzeichnis, kein
   pytest-Setup à la `pytest-homeassistant-custom-component`).
4. **Keine CI.** Für HACS-Veröffentlichung sinnvoll: GitHub Actions mit
   `home-assistant/actions/hassfest` und `hacs/action`.
5. **Keine dynamische Organisations-Sensor-Erstellung** zur Laufzeit (siehe
   Architektur-Punkt oben).
6. **`iot_class: cloud_push`** in `manifest.json` – korrekt, da der
   Normalbetrieb über SSE läuft; falls sich das Verhalten grundlegend
   ändert, diesen Wert mit anpassen (HACS/hassfest validiert das gegen die
   tatsächliche Update-Methode nicht streng, aber es ist die für Nutzer
   sichtbare Einordnung in HA).

## Nächste Schritte (Vorschlag)

1. Lokale HA-Dev-Umgebung aufsetzen (am einfachsten: offizieller
   [HA VS Code Devcontainer](https://developers.home-assistant.io/docs/development_environment)
   oder `pip install homeassistant` in einem venv) und
   `custom_components/groupalarm` einbinden, Config-Flow manuell
   durchklicken.
2. `hassfest` lokal laufen lassen (`python -m script.hassfest`, aus einem
   HA-Core-Checkout heraus) zur Manifest-/Struktur-Validierung.
3. GitHub-Repository anlegen, Platzhalter in `manifest.json` ersetzen,
   `hacs.json`/`README.md` ggf. mit echter Repo-URL ergänzen.
4. Optional: Tests mit `pytest-homeassistant-custom-component` für
   `api.py` (SSE-Parsing, Fehlerfälle) und `coordinator.py` (Dedupe,
   Backoff) schreiben – dort steckt die eigentliche Logik.
5. Bei HACS zur Aufnahme einreichen (Default-Repository-Antrag), sobald
   Punkt 1–4 erledigt sind.
