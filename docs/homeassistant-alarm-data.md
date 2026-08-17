# HomeAssistant-Integration – strukturierte Alarmdaten (`alarmData`)

Diese Datei beschreibt eine Änderung an der in
[`homeassistant-integration-api.md`](homeassistant-integration-api.md) dokumentierten API: das Feld
`rawAlarm` wurde durch ein neues Feld `alarmData` ersetzt.

## Hintergrund

Bisher enthielt jeder Alarm-Eintrag (SSE `alarm`-Event, `/poll`-Antwort, Testalarm) ein Feld
`rawAlarm` mit den **unveränderten Rohdaten der jeweiligen Alarmquelle**. Diese Rohdaten sehen je
nach `alarmSource` der Organisation komplett unterschiedlich aus:

- `ALAMOS` liefert z.B. `unit`, `keyword`, `optionalContent.latitude`/`longitude`
- `GROUPALARM` liefert u.a. `resources`, `alarmResources`, `startDate`

Eine HomeAssistant-Integration, die daraus etwas Sinnvolles anzeigen wollte (Adresse, Stichwort,
Fahrzeuge, Koordinaten für eine `device_tracker`-Entity o.ä.), musste also für jede Alarmquelle
eigene Parsing-Logik mitbringen und war bei jeder Änderung der Rohdaten-Struktur einer Quelle
angreifbar.

GroupAlarm hat für Webhooks bereits eine Lösung dafür: Beim Anlegen eines Webhooks kann der Nutzer
ein Payload-Template mit Platzhaltern wie `{{code}}`, `{{adresse}}`, `{{fahrzeuge}}` etc. definieren
(siehe `OrganizationManager#buildWebhookPlaceholders`). Der Server übernimmt dabei bereits die ganze
Quellen-abhängige Extraktion.

**`alarmData` nutzt dieselbe Extraktionslogik**, aber ohne Template-Auswahl – die HomeAssistant-
Integration bekommt immer exakt dieselben Felder in derselben Struktur, egal welche Alarmquelle die
Organisation nutzt. Es gibt (bewusst, wie vom Nutzer gewünscht) **keine Möglichkeit, die Felder zu
konfigurieren** – anders als bei Webhooks ist das feste Feldset hier kein Nutzer-Feature, sondern der
einzige Modus.

## Wo `alarmData` erscheint

Überall, wo bisher `rawAlarm` stand – die äußere Struktur (`id`, `organizationUuid`, `organization`,
`message`, `timestamp`) bleibt unverändert:

- `GET /homeassistant/{uuid}/stream` – im `data`-Feld jedes `alarm`-Events
- `GET /homeassistant/{uuid}/poll` – in jedem Eintrag von `alarms[]`
- `POST /api/profile/homeassistant/{uuid}/test` (Testalarm im Profil) – im Response-Feld `alarm`

## Feldreferenz

| Feld         | Typ               | Beschreibung                                                                 |
|--------------|--------------------|--------------------------------------------------------------------------------|
| `code`       | string             | Einsatzstichwort-Code, z.B. `"B2.01"`. Leerer String, wenn nicht ermittelbar.  |
| `stichwort`  | string             | Klartext-Stichwort, z.B. `"Gebäudebrand"`.                                     |
| `adresse`    | string             | Straße/Hausnummer des Einsatzorts.                                            |
| `ort`        | string             | Ort/Stadt des Einsatzorts.                                                     |
| `zusatz`     | string             | Zusatzinformation zur Adresse (z.B. Stockwerk, Hinterhof).                    |
| `fahrzeuge`  | string             | Kommagetrennte Liste alarmierter Fahrzeuge/Ressourcen, z.B. `"HLF 1, DLK 1"`.  |
| `lat`        | number \| `null`   | Breitengrad, `null` wenn die Alarmquelle keine gültigen Koordinaten geliefert hat. |
| `lon`        | number \| `null`   | Längengrad, `null` unter denselben Bedingungen wie `lat`.                      |
| `maps_link`  | string \| `null`   | Fertiger Google-Maps-Link aus `lat`/`lon`, `null` wenn keine Koordinaten vorhanden. |
| `prioritaet` | string             | Dringlichkeit/Severity aus der Alarmquelle, falls vorhanden. Sonst leerer String. |
| `datum`      | string             | Alarmdatum, Format `dd.MM.yyyy` (z.B. `"16.08.2026"`).                        |
| `uhrzeit`    | string             | Alarmzeit, Format `HH:mm` (z.B. `"20:45"`).                                    |
| `test`       | boolean            | **Nur bei Testalarmen vorhanden** (`true`). Fehlt bei echten Alarmen komplett. |

Felder, die sich aus den Rohdaten nicht ermitteln lassen, werden nicht weggelassen, sondern als
leerer String (`""`) bzw. bei `lat`/`lon`/`maps_link` als `null` geliefert – die Integration muss also
nicht zwischen "Feld fehlt" und "Feld ist leer" unterscheiden.

## Vollständiges Beispiel

Ein echter Alarm einer Feuerwehr-Organisation, wie er über `/poll` oder als SSE `alarm`-Event
ankommt:

```json
{
  "id": 143,
  "organizationUuid": "9b1c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b",
  "organization": "Freiwillige Feuerwehr Musterstadt",
  "message": "Alarm von: Freiwillige Feuerwehr Musterstadt\nDatum/Uhrzeit 16.08.2026 20:45\nB2.01 Gebäudebrand - Musterstraße 1, 12345 Musterstadt",
  "timestamp": "2026-08-16T18:45:24.714Z",
  "alarmData": {
    "code": "B2.01",
    "stichwort": "Gebäudebrand",
    "adresse": "Musterstraße 1",
    "ort": "Musterstadt",
    "zusatz": "Hinterhof, 2. Stock",
    "fahrzeuge": "HLF 1, DLK 1, ELW 1",
    "lat": 52.520008,
    "lon": 13.404954,
    "maps_link": "https://www.google.com/maps/search/?api=1&query=52.520008,13.404954",
    "prioritaet": "Hoch",
    "datum": "16.08.2026",
    "uhrzeit": "20:45"
  }
}
```

Ein Alarm ohne bekannte Koordinaten (z.B. weil die Alarmquelle keine `optionalContent.latitude`/
`longitude` mitgeliefert hat):

```json
{
  "id": 146,
  "organizationUuid": "9b1c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b",
  "organization": "DLRG Musterstadt",
  "message": "Alarm von: DLRG Musterstadt\nDatum/Uhrzeit 16.08.2026 21:10\nWasserrettung - Seepromenade 3, 12345 Musterstadt",
  "timestamp": "2026-08-16T19:10:00.512Z",
  "alarmData": {
    "code": "",
    "stichwort": "Wasserrettung",
    "adresse": "Seepromenade 3",
    "ort": "Musterstadt",
    "zusatz": "",
    "fahrzeuge": "RTB 1",
    "lat": null,
    "lon": null,
    "maps_link": null,
    "prioritaet": "",
    "datum": "16.08.2026",
    "uhrzeit": "21:10"
  }
}
```

Ein Testalarm (ausgelöst über den "Testen"-Button im Profil) – erkennbar an `"id": -1` und
`"test": true`:

```json
{
  "id": -1,
  "organizationUuid": "3f2e1d0c-9b8a-7f6e-5d4c-1234567890ab",
  "organization": "Test-Organisation",
  "message": "Alarm von: Test-Organisation\nDatum/Uhrzeit 16.08.2026 22:30\nB2.01 Testalarm - Musterstraße 1, 12345 Musterstadt",
  "timestamp": "2026-08-16T20:30:00.000Z",
  "alarmData": {
    "code": "B2.01",
    "stichwort": "Testalarm",
    "adresse": "Musterstraße 1",
    "ort": "Musterstadt",
    "zusatz": "Hinterhof, 2. Stock",
    "fahrzeuge": "HLF 1, DLK 1, ELW 1",
    "lat": 52.520008,
    "lon": 13.404954,
    "maps_link": "https://www.google.com/maps/search/?api=1&query=52.520008,13.404954",
    "prioritaet": "Hoch",
    "datum": "16.08.2026",
    "uhrzeit": "22:30",
    "test": true
  },
  "test": true
}
```

## Betroffene Server-Dateien (für spätere Nachvollziehbarkeit)

- `src/main/java/org/groupalarm/organization/OrganizationManager.java` – neue Methode
  `buildHomeAssistantAlarmData()`, genutzt von `pushAlarmToHomeAssistant()` (Live-SSE) und
  `getAlarmLogSince()` (`/poll`, rekonstruiert `alarmData` aus dem gespeicherten `raw_alarm` der
  `alarm_log`-Tabelle).
- `src/main/java/org/groupalarm/web/WebManager.java` – `buildHomeAssistantTestAlarm()` liefert
  `alarmData` jetzt im selben Format wie echte Alarme.
- `src/main/resources/public/interface/js/profile.js` – die Live-Vorschau im Testalarm-Modal im
  Profil (`buildHomeAssistantTestAlarmPreview()`) spiegelt das neue Format.

Die Spalte `raw_alarm` in der Datenbanktabelle `alarm_log` bleibt unverändert (weiterhin die echten
Rohdaten der Alarmquelle, z.B. für Debugging) – geändert hat sich nur, was die HomeAssistant-API
daraus nach außen liefert.
