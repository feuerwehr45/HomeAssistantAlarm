# Auftrag: Integration auf den AlarmServer umstellen

Dieser Auftrag ist für eine Claude-Code-Session in diesem Repository (HomeAssistantAlarm) gedacht und
ohne weiteren Kontext verständlich. So startest du die Session:

> Lies `docs/umstellung-alarmserver.md`, `docs/DEVELOPMENT.md`, `docs/homeassistant-integration-api.md`
> und `docs/homeassistant-alarm-data.md` und setze die Umstellung um. Frag mich, bevor du etwas an
> meiner laufenden HomeAssistant-Instanz änderst.

---

## Ausgangslage

Die Integration wurde im August 2026 gegen den alten GroupAlarm-Server (V2) gebaut (Version 0.3.0). V2
ist abgeschaltet. Sein Nachfolger **AlarmServer** läuft produktiv unter `https://alarm.feuerwehr45.de`
(Spring Boot, eigenes Repository unter
`C:\Users\teune\OneDrive\Dokumente\Developing\GroupAlarmV2`, nur lesen).

Die HomeAssistant-API des AlarmServers ist **bewusst kompatibel** zu V2: gleiche Pfade
(`/api/homeassistant/{uuid}/status`, `/poll`, `/stream`), gleiche Anmeldung
(`Authorization: Bearer <api_key>`), gleiche Feldnamen. Die beiden Referenzen in `docs/` sind auf den
Stand des AlarmServers aktualisiert (aus dessen Repository übernommen). Lies die Unterschiede mit
`git diff docs/` nach.

Der Code ist trotzdem nicht ohne Änderungen nutzbar. Bei der Durchsicht sind diese Punkte aufgefallen:

| Nr. | Stelle | Problem |
|---|---|---|
| 1 | `const.py` | `DEFAULT_BASE_URL` zeigt noch auf `https://api.groupalarm.org` |
| 2 | `coordinator.py`, `_async_catch_up` | Bei einem neuen Config-Entry ist `latest_id = 0`. Der erste `/poll` liefert dann die letzten bis zu 50 Alarme, und **jeder löst ein Event aus** (Automationen, Alarmstatus). Laut API-Referenz soll der erste Abruf nur den Stand übernehmen |
| 3 | `coordinator.py`, `_handle_alarm` | Der Probelauf aus dem Profil kommt immer mit `id: -1`. Die Entdoppelung über `_recent_ids` verschluckt deshalb jeden **zweiten** Probelauf |
| 4 | `coordinator.py` | Die Alarm-IDs des AlarmServers sind **kleiner** als die aus V2. Eine gespeicherte alte ID würde `/poll` blockieren, ohne Fehlermeldung (`latestId` ist der größere Wert aus `since_id` und dem neuesten Alarm). Der Speicher ist an die `entry_id` gebunden; bei einem neuen Entry tritt das nicht auf, bei einer Umkonfiguration des bestehenden schon |
| 5 | `coordinator.py`, Aufholen | Nach einem längeren Ausfall von HomeAssistant holt `/poll` alte Alarme nach und feuert für jeden ein Event. Licht oder Sirene für einen Alarm von vor zwei Stunden ist unerwünscht |
| 6 | `api.py`, `async_stream` | Jedes `error`-Event wird als Anmeldefehler behandelt und startet Reauth. Der AlarmServer meldet auch die **Sperre nach zu vielen Fehlversuchen** (429) als `error`-Event. Das darf keinen Reauth auslösen, sondern muss warten (`Retry-After` bei `/poll` bzw. Text des Events prüfen; im Server-Code `HomeAssistantSseHub`/`HomeAssistantAccess` nachsehen, was genau gesendet wird) |
| 7 | Probelauf | Er kommt mit einer **zufälligen** `organizationUuid`. Sie landet in `last_alarm_by_org` und im Speicher und bleibt dort für immer |
| 8 | Neues Event `notification` | Der AlarmServer sendet News und Systemmeldungen als `notification`-Event (nur Stream). Bisher ignoriert |
| 9 | Umstellung selbst | Löscht Thomas den alten Entry und legt einen neuen an, können sich Entity-IDs ändern und seine Automationen brechen |

## Aufgaben

Arbeite auf einem Branch `alarmserver` von `main`. Version danach `0.4.0` (`manifest.json`).

1. **Basis-URL**: Default `https://alarm.feuerwehr45.de`. Im Config-Flow vorbelegt, änderbar.
2. **Erstabgleich ohne Auslösen** (Punkt 2): Gibt es keinen gespeicherten Stand, den ersten `/poll` ohne
   `since_id` machen, `latestId` übernehmen und den neuesten Alarm still in die Sensoren übernehmen
   (damit sie etwas anzeigen), aber **kein Event feuern und keinen Alarmstatus aktivieren**.
3. **Speicher an Server binden** (Punkt 4): Im Speicher zusätzlich Basis-URL und UUID ablegen. Weichen sie
   beim Start ab, den gespeicherten Stand verwerfen und wie in Aufgabe 2 neu abgleichen.
4. **Alte Alarme nicht auslösen** (Punkt 5): Beim Aufholen per `/poll` Alarme, deren `timestamp` älter
   als N Minuten ist (Option im Options-Flow, Default 15, wie `alarm.maxAgeMinutes` im AlarmServer),
   nur in die Sensoren übernehmen, ohne Event und ohne Alarmstatus. Über den Stream gilt dasselbe
   (schützt bei falsch gehender Uhr nicht, also großzügig bleiben und im Log vermerken).
5. **Probelauf** (Punkte 3 und 7): `id <= 0` nicht entdoppeln und nicht als `latest_id` verwenden. Den
   Probelauf nicht in `last_alarm_by_org` speichern, wenn die `organizationUuid` keiner abonnierten Org
   gehört. Er feuert das Event und aktualisiert die Gesamt-Sensoren.
6. **`test`-Feld**: Jeder Alarm hat `test` (oben und in `alarmData`). Als Attribut an den Sensoren und im
   Event mitgeben. Ob Test-Alarme den **Alarmstatus** (binary_sensor / „Alarm“) aktivieren, mit Thomas
   festlegen und als Option anbieten (Vorschlag: ja, damit sich Automationen testen lassen; die
   Automationen filtern selbst über `test`). Nicht stillschweigend ändern.
7. **Fehler-Event richtig unterscheiden** (Punkt 6): Nur echte Anmeldefehler starten Reauth. Sperre bzw.
   Rate-Limit: warten und mit Backoff weiter. `/poll` mit `429`: `Retry-After` beachten.
8. **`notification`-Event** (Punkt 8): als eigenes HA-Event `homeassistantalarm_notification` mit Typ,
   Text und Zeit feuern. Kein Sensor nötig. Unbekannte Events weiterhin ignorieren.
9. **Umkonfigurieren statt neu anlegen** (Punkt 9): Einen Reconfigure-Flow (`async_step_reconfigure`)
   ergänzen, mit dem sich Basis-URL, UUID und API-Key am **bestehenden** Entry ändern lassen (Prüfung über
   `/status`, `unique_id` auf die neue UUID setzen, Speicher zurücksetzen). So bleiben die
   Gesamt-Entities und ihre IDs erhalten. Entities je Organisation hängen an der Org-UUID; die
   Organisationen haben im AlarmServer neue UUIDs, es entstehen also neue Org-Entities. Die alten
   Org-Geräte sollen sich in der HA-Oberfläche löschen lassen (`async_remove_config_entry_device`
   erlauben, wenn die Org nicht mehr abonniert ist).
10. **Höchstens ein Stream je Entry**: Der AlarmServer erlaubt 3 gleichzeitige Streams je Verbindung, ein
    vierter verdrängt den ältesten. Sicherstellen, dass Reload/Reconfigure den alten Stream sauber
    beendet, bevor ein neuer aufgeht.
11. **Branding und Texte**: Reste von „GroupAlarm“ in Texten für Nutzer (`strings.json`, Übersetzungen,
    README) auf „AlarmServer“ bzw. „HomeAssistantAlarm“ ändern. Klassennamen im Code dürfen bleiben.
12. **Tests**: Es gibt noch keine. Mit `pytest-homeassistant-custom-component` und einem kleinen
    Fake-Server (aiohttp) die Logik aus 2 bis 9 testen: Erstabgleich ohne Event, Server-Wechsel setzt
    zurück, alte Alarme ohne Event, zwei Probeläufe hintereinander, `error`-Event Anmeldung vs. Sperre,
    `notification`, Reconfigure behält Entities. Läuft Python auf dem PC nicht passend, in einem
    Docker-Container (`python:3.13`) testen. Einen Befehl dafür in `docs/DEVELOPMENT.md` aufnehmen.
13. **Doku**: README (Einrichtung mit dem AlarmServer, Umstellung bestehender Installationen, Optionen,
    Events mit Beispiel-Automation, die Test-Alarme ausfiltert), `docs/DEVELOPMENT.md` (Stand, Entscheidungen,
    Tests).

## Was Thomas im AlarmServer tut

1. Anmelden unter `https://alarm.feuerwehr45.de`.
2. **Profil → Kontakt hinzufügen → HomeAssistant**, Namen vergeben. Basis-URL, UUID und API-Key werden
   angezeigt, der Key **nur dieses eine Mal**.
3. **Abos**: Die neue Verbindung bei den gewünschten Organisationen einschalten. Ohne Abo ist
   `subscribedOrganizations` leer und es kommen keine Alarme.
4. In HomeAssistant den bestehenden Eintrag **umkonfigurieren** (Aufgabe 9) und die Daten selbst eingeben.

## Regeln für diese Session

- **Keine Zugangsdaten in den Chat, ins Repository oder ins Log.** UUID und API-Key gibt Thomas selbst in
  HomeAssistant ein. Für `curl`-Tests nimmt er sie aus einer lokalen, ignorierten Datei oder tippt sie
  selbst.
- **Der AlarmServer ist produktiv.** Dort nichts ändern, nur lesend abfragen. Den Code des AlarmServers
  darfst du zum Nachschlagen lesen, aber nicht ändern.
- **Thomas' HomeAssistant**: Nichts installieren, neu starten oder umkonfigurieren ohne sein OK.
- **Automationen**: Hängen an den Events Licht, Sirene oder Benachrichtigungen, vor jedem Test mit Thomas
  klären, was ausgelöst werden darf.
- Git: committen und den Branch pushen ist erlaubt. Merge nach `main`, Tag `v0.4.0` und GitHub-Release
  nur nach Thomas' OK (siehe „Installation über HACS“). Kein Force-Push, keine Tags löschen.

## Installation über HACS (so installiert Thomas)

Thomas installiert die Integration über **HACS** aus dem GitHub-Repository
`feuerwehr45/HomeAssistantAlarm` (bisherige Tags `v0.1.0` bis `v0.3.0`). Daraus folgt:

1. **Testen vor dem Release**: Branch `alarmserver` pushen. Thomas wählt in HACS bei der Integration
   „Erneut herunterladen“ und dort den Branch `alarmserver` als Version, startet HomeAssistant neu und
   konfiguriert um. Schreib ihm diese Schritte kurz auf, wenn es so weit ist. Lässt HACS die Auswahl des
   Branches nicht zu, mit Thomas eine Vorabversion (`v0.4.0-beta.1`, als Pre-Release auf GitHub) abstimmen.
2. **Release**: Nach der Abnahme und Thomas' OK `alarmserver` nach `main` mergen, Tag `v0.4.0` setzen
   und ein **GitHub-Release** mit kurzen Release-Notes anlegen (`gh release create`), damit HACS das Update
   anbietet. Die Version in `manifest.json` muss zum Tag passen.
3. Release-Notes enthalten einen Hinweis für bestehende Installationen: „Eintrag umkonfigurieren statt
   neu anlegen“ und dass alte Org-Geräte gelöscht werden können.
4. `hacs.json`: Mindestversion von HomeAssistant prüfen, falls neue APIs (Reconfigure-Flow) eine höhere
   brauchen, und anpassen.

## Prüfen mit Thomas

```bash
# Zugangsdaten: 200 mit Verbindungsname und den abonnierten Organisationen
curl -s -H "Authorization: Bearer <KEY>" https://alarm.feuerwehr45.de/api/homeassistant/<UUID>/status

# Stand ohne since_id: liefert latestId
curl -s -H "Authorization: Bearer <KEY>" https://alarm.feuerwehr45.de/api/homeassistant/<UUID>/poll

# Stream: zuerst connected, danach etwa alle 25 s ein Heartbeat. Danach beenden (max. 3 Streams)
curl -sN -H "Authorization: Bearer <KEY>" https://alarm.feuerwehr45.de/api/homeassistant/<UUID>/stream
```

Abnahme:
- [ ] Umkonfigurieren des bestehenden Eintrags klappt, die Gesamt-Entities behalten ihre IDs
- [ ] Nach dem Umkonfigurieren feuert **kein** Event für alte Alarme, die Sensoren zeigen den letzten
- [ ] Der Stream bleibt über mehrere Heartbeats stehen; nach einem Neustart von HomeAssistant verbindet er
      sich neu und holt per `/poll` auf
- [ ] Probelauf aus dem Profil des AlarmServers zweimal hintereinander: beide kommen als Event an
      (`id: -1`, `test: true`)
- [ ] Test-Alarm „nur an mich“ einer Organisation (löst Thomas in der Verwaltung aus): Event mit eigener
      `id` und `test: true`, Alarmstatus wie vereinbart
- [ ] Danach liefert ein weiterer `/poll` ihn nicht noch einmal
- [ ] Der AlarmServer zeigt unter **Verwaltung → System → HomeAssistant** genau eine offene Verbindung
- [ ] Alte Org-Geräte aus V2 lassen sich in HomeAssistant löschen

## Fehlersuche

| Beobachtung | Ursache |
|---|---|
| `401` bei `/status` oder `/poll` | Key falsch, Header fehlt, oder das Konto ist deaktiviert |
| `404` | UUID unbekannt, etwa noch die alte |
| `/stream` antwortet `200`, aber sofort ein `error`-Event | Falsche Anmeldung oder Sperre. Der Stream meldet das wie V2 als Event, nicht als HTTP-Status |
| `429` mit `Retry-After` | Mehr als 20 Fehlversuche je Minute und IP. Wartezeit einhalten |
| Status in Ordnung, aber keine Alarme | Abo der Verbindung fehlt, oder eine alte ID ist noch gespeichert |

## Später (nicht Teil dieses Auftrags)

Mit dem AlarmServer-Meilenstein M10b kommen **Alarm-Monitore** dazu: je Monitor ein Sensor (Ruhe/Alarm)
und ein Knopf „Monitor zurücksetzen“. Dafür gibt es dann einen eigenen Auftrag
(`docs/homeassistant-monitor.md` im AlarmServer-Repository). Baue die Integration so, dass sich weitere
Geräte und Plattformen (z.B. `button`) ohne Umbau ergänzen lassen.

## Am Ende

Bericht `docs/bericht-umstellung-alarmserver.md`: was geändert wurde, Testergebnisse, welche
Abnahmepunkte mit Thomas gelaufen sind, offene Punkte. Thomas überträgt das Ergebnis in
`docs/12-inbetriebnahme.md` des AlarmServers, Abschnitt „HomeAssistant“.
