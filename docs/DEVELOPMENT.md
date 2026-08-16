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
- **`latest_id`, `last_alarm` und `last_alarm_by_org` werden persistiert**
  (`homeassistant.helpers.storage.Store`, ein Store pro Config-Entry), damit
  nach einem HA-Neustart nahtlos beim letzten bekannten Alarm weitergemacht
  wird (statt erneut die letzten 50 Alarme zu bekommen) und die Sensoren
  sofort wieder den letzten Alarmtext zeigen, statt bis zum nächsten neuen
  Alarm "unknown" anzuzeigen.
- **Organisationen werden per Timer nachgeführt statt nur beim Setup
  gelesen.** `GroupAlarmConnection.organizations` startet mit der
  `/status`-Antwort vom Config-Entry-Setup und wird danach alle
  `ORGANIZATION_REFRESH_INTERVAL` (15 Minuten, `const.py`) per
  `async_track_time_interval` gegen `/status` abgeglichen
  (`_async_refresh_organizations`). Neue Organisationen lösen das Signal
  `SIGNAL_NEW_ORGANIZATIONS` aus, auf das `sensor.py` hört, um dynamisch
  per `async_add_entities` einen neuen Organisations-Sensor nachzuschieben
  – kein Reload nötig. Der Refresh-Fehlerfall (Server kurz nicht
  erreichbar) wird nur geloggt (`_LOGGER.debug`), da er die primäre
  Stream-Verbindung/Verfügbarkeit nicht beeinflussen soll.
- **Event `homeassistantalarm_alarm` ist der primäre Automatisierungs-Mechanismus**,
  nicht die Sensor-States – auch das eine explizite Empfehlung aus der
  Server-Doku (State-Changes lösen bei identischen Folge-Updates nicht
  zuverlässig aus).

## Dateiübersicht

```
custom_components/homeassistantalarm/
├── api.py             # GroupAlarmClient: /status, /poll, /stream (SSE-Parsing)
├── coordinator.py     # GroupAlarmConnection: Aufhol-Poll + Stream-Loop, Dedupe, Persistenz
├── config_flow.py     # Setup-Dialog + Reauth-Flow
├── device_trigger.py  # Geraete-Trigger "Neuer Alarm" (+ optionaler Organisations-Filter) fuers Automations-UI
├── __init__.py         # async_setup_entry/async_unload_entry, runtime_data
├── sensor.py            # Letzter-Alarm-Sensor gesamt + je Organisation
├── const.py              # Domain, Signalnamen, Defaults/Timeouts
├── manifest.json
├── strings.json / translations/{en,de}.json
```

**`device_trigger.py`** macht "neuer Alarm" im Automations-Editor unter
*Trigger → Gerät → HomeAssistantAlarm-Verbindung* durchsuchbar/auswählbar,
statt dass Nutzer manuell einen generischen Event-Trigger mit dem
Event-Typ konfigurieren müssen. Delegiert intern an die Core-Trigger-
Plattform `homeassistant.components.homeassistant.triggers.event` (das
dokumentierte Standardmuster für event-basierte Device-Trigger). Bietet
zusätzlich optional einen Organisations-Filter (`extra_fields`), dessen
Auswahlliste live aus `entry.runtime_data.connection.organizations` gebaut
wird (also inklusive Organisationen, die erst nach dem Setup per
Hintergrund-Refresh dazugekommen sind). **Wie der Rest der Integration
ungetestet** – insbesondere die
Importpfade (`DEVICE_TRIGGER_BASE_SCHEMA`, `event_trigger.CONF_*`) und das
Capabilities-Schema für den Organisations-Filter sollten beim ersten
Durchklicken im Automations-Editor geprüft werden.

**Event umbenannt:** Das gefeuerte Event heißt jetzt `homeassistantalarm_alarm`
(vorher `groupalarm_alarm`), passend zur Domain. Automationen, die bereits
mit dem alten Event-Typ manuell angelegt wurden, müssen entweder auf
`homeassistantalarm_alarm` aktualisiert oder auf den neuen Geräte-Trigger
umgestellt werden.

## Bekannte Lücken / offene TODOs

1. **Ungetestet.** Noch nie in einer laufenden HA-Instanz geladen worden.
   Vor allem prüfen: Config-Flow (inkl. Fehlerfälle 401/404/Timeout),
   SSE-Parsing gegen einen echten oder gemockten Stream, Reconnect-
   Verhalten, Reauth-Flow, Sensor-Attribute, Device-Trigger und den
   periodischen Organisations-Refresh (siehe oben).
2. **Keine automatisierten Tests** (kein `tests/`-Verzeichnis, kein
   pytest-Setup à la `pytest-homeassistant-custom-component`).
3. **Keine CI.** Für HACS-Veröffentlichung sinnvoll: GitHub Actions mit
   `home-assistant/actions/hassfest` und `hacs/action`.
4. **`iot_class: cloud_push`** in `manifest.json` – korrekt, da der
   Normalbetrieb über SSE läuft; falls sich das Verhalten grundlegend
   ändert, diesen Wert mit anpassen (HACS/hassfest validiert das gegen die
   tatsächliche Update-Methode nicht streng, aber es ist die für Nutzer
   sichtbare Einordnung in HA).

## Nächste Schritte (Vorschlag)

1. Lokale HA-Dev-Umgebung aufsetzen (am einfachsten: offizieller
   [HA VS Code Devcontainer](https://developers.home-assistant.io/docs/development_environment)
   oder `pip install homeassistant` in einem venv) und
   `custom_components/homeassistantalarm` einbinden, Config-Flow manuell
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
