# Bericht: Umstellung der Integration auf den AlarmServer

Stand: 2026-11-08, Branch `alarmserver`, Version 0.4.0. Auftrag:
[`umstellung-alarmserver.md`](umstellung-alarmserver.md). Details und
Begründungen: [`DEVELOPMENT.md`](DEVELOPMENT.md), Abschnitt „Umstellung auf den
AlarmServer (0.4.0)“.

## Was geändert wurde

| Aufgabe | Umsetzung |
|---|---|
| 1 Basis-URL | Default `https://alarm.feuerwehr45.de`, im Config-Flow vorbelegt, änderbar |
| 2 Erstabgleich | Ohne gespeicherten Stand: `/poll` ohne `since_id`, `latestId` übernehmen, Alarme still in die Sensoren, kein Event, kein Alarmstatus |
| 3 Speicher an Server | Store enthält `base_url` + `uuid`; Abweichung (auch Store aus 0.3.0) → verwerfen, Erstabgleich |
| 4 Alte Alarme | Option „Höchstalter für Events“ (Default 15 min, 0 = aus); ältere Alarme aus Poll und Stream nur in die Sensoren; Stream-Fall mit Warnung im Log |
| 5 Probelauf | `id <= 0` nicht entdoppelt, nie `latest_id`; zufällige Org nicht in `last_alarm_by_org`; Event + Gesamt-Sensoren |
| 6 `test` | Im Event (oberste Ebene) und als Sensor-Attribut. **Mit Thomas festgelegt:** Test-Alarme setzen den Alarmstatus, Option „Test-Alarme setzen den Alarmstatus“, Default an |
| 7 Fehler-Event | Text-Klassifizierung nach `HomeAssistantAccess`: Sperre → warten (60 s), kein Reauth; Key/UUID falsch → Reauth; unbekannt → Backoff, der nächste `/poll` entscheidet per HTTP-Status. `/poll`/`/status` 429 → `Retry-After` |
| 8 `notification` | HA-Event `homeassistantalarm_notification` mit `type`, `message`, `timestamp`; unbekannte Events ignoriert |
| 9 Reconfigure | `async_step_reconfigure` für URL, UUID, Key; Prüfung über `/status`; `unique_id` → neue UUID; `entry_id` bleibt, damit Gesamt-Entities und IDs. Neu: ein Gerät je Organisation, nicht mehr abonnierte lassen sich löschen (`async_remove_config_entry_device`) |
| 10 Ein Stream | Stream-Antwort wird im `finally` geschlossen, Unload wartet auf das Ende; getestet mit dreifachem Reload |
| 11 Branding | Texte in `strings.json`, `translations/{de,en}.json`, README auf AlarmServer/HomeAssistantAlarm |
| 12 Tests | `tests/` mit Fake-AlarmServer, 18 Tests, `sh run_tests.sh` (Docker `python:3.13`) |
| 13 Doku | README (Einrichtung, Umstellung, Optionen, Events, Beispiel-Automation mit Test-Filter), DEVELOPMENT.md |

`manifest.json` → `0.4.0`. `hacs.json` unverändert (`2024.12.0`; Reconfigure-Flow
und `OptionsFlow.config_entry` gibt es seit 2024.11).

**Abweichung vom Auftrag:** „Speicher zurücksetzen“ beim Reconfigure geschieht über
die Bindung an URL + UUID, nicht bedingungslos. Ändert sich nur der API-Key, bleibt
der Stand erhalten; sonst würden Alarme aus dem Umkonfigurier-Fenster nicht
nachgeholt.

**Hinweis zu den „alten Org-Geräten“:** Bis 0.3.0 gab es keine Org-Geräte, alle
Entities hingen am Verbindungsgerät. Die Org-Entities aus V2 sind nach der Umstellung
„nicht mehr bereitgestellt“ und in den Entity-Einstellungen löschbar. Ab 0.4.0 hat
jede Org ein eigenes Gerät, das sich löschen lässt, sobald die Org nicht mehr
abonniert ist. Die neuen Org-Entities bekommen bei gleichem Org-Namen ein `_2`, weil
die alten IDs noch belegt sind (nach dem Löschen umbenennbar; steht in der README).

## Testergebnisse (automatisiert)

`sh run_tests.sh`: **18 passed** (HA 2026.2.3, Python 3.13), dreimal hintereinander
stabil, keine Warnungen.

- Erstabgleich ohne Event; Folgealarm feuert
- Aufholen ab gespeicherter ID
- Fremder Speicherstand (0.3.0-Format, andere URL) wird verworfen
- Alte Alarme ohne Event (Poll, Stream), Option 0 schaltet ab
- Zwei Probeläufe hintereinander: zwei Events, keine Phantom-Org, `latest_id` unverändert
- Test-Alarm einer Org: Option aus/an
- `error`-Event „Ungültiger API-Key“ → Reauth; Sperre → kein Reauth, Neuverbindung danach
- `/poll` 429: wartet `Retry-After`, kein Reauth
- `notification`-Event; unbekanntes Event ignoriert
- Reload: genau ein offener Stream
- Dedupe Stream/Poll
- Config-Flow (Default-URL, falscher Key, 429), Reconfigure behält Entities und setzt den
  Speicher zurück, Reconfigure auf belegte UUID bricht ab, Options-Flow, Löschen alter
  Org-Geräte

Nicht automatisiert getestet: Device-Trigger, 5-Minuten-Reset des Alarmstatus,
Org-Refresh per Timer, Verhalten unter HA 2024.12.

## Abnahme mit Thomas

Thomas testet seit 2026-11-08 mit `v0.4.0-beta.1` (HACS-Pre-Release, Branch-Auswahl liess HACS
nicht zu). Bisher bestätigt: Umkonfigurieren klappt (mit Server-URL ohne `/api/...`, siehe
Offene Punkte), ein Probelauf kam an. Organisationen, die erst nach dem Umkonfigurieren
abonniert wurden, erschienen nach „Neu laden“ (sonst nach spätestens 15 Minuten). Von Claude
wurde an Thomas' HomeAssistant und am AlarmServer nichts geändert.

- [ ] Umkonfigurieren des bestehenden Eintrags klappt, die Gesamt-Entities behalten ihre IDs
- [ ] Nach dem Umkonfigurieren feuert kein Event für alte Alarme, die Sensoren zeigen den letzten
- [ ] Stream bleibt über mehrere Heartbeats stehen; nach HA-Neustart Neuverbindung + Aufholen
- [ ] Probelauf zweimal hintereinander: beide als Event (`id: -1`, `test: true`)
- [ ] Test-Alarm „nur an mich“: Event mit eigener `id`, `test: true`, Alarmstatus „Alarm“
- [ ] Weiterer `/poll` liefert ihn nicht noch einmal
- [ ] AlarmServer zeigt genau eine offene Verbindung
- [ ] Alte Org-Geräte/Entities aus V2 lassen sich löschen

## Offene Punkte

- **Gefunden bei der Abnahme (beta.1):** Die im Profil angezeigte „Basis-URL“ enthält
  `/api/homeassistant/<uuid>`. Als Server-URL eingetragen, führte das zu „Ungültiger API-Key“
  (401 von Spring Security für den doppelten Pfad). Die Integration kürzt die URL jetzt selbst
  (`normalize_base_url`, auf `alarmserver` gepusht, noch nicht als Beta veröffentlicht). Für den
  AlarmServer: Anzeige im Profil oder `docs/homeassistant-integration-api.md` angleichen.

- Abnahme (oben), danach Merge nach `main`, Tag `v0.4.0`, GitHub-Release – nur nach
  Thomas' OK. `gh` ist auf dem Rechner nicht installiert; für `gh release create`
  installieren oder das Release in der GitHub-Oberfläche anlegen.
- CI (hassfest, HACS-Action, Tests) fehlt weiterhin.
- Später: Alarm-Monitore (M10b) als eigene Geräte/`button`-Plattform.
