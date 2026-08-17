# Entwicklungsstand & Weiterarbeit

Diese Datei richtet sich an Entwickler (auch an eine zukünftige Claude-Code-
Session ohne Kenntnis dieses Chatverlaufs), die an dieser HACS-Integration
weiterarbeiten. Sie hält fest, was gebaut wurde, warum, und was als
Nächstes ansteht. Nutzerdoku (Installation/Einrichtung) steht in der
[README](../README.md).

## Status (Stand: 2026-08-17)

Erste Live-Tests gegen eine echte Home-Assistant-Instanz liefen erfolgreich
(bis auf den unten beschriebenen State-Length-Bug). Nach wie vor keine
automatisierten Tests/CI (siehe "Bekannte Lücken" unten).

Die Implementierung folgt strikt [`docs/homeassistant-integration-api.md`](homeassistant-integration-api.md)
(Server-API-Referenz, vom Nutzer vorgegeben – nicht verändern, ohne die
Server-Seite abzugleichen) sowie der Ergänzung
[`docs/homeassistant-alarm-data.md`](homeassistant-alarm-data.md) (`rawAlarm`
→ `alarmData`, strukturierte Felder statt quellenabhängiger Rohdaten).

### Bugfix 2026-08-17: State-Length-Limit riss Updates bei langen Alarmtexten ab

Beim ersten Live-Test blieb der Sensor-State bei einer Organisation auf dem
alten Alarm stehen, während andere Organisationen normal aktualisierten –
einziger auffälliger Unterschied war ein deutlich längerer Alarmtext bei der
betroffenen Organisation. Ursache: `sensor.py` hat den kompletten `message`-
Text unverändert als `native_value` (= HA-Entity-State) verwendet. HA
validiert Entity-States serverseitig auf maximal 255 Zeichen
(`homeassistant/core.py`, `State`); bei Überschreitung wirft
`async_write_ha_state()` einen `InvalidStateError`, der State bleibt auf dem
vorherigen Wert stehen – **kein** sichtbarer Fehler in der UI, nur ein Log-
Eintrag.

Erster Fix-Versuch war, `native_value` auf 255 Zeichen zu kappen (`…`-Suffix)
– funktioniert, bleibt aber ein Pflaster, da ein noch längerer Text das
Problem jederzeit wieder auslösen könnte. Stattdessen (Vorschlag des
Nutzers) liefert `native_value` von `GroupAlarmLastAlarmSensor` und
`GroupAlarmOrganizationSensor` jetzt **den Alarm-Zeitpunkt** (`sensor._alarm_timestamp()`,
geparst aus dem ISO8601-Feld `timestamp` via `homeassistant.util.dt.parse_datetime`),
mit `_attr_device_class = SensorDeviceClass.TIMESTAMP`. Das schließt das
255-Zeichen-Problem strukturell aus (ein ISO-Zeitstempel ist nie auch nur
annähernd so lang) und zeigt in der HA-UI direkt an, wann der letzte Alarm
war (lokalisiert/relativ dank `device_class: timestamp`). Der volle
Alarmtext bleibt weiterhin über das Attribut `full_message` verfügbar.

**Migrationshinweis:** Für bestehende Installationen ändert sich damit der
State-Typ dieser beiden Entities von Text auf Timestamp – der History-Graph
zeigt an dieser Stelle einen Bruch (alte String-States, neue Datetime-States).
Unkritisch, da die Integration noch nicht veröffentlicht ist, aber gut zu
wissen, falls es nach einem Update auffällt.

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
- **"Alarmstatus"-Sensoren (`"Alarm"`/`"Kein Alarm"`), einer je Organisation
  plus ein Gesamt-Sensor**, ergänzend zu den bestehenden Text-Sensoren mit
  dem letzten Alarmtext. Nutzerwunsch: eine einfache, dashboard-taugliche
  Zwei-Zustands-Anzeige, die nach einem Alarm 5 Minuten (`ALARM_ACTIVE_DURATION`,
  `const.py`) auf `"Alarm"` bleibt und dann automatisch zurückfällt; ein
  erneuter Alarm während der aktiven Phase setzt den Timer neu (kein
  Aufaddieren). Umgesetzt in `GroupAlarmConnection._activate_alarm_status()`
  über `async_call_later` pro Schlüssel (Organisations-UUID oder
  `ALARM_STATUS_OVERALL_KEY` fürs Gesamt), Reset-Timer wird bei erneutem
  Alarm für denselben Schlüssel gecancelt und neu gestartet.
  `active_alarm_keys` wird bewusst **nicht** persistiert (anders als
  `last_alarm`/`latest_id`) – nach einem HA-Neustart ist "kein Alarm" der
  sichere Default, da nicht mehr rekonstruierbar ist, wie viel vom
  5-Minuten-Fenster noch übrig wäre.
  **Bekannte Einschränkung (nicht neu, betrifft auch das Event):** Der
  Aufhol-Poll beim Start/Reconnect (`_async_catch_up`) kann ältere,
  während einer Downtime aufgelaufene Alarme nachliefern – dafür wird der
  Alarmstatus (wie auch das Event) genauso aktiviert, als wäre der Alarm
  gerade jetzt eingetroffen. Bei einer kurzen Downtime unkritisch, bei einer
  langen mit vielen nachgelieferten Alarmen theoretisch verwirrend
  (Status springt beim Neustart kurz auf "Alarm" für einen Alarm, der
  Stunden zurückliegt). Bisher nicht behoben, da unklar, ob das in der
  Praxis stört – ggf. Alarme mit `timestamp` älter als
  `ALARM_ACTIVE_DURATION` beim Catch-up von der Statusaktivierung
  ausnehmen, falls das im Live-Betrieb auffällt.
- **Verbindungs-Sensor (`binary_sensor.py`, `GroupAlarmConnectivitySensor`),
  `device_class: connectivity`, `entity_category: diagnostic`.** Nutzerwunsch:
  sichtbar haben, ob die Verbindung zum Server noch steht. Bewusst als
  eigene Plattform (`PLATFORMS` in `const.py` um `Platform.BINARY_SENSOR`
  erweitert) statt als weiterer `sensor.py`-Sensor, weil `device_class:
  connectivity` das idiomatische HA-Muster dafür ist (On/Off, passendes
  Icon, lokalisiertes "Verbunden"/"Getrennt" in der UI). Wichtiger
  Unterschied zu allen anderen Entities dieser Integration: Diese Entity
  bindet ihre eigene `available`-Property **nicht** an
  `GroupAlarmConnection.available` (anders als `_GroupAlarmBaseSensor` in
  `sensor.py`) – sie liest den Wert stattdessen nur als `is_on`. Würde sie
  wie die Alarm-Sensoren bei Verbindungsverlust selbst auf "nicht verfügbar"
  gehen, gäbe es keine Entity mehr, die überhaupt anzeigt, *dass* die
  Verbindung weg ist. Hört auf dasselbe `SIGNAL_AVAILABILITY`-Signal wie die
  bestehenden Sensoren, um sich bei Statusänderungen neu zu rendern.

## Dateiübersicht

```
custom_components/homeassistantalarm/
├── api.py             # GroupAlarmClient: /status, /poll, /stream (SSE-Parsing)
├── coordinator.py     # GroupAlarmConnection: Aufhol-Poll + Stream-Loop, Dedupe, Persistenz
├── config_flow.py     # Setup-Dialog + Reauth-Flow
├── device_trigger.py  # Geraete-Trigger "Neuer Alarm" (+ optionaler Organisations-Filter) fuers Automations-UI
├── __init__.py         # async_setup_entry/async_unload_entry, runtime_data
├── sensor.py            # Letzter-Alarm-Sensor + Alarmstatus-Sensor, je gesamt und pro Organisation
├── binary_sensor.py      # Verbindungs-Sensor (device_class connectivity), immer verfuegbar
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

1. **Neue Sensoren/Änderungen vom 2026-08-17 noch nicht live getestet.** Der
   Grundbetrieb (Config-Flow, Stream, Reconnect) sowie die Text-Sensoren
   sind im Live-Betrieb bestätigt; noch ungetestet sind:
   `GroupAlarmOverallStatusSensor`/`GroupAlarmOrganizationStatusSensor`
   (5-Minuten-Timer, Reset-Verhalten bei Folgealarmen), die Umstellung von
   `native_value` auf `device_class: timestamp` bei den Letzter-Alarm-
   Sensoren, die Umstellung von `rawAlarm` auf `alarmData` in
   `_alarm_attributes()`, sowie der neue `GroupAlarmConnectivitySensor`
   (`binary_sensor.py`) inkl. der neuen `Platform.BINARY_SENSOR`-Plattform.
   Device-Trigger ebenfalls weiterhin ungetestet (Importpfade,
   Capabilities-Schema).
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
