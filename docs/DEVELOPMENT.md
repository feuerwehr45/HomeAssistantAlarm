# Entwicklungsstand & Weiterarbeit

Diese Datei richtet sich an Entwickler (auch an eine zukünftige Claude-Code-
Session ohne Kenntnis dieses Chatverlaufs), die an dieser HACS-Integration
weiterarbeiten. Sie hält fest, was gebaut wurde, warum, und was als
Nächstes ansteht. Nutzerdoku (Installation/Einrichtung) steht in der
[README](../README.md).

## Status (Stand: 2026-11-08, Version 0.4.0, Branch `alarmserver`)

Umgestellt vom abgeschalteten GroupAlarm-Server (V2) auf den **AlarmServer**
(`https://alarm.feuerwehr45.de`, Repository `GroupAlarmV2`, Paket
`channel.homeassistant`). Auftrag: [`umstellung-alarmserver.md`](umstellung-alarmserver.md),
Ergebnis: [`bericht-umstellung-alarmserver.md`](bericht-umstellung-alarmserver.md).
Automatisierte Tests gibt es jetzt (siehe "Tests"), CI noch nicht.

### Umstellung auf den AlarmServer (0.4.0) – Entscheidungen

- **Erstabgleich ohne Auslösen.** `GroupAlarmConnection._latest_id` ist
  `None`, solange es keinen Stand für diesen Server gibt. Dann macht
  `_async_initial_sync()` einen `/poll` ohne `since_id`, übernimmt `latestId`
  und schreibt die gelieferten Alarme still in die Sensoren (jede Org bekommt
  ihren letzten, gesamt den neuesten) – kein Event, kein Alarmstatus.
- **Speicher an Server gebunden.** Der Store enthält zusätzlich `base_url`
  und `uuid`. Passen sie beim Start nicht zum Entry (auch: Store aus 0.3.0
  ohne diese Felder), wird er verworfen und neu abgeglichen. Grund: Die IDs
  des AlarmServers sind kleiner als die aus V2; eine alte, größere ID würde
  `/poll` still blockieren (`latestId = max(since_id, neuester)`).
  Das ersetzt auch das „Speicher zurücksetzen“ beim Reconfigure: Ändern sich
  Server oder UUID, greift die Bindung; ändert sich nur der Key, bleibt der
  Stand erhalten (sonst gingen Alarme aus dem Umkonfigurier-Fenster verloren).
- **Alte Alarme.** Option `max_age_minutes` (Default 15 wie
  `alarm.maxAgeMinutes` im AlarmServer, 0 = aus). Ältere Alarme aus `/poll`
  oder `/stream` gehen nur in die Sensoren. Beim Stream wird das als Warnung
  geloggt (Hinweis auf falsch gehende Uhr), beim Aufholen als Info.
  Gemessen wird gegen `timestamp` (Eingang beim Server).
- **Probelauf** (`id <= 0`, zufällige `organizationUuid`): nicht
  entdoppelt, nie `latest_id`, nicht in `last_alarm_by_org` (außer die Org
  ist zufällig abonniert). Er feuert das Event und aktualisiert die
  Gesamt-Sensoren.
- **`test`.** `is_test_alarm()` liest oben und in `alarmData`; das Event
  bekommt `test` immer auf oberster Ebene, die Sensoren als Attribut. Ob
  Test-Alarme den Alarmstatus setzen: Option `test_activates_status`,
  **Default an** – mit Thomas am 2026-11-08 so festgelegt (Automationen auf
  den Status lassen sich so testen; wer nicht will, filtert über `test`).
- **Fehler-Events.** `api.classify_stream_error()` ordnet den Text des
  SSE-`error`-Events zu: Sperre („Zu viele Fehlversuche…“, aus
  `HomeAssistantAccess.authenticate`) → `GroupAlarmRateLimitError`, warten
  (`RATE_LIMIT_WAIT` = 60 s, das Event hat kein Retry-After); „Ungültiger
  API-Key“/„Header fehlt“ → Reauth; „Verbindung nicht gefunden“/„Ungültige
  UUID“ → Reauth; unbekannter Text → `GroupAlarmStreamRejectedError`, normaler
  Backoff, der nächste Aufhol-`/poll` klassifiziert dann über den HTTP-Status.
  So startet ein geänderter Servertext nie fälschlich einen Reauth. `/poll`
  und `/status` mit `429` → `Retry-After` abwarten, ebenfalls kein Reauth.
  Beim Setup wird 429 zu `ConfigEntryNotReady`.
- **`notification`** → HA-Event `homeassistantalarm_notification` mit
  `type`, `message`, `timestamp`. Unbekannte Events werden geloggt (debug)
  und ignoriert.
- **Reconfigure-Flow** (`async_step_reconfigure`): Basis-URL, UUID, Key am
  bestehenden Entry ändern, Prüfung über `/status`, `unique_id` wird auf die
  neue UUID gesetzt (Abbruch, falls ein anderer Entry sie schon hat). Die
  `entry_id` bleibt → Gesamt-Entities behalten Unique-IDs und Entity-IDs.
- **Org-Geräte.** Bis 0.3.0 hingen alle Entities an einem Gerät. Jetzt hat
  jede Organisation ein eigenes Gerät (`identifiers = (DOMAIN,
  f"{entry_id}_{org_uuid}")`, `via_device` = Verbindungsgerät).
  `async_remove_config_entry_device` erlaubt das Löschen, wenn die Org nicht
  mehr abonniert ist; das Verbindungsgerät ist nie löschbar. Der
  Org-Refresh ersetzt die Org-Liste jetzt komplett (vorher nur Ergänzung),
  damit abgemeldete Orgs herausfallen. Die Org-Entities aus 0.3.0 hängen
  noch am Verbindungsgerät; nach der Umstellung sind sie „nicht mehr
  bereitgestellt“ und in den Entity-Einstellungen löschbar.
- **Ein Stream je Entry.** `async_stream()` schließt die HTTP-Antwort im
  `finally` explizit (`resp.close()`), `async_stop()` wartet auf das Ende
  des Tasks. Reload/Reconfigure entladen vollständig, bevor neu verbunden
  wird. Test: `test_reload_keeps_a_single_stream`.
- **Server-URL wird gekürzt** (`api.normalize_base_url`): Das AlarmServer-Profil zeigt als
  „Basis-URL“ `https://host/api/homeassistant/<uuid>` (`ContactController.access()`), nicht den
  Server-Teil, wie es `homeassistant-integration-api.md` beschreibt. Eingefügt ergab das
  `…/<uuid>/api/homeassistant/<uuid>/status`, und Spring Security antwortet darauf mit 401, was
  wie ein falscher Key aussah (Fund bei Thomas' Abnahme, 2026-11-08). Alles ab `/api` wird
  abgeschnitten, fehlendes Schema wird `https`. Auch im Client angewandt, damit bestehende
  Einträge mit langer URL funktionieren.
- **Optionen** werden live aus `entry.options` gelesen; kein Update-Listener,
  kein Reload, kein neuer Stream.
- **Erweiterbarkeit (Monitore, M10b).** Neue Plattformen: `PLATFORMS` in
  `const.py` + Modul. Neue Geräte: eigenes Identifier-Schema analog
  `org_device_identifier()` und in `async_remove_config_entry_device`
  berücksichtigen.

## Tests

`tests/` mit `pytest-homeassistant-custom-component` und einem Fake-Server
(`tests/conftest.py`, `FakeAlarmServer`, aiohttp), der `/status`, `/poll` und
`/stream` des AlarmServers nachbildet (inkl. 401/404/429 und `error`-Events).

Lokal gibt es kein Python, daher in Docker (`python:3.13`):

```bash
sh run_tests.sh            # alle Tests
sh run_tests.sh -k probe   # Auswahl, beliebige pytest-Argumente
```

`run_tests.sh` kopiert das Repo in den Container (schreibgeschützt
eingehängt, kein `__pycache__` im OneDrive-Ordner) und nutzt das Volume
`ha-pip-cache`. Gepinnt ist `pytest-homeassistant-custom-component==0.13.316`
(= HA 2026.2.3), die letzte Version für Python 3.13; neuere HA-Versionen
brauchen Python 3.14 – dann Image und Pin gemeinsam anheben.

Abgedeckt: Erstabgleich ohne Event, Aufholen ab gespeicherter ID, Verwerfen
eines fremden Speicherstands (0.3.0-Format und andere URL), alte Alarme ohne
Event (Poll und Stream, Option 0), zwei Probeläufe hintereinander,
Test-Alarm-Option, `error`-Event Anmeldung (Reauth) vs. Sperre (kein
Reauth), `/poll` 429 mit `Retry-After`, `notification`, Reload hält genau
einen Stream, Dedupe Stream/Poll, Config-Flow-Default, Reconfigure behält
Entities und setzt den Speicher zurück, Reconfigure auf belegte UUID,
Options-Flow, Löschen alter Org-Geräte.

Nicht abgedeckt: Device-Trigger, 5-Minuten-Reset des Alarmstatus,
Org-Refresh per Timer.

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
  Seit 0.4.0 aktivieren nachgeholte Alarme, die älter als
  `max_age_minutes` sind, weder Event noch Alarmstatus (siehe oben).
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
├── api.py             # GroupAlarmClient: /status, /poll, /stream (SSE-Parsing, Fehler-Klassifizierung)
├── coordinator.py     # GroupAlarmConnection: Aufhol-Poll + Stream-Loop, Dedupe, Persistenz
├── config_flow.py     # Setup, Reconfigure, Reauth, Options-Flow
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
2. **Tests nur für die Verbindungslogik und die Flows** (siehe "Tests");
   Device-Trigger und Timer-Pfade ungetestet. Getestet nur gegen HA
   2026.2.3; Mindestversion in `hacs.json` bleibt 2024.12.0 (Reconfigure-
   Flow und `OptionsFlow.config_entry` gibt es seit 2024.11), dort aber nicht
   ausprobiert.
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
