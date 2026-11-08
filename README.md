# HomeAssistantAlarm

**HomeAssistantAlarm** ist eine [HACS](https://hacs.xyz/)-Custom-Integration,
die Alarme vom **AlarmServer** (`https://alarm.feuerwehr45.de`, siehe
[`docs/homeassistant-integration-api.md`](docs/homeassistant-integration-api.md))
in Home Assistant empfängt – als Event, mit dem sich beliebige Automationen
auslösen lassen ("Licht an bei Einsatz", Push-Benachrichtigung, Sirene, ...),
und als Sensor-Entities fürs Dashboard/History.

## Funktionsweise

- **Home Assistant ist der Client.** Es wird kein eingehender Webhook
  benötigt – die Integration baut die Verbindung von HA aus zum AlarmServer
  auf.
- **Echtzeit-Stream (SSE) + Aufhol-Poll**: Nach dem Start (und nach jedem
  Verbindungsabbruch) holt die Integration per `/poll` zunächst verpasste
  Alarme nach, danach hält sie den `/stream`-Kanal offen, über den neue
  Alarme mit < 1s Latenz eintreffen. Bricht der Stream ab, wird mit
  exponentiellem Backoff (1s bis max. 30s) automatisch neu verbunden.
- **Kein Nachfeuern alter Alarme**: Beim allerersten Abgleich (neuer Eintrag,
  anderer Server oder andere Verbindung) übernimmt die Integration nur den
  Stand und füllt die Sensoren – ohne Events. Alarme, die beim Aufholen
  älter als 15 Minuten sind (einstellbar), aktualisieren ebenfalls nur die
  Sensoren.
- **Dedupe über `id`**: Alarme, die sowohl per Stream als auch beim
  Aufhol-Poll geliefert werden, werden nur einmal verarbeitet.
- Die zuletzt verarbeitete Alarm-`id` wird zusammen mit Server und
  Verbindungs-UUID gespeichert, damit nach einem Neustart von Home Assistant
  nahtlos weitergemacht wird.

## Installation

### Über HACS

1. HACS → Integrationen → Menü (⋮) → *Benutzerdefinierte Repositories*.
2. `https://github.com/feuerwehr45/HomeAssistantAlarm` als Typ *Integration*
   hinzufügen.
3. "HomeAssistantAlarm" installieren und Home Assistant neu starten.

### Manuell

Den Ordner [`custom_components/homeassistantalarm`](custom_components/homeassistantalarm)
in das `custom_components`-Verzeichnis deiner Home-Assistant-Installation
kopieren und Home Assistant neu starten.

## Einrichtung

1. Im AlarmServer (`https://alarm.feuerwehr45.de`) anmelden und unter
   **Profil → Kontakt hinzufügen → HomeAssistant** eine Verbindung anlegen.
   Basis-URL, **UUID** und **API-Key** werden angezeigt, der Key **nur dieses
   eine Mal** – direkt in Home Assistant eintragen oder sicher notieren.
2. Unter **Abos** die neue Verbindung bei den gewünschten Organisationen
   einschalten. Ohne Abo kommen keine Alarme (die Einrichtung klappt
   trotzdem, es gibt dann nur keine Organisations-Sensoren).
3. In Home Assistant: *Einstellungen → Geräte & Dienste → Integration
   hinzufügen* → "HomeAssistantAlarm" suchen.
4. **Server-URL** (vorbelegt mit `https://alarm.feuerwehr45.de`),
   **Verbindungs-UUID** und **API-Key** eingeben. Ein selbst gehosteter
   AlarmServer hat die Form `http://<server>:<port>` (Standardport `8080`).
   Die im Profil angezeigte volle Adresse
   (`https://alarm.feuerwehr45.de/api/homeassistant/<UUID>`) geht auch, die
   Integration kürzt sie selbst auf den Server-Teil.

Neu abonnierte Organisationen erkennt die Integration automatisch (Prüfung
alle 15 Minuten, sofort bei einem Alarm einer unbekannten Organisation) und
legt dafür ein eigenes Gerät mit Sensoren an – ohne Neustart.

## Umstellung von GroupAlarm V2 (Version ≤ 0.3.0)

Der alte GroupAlarm-Server (V2) ist abgeschaltet. Bestehende Einträge
**umkonfigurieren statt löschen und neu anlegen** – sonst ändern sich die
Entity-IDs und Automationen brechen:

1. Integration auf Version 0.4.0 aktualisieren (HACS) und Home Assistant neu
   starten.
2. Im AlarmServer die Verbindung anlegen und abonnieren (siehe oben, Schritte
   1–2).
3. *Einstellungen → Geräte & Dienste → HomeAssistantAlarm* → Menü (⋮) beim
   Eintrag → **Neu konfigurieren**. Server-URL, neue UUID und neuen API-Key
   eingeben.

Danach gilt:

- Die Gesamt-Entities (`Letzter Alarm`, `Alarmstatus`, `Verbindung`) behalten
  ihre Entity-IDs.
- Der gespeicherte Stand des alten Servers wird verworfen. Die vorhandenen
  Alarme des AlarmServers landen nur in den Sensoren, es feuert **kein**
  Event für alte Alarme.
- Die Organisationen haben im AlarmServer neue UUIDs. Für sie entstehen neue
  Organisations-Geräte und -Entities. Da die alten Entity-IDs zu dem Zeitpunkt
  noch belegt sind, bekommen die neuen bei gleichem Organisationsnamen ein
  `_2` (z.B. `sensor.ff_musterstadt_letzter_alarm_2`). Nach dem Löschen der
  alten Entities lässt sich die Entity-ID in den Entity-Einstellungen
  zurückbenennen. Automationen, die nach `organizationUuid` filtern, auf die
  neuen UUIDs anpassen (zu sehen im Attribut `organization_uuid` der
  Sensoren).
- Die alten Organisations-Entities aus V2 werden als "nicht mehr
  bereitgestellt" angezeigt und lassen sich in den Entity-Einstellungen
  löschen. Organisations-Geräte, deren Organisation nicht mehr abonniert ist,
  lassen sich auf der Geräteseite löschen.

## Optionen

*Einstellungen → Geräte & Dienste → HomeAssistantAlarm → Konfigurieren*.
Änderungen wirken sofort, ohne Neustart.

| Option | Default | Bedeutung |
|---|---|---|
| Höchstalter für Events (Minuten) | `15` | Ältere Alarme (z.B. nach einem Ausfall von Home Assistant nachgeholt) aktualisieren nur die Sensoren, ohne Event und ohne Alarmstatus. Wie `alarm.maxAgeMinutes` im AlarmServer. `0` schaltet die Prüfung ab. Achtung: Bei falsch gehender Uhr wirkt die Prüfung auch auf frische Alarme; das steht dann als Warnung im Log. |
| Test-Alarme setzen den Alarmstatus | an | Test-Alarme feuern immer das Event (mit `test: true`). Die Option legt fest, ob sie zusätzlich die Alarmstatus-Sensoren auf `Alarm` setzen. |

## Was die Integration bereitstellt

### Event `homeassistantalarm_alarm`

Für jeden neuen Alarm wird ein Event `homeassistantalarm_alarm` gefeuert – das ist
der primäre Mechanismus für Automationen. Payload:

| Feld               | Beschreibung                                              |
|--------------------|------------------------------------------------------------|
| `id`               | Fortlaufende Alarm-ID (zur Dedupe); `-1` beim Probelauf aus dem Profil |
| `organization`     | Name der auslösenden Organisation                           |
| `organizationUuid` | UUID der Organisation (beim Probelauf zufällig)              |
| `message`          | Fertig formatierter Alarmtext                               |
| `timestamp`        | Zeitpunkt des Alarms (ISO 8601)                              |
| `test`             | `true` bei Test-Alarmen (Probelauf im Profil oder Test-Alarm einer Organisation), sonst `false` |
| `alarmData`        | Strukturierte Alarmdaten (Code, Stichwort, Adresse, Fahrzeuge, Koordinaten, ...) – einheitliches Format unabhängig von der Alarmquelle, siehe [`docs/homeassistant-alarm-data.md`](docs/homeassistant-alarm-data.md) |

**Test-Alarme:** Der Probelauf aus dem Profil ("Testen") und Test-Alarme einer
Organisation kommen als normales Event mit `test: true`. Automationen, die
nur bei echten Einsätzen laufen sollen (Licht, Sirene), müssen das selbst
ausfiltern:

```yaml
automation:
  - alias: "Licht an bei Einsatz (ohne Test-Alarme)"
    triggers:
      - trigger: event
        event_type: homeassistantalarm_alarm
    conditions:
      - condition: template
        value_template: "{{ not trigger.event.data.test }}"
    actions:
      - action: light.turn_on
        target:
          entity_id: light.flur
```

Nach Organisation filtern (UUID aus dem Attribut `organization_uuid` des
Organisations-Sensors):

```yaml
automation:
  - alias: "Alarmierung nur für die Feuerwehr"
    triggers:
      - trigger: event
        event_type: homeassistantalarm_alarm
        event_data:
          organizationUuid: "9b1c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
    actions:
      - action: notify.mobile_app_handy
        data:
          title: "{{ '[TEST] ' if trigger.event.data.test else '' }}Einsatzalarm"
          message: "{{ trigger.event.data.message }}"
```

Im Automations-Editor gibt es außerdem den Geräte-Trigger **"Neuer Alarm"**
(Trigger → Gerät → die HomeAssistantAlarm-Verbindung), optional gefiltert
nach Organisation.

### Event `homeassistantalarm_notification`

News und Systemmeldungen des AlarmServers (nur wenn die Verbindung sie im
AlarmServer abonniert hat und gerade verbunden ist; sie werden nicht
nachgeholt). Payload: `type` (z.B. `NEWS`), `message`, `timestamp`.

```yaml
automation:
  - alias: "AlarmServer-Meldungen aufs Handy"
    triggers:
      - trigger: event
        event_type: homeassistantalarm_notification
    actions:
      - action: notify.mobile_app_handy
        data:
          title: "AlarmServer ({{ trigger.event.data.type }})"
          message: "{{ trigger.event.data.message }}"
```

### Geräte und Sensor-Entities

Ein Gerät je Verbindung (Gesamt-Entities) und darunter ein Gerät je
abonnierter Organisation.

- **`Letzter Alarm`** / **`<Organisation> letzter Alarm`** – Zeitpunkt
  (`device_class: timestamp`) des zuletzt empfangenen Alarms, gesamt bzw.
  je Organisation. Der volle Alarmtext steht als Attribut `full_message`
  zur Verfügung, dazu die strukturierten `alarmData`-Felder einzeln als
  Attribute (`code`, `stichwort`, `adresse`, `ort`, `zusatz`, `fahrzeuge`,
  `lat`, `lon`, `maps_link`, `prioritaet`, `datum`, `uhrzeit`) sowie `id`,
  `organization`, `organization_uuid`, `timestamp` und `test`. Der
  Probelauf aus dem Profil erscheint nur im Gesamt-Sensor.
- **`Alarmstatus`** / **`<Organisation> Alarmstatus`** – einfacher
  Zwei-Zustands-Sensor (`Alarm` / `Kein Alarm`), gesamt bzw. je
  Organisation. Springt bei einem neuen Alarm auf `Alarm` und fällt nach 5
  Minuten automatisch wieder auf `Kein Alarm` zurück; ein weiterer Alarm in
  dieser Zeit setzt die 5 Minuten neu. Alte (nachgeholte) Alarme setzen ihn
  nicht, Test-Alarme je nach Option.
- **`Verbindung`** (`binary_sensor`, `device_class: connectivity`) – zeigt,
  ob die Verbindung zum AlarmServer aktuell steht. Bleibt bewusst immer
  verfügbar, damit auch bei einem Verbindungsausfall sichtbar bleibt,
  *dass* er besteht.

Die Alarm- und Alarmstatus-Sensoren dienen Dashboards/History – für
Automationen sollte immer das `homeassistantalarm_alarm`-Event genutzt
werden, da Zustandsänderungen bei identischen Folge-Updates nicht
zuverlässig auslösen.

## Fehlerbehandlung

- Ist der API-Key ungültig oder wurde die Verbindung im AlarmServer
  gelöscht, fordert Home Assistant automatisch eine erneute
  Authentifizierung an (*Einstellungen → Geräte & Dienste* → Hinweis bei
  der Integration).
- Nach zu vielen Fehlversuchen sperrt der AlarmServer die IP kurz (`429`).
  Die Integration wartet dann (`Retry-After`) und verbindet sich danach
  selbst neu – das ist kein Anmeldefehler und startet keine erneute
  Authentifizierung.
- Netzwerkprobleme führen zu automatischen Reconnect-Versuchen mit
  exponentiellem Backoff; die Alarm-/Alarmstatus-Sensoren werden
  währenddessen als "nicht verfügbar" markiert – der `Verbindung`-Sensor
  bleibt verfügbar und wechselt stattdessen selbst auf "Getrennt".
- Je Verbindung hält die Integration genau einen Stream offen (der
  AlarmServer erlaubt drei). Unter *Verwaltung → System → HomeAssistant*
  im AlarmServer sollte daher genau eine offene Verbindung je Eintrag zu
  sehen sein.

| Beobachtung | Ursache |
|---|---|
| Fehler "Ungültiger API-Key" | Key falsch, oder das Konto im AlarmServer ist deaktiviert |
| Fehler "Unbekannte Verbindungs-UUID" | UUID falsch, etwa noch die alte aus V2 |
| Verbunden, aber keine Alarme | Abo der Verbindung im AlarmServer fehlt |

## API-Referenz

Die vollständige Beschreibung der Server-Schnittstelle, gegen die diese
Integration implementiert wurde, befindet sich in
[`docs/homeassistant-integration-api.md`](docs/homeassistant-integration-api.md).
Entwicklungsstand, Architekturentscheidungen, Tests und offene TODOs stehen
in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

## Entwicklung

```
custom_components/homeassistantalarm/
├── __init__.py        # Entry-Setup/-Unload, Löschen alter Org-Geräte
├── api.py              # HTTP/SSE-Client für /status, /poll, /stream
├── config_flow.py       # Einrichtung, Umkonfigurieren, Reauth, Optionen
├── const.py             # Domain, Signale, Defaults
├── coordinator.py       # Poll-Aufholen + Stream-Loop mit Backoff & Dedupe
├── device_trigger.py     # Geräte-Trigger "Neuer Alarm" fürs Automations-UI
├── sensor.py              # Letzter-Alarm- und Alarmstatus-Sensoren
├── binary_sensor.py        # Verbindungs-Sensor
├── strings.json / translations/  # UI-Texte (en/de)
└── manifest.json
tests/                      # pytest + Fake-AlarmServer (aiohttp)
```

Tests laufen in Docker, ohne lokales Python: `sh run_tests.sh`.
