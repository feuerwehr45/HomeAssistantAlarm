# HomeAssistantAlarm

**HomeAssistantAlarm** ist eine [HACS](https://hacs.xyz/)-Custom-Integration,
die Alarme von einem GroupAlarm-Server (siehe
[`docs/homeassistant-integration-api.md`](docs/homeassistant-integration-api.md))
in Home Assistant empfängt – als Event, mit dem sich beliebige Automationen
auslösen lassen ("Licht an bei Einsatz", Push-Benachrichtigung, Sirene, ...),
und als Sensor-Entities fürs Dashboard/History.

## Funktionsweise

- **Home Assistant ist der Client.** Es wird kein eingehender Webhook
  benötigt – die Integration baut die Verbindung von HA aus zum
  GroupAlarm-Server auf.
- **Echtzeit-Stream (SSE) + Aufhol-Poll**: Nach dem Start (und nach jedem
  Verbindungsabbruch) holt die Integration per `/poll` zunächst verpasste
  Alarme nach, danach hält sie den `/stream`-Kanal offen, über den neue
  Alarme mit < 1s Latenz eintreffen. Bricht der Stream ab, wird mit
  exponentiellem Backoff (1s bis max. 30s) automatisch neu verbunden.
- **Dedupe über `id`**: Alarme, die sowohl per Stream als auch beim
  Aufhol-Poll geliefert werden, werden nur einmal verarbeitet.
- Die zuletzt verarbeitete Alarm-`id` wird persistiert, damit nach einem
  Neustart von Home Assistant nahtlos weitergemacht wird.

## Installation

### Über HACS

1. HACS → Integrationen → Menü (⋮) → *Benutzerdefinierte Repositories*.
2. Dieses Repository als Typ *Integration* hinzufügen.
3. "HomeAssistantAlarm" installieren und Home Assistant neu starten.

### Manuell

Den Ordner [`custom_components/homeassistantalarm`](custom_components/homeassistantalarm)
in das `custom_components`-Verzeichnis deiner Home-Assistant-Installation
kopieren und Home Assistant neu starten.

## Einrichtung

1. Im GroupAlarm-Webinterface unter *Profil → HomeAssistant* eine neue
   Verbindung anlegen. Dabei werden **UUID** und **API-Key** einmalig
   angezeigt – notieren.
2. In Home Assistant: *Einstellungen → Geräte & Dienste → Integration
   hinzufügen* → "HomeAssistantAlarm" suchen.
3. **Server-URL**, **Verbindungs-UUID** und **API-Key** eingeben:
   - Produktiv-Server: `https://api.groupalarm.org`
   - Selbst gehostet: `http://<server>:<port>` (Standardport `7000`)
4. Die Zuordnung der Verbindung zu GroupAlarm-Organisationen erfolgt
   ausschließlich im GroupAlarm-Webinterface (*Schatten-Organisationen*).
   Wird die Zuordnung nach der Einrichtung geändert, erkennt die Integration
   neu hinzugefügte Organisationen automatisch (Prüfung alle 15 Minuten im
   Hintergrund) und legt dafür einen neuen Sensor an – ohne dass HA neu
   geladen werden muss.

## Was die Integration bereitstellt

### Event `homeassistantalarm_alarm`

Für jeden neuen Alarm wird ein Event `homeassistantalarm_alarm` gefeuert – das ist
der primäre Mechanismus für Automationen. Payload:

| Feld               | Beschreibung                                              |
|--------------------|------------------------------------------------------------|
| `id`               | Fortlaufende Alarm-ID (zur Dedupe)                          |
| `organization`     | Name der auslösenden Organisation                           |
| `organizationUuid` | UUID der Organisation                                       |
| `message`          | Fertig formatierter Alarmtext                               |
| `timestamp`        | Zeitpunkt des Alarms (ISO 8601)                              |
| `alarmData`        | Strukturierte Alarmdaten (Code, Stichwort, Adresse, Fahrzeuge, Koordinaten, ...) – einheitliches Format unabhängig von der Alarmquelle, siehe [`docs/homeassistant-alarm-data.md`](docs/homeassistant-alarm-data.md) |

Beispiel-Automation (YAML):

```yaml
automation:
  - alias: "Licht an bei Einsatz"
    trigger:
      - platform: event
        event_type: homeassistantalarm_alarm
    action:
      - service: light.turn_on
        target:
          entity_id: light.flur
```

Nach Organisation filtern:

```yaml
automation:
  - alias: "Alarmierung nur für die Feuerwehr"
    trigger:
      - platform: event
        event_type: homeassistantalarm_alarm
        event_data:
          organizationUuid: "9b1c...-org1"
    action:
      - service: notify.mobile_app_handy
        data:
          title: "Einsatzalarm"
          message: "{{ trigger.event.data.message }}"
```

### Sensor-Entities

- **`Letzter Alarm`** / **`<Organisation> letzter Alarm`** – Zeitpunkt
  (`device_class: timestamp`) des zuletzt empfangenen Alarms, gesamt bzw.
  je Organisation, die zum Zeitpunkt der Einrichtung freigeschaltet war
  (neu hinzugekommene Organisationen bekommen automatisch ihren eigenen
  Sensor, siehe oben). Der volle Alarmtext steht als Attribut
  `full_message` zur Verfügung, dazu die strukturierten `alarmData`-Felder
  einzeln als Attribute (`code`, `stichwort`, `adresse`, `ort`, `zusatz`,
  `fahrzeuge`, `lat`, `lon`, `maps_link`, `prioritaet`, `datum`, `uhrzeit`)
  sowie `id`, `organization`, `organization_uuid` und `timestamp`.
- **`Alarmstatus`** / **`<Organisation> Alarmstatus`** – einfacher
  Zwei-Zustands-Sensor (`Alarm` / `Kein Alarm`), gesamt bzw. je
  Organisation. Springt bei einem neuen Alarm auf `Alarm` und fällt nach 5
  Minuten automatisch wieder auf `Kein Alarm` zurück; ein weiterer Alarm in
  dieser Zeit setzt die 5 Minuten neu. Gedacht für einfache
  Dashboard-Anzeigen ("ist gerade etwas los?"), ohne dafür Automationen/
  Templates auf Basis des Events bauen zu müssen.
- **`Verbindung`** (`binary_sensor`, `device_class: connectivity`) – zeigt,
  ob die Verbindung zum GroupAlarm-Server aktuell steht. Bleibt bewusst
  immer verfügbar (siehe unten), damit auch bei einem Verbindungsausfall
  sichtbar bleibt, *dass* er besteht.

Die Alarm- und Alarmstatus-Sensoren dienen Dashboards/History – für
Automationen sollte immer das `homeassistantalarm_alarm`-Event genutzt
werden, da Zustandsänderungen bei identischen Folge-Updates nicht
zuverlässig auslösen.

## Fehlerbehandlung

- Ist der API-Key ungültig oder wurde die Verbindung im GroupAlarm-Profil
  gelöscht, fordert Home Assistant automatisch eine erneute
  Authentifizierung an (*Einstellungen → Geräte & Dienste* → Hinweis bei
  der Integration).
- Netzwerkprobleme führen zu automatischen Reconnect-Versuchen mit
  exponentiellem Backoff; die Alarm-/Alarmstatus-Sensoren werden
  währenddessen als "nicht verfügbar" markiert – der `Verbindung`-Sensor
  bleibt verfügbar und wechselt stattdessen selbst auf "Getrennt".

## API-Referenz

Die vollständige Beschreibung der Server-Schnittstelle, gegen die diese
Integration implementiert wurde, befindet sich in
[`docs/homeassistant-integration-api.md`](docs/homeassistant-integration-api.md).
Entwicklungsstand, Architekturentscheidungen und offene TODOs stehen in
[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

## Entwicklung

```
custom_components/homeassistantalarm/
├── __init__.py        # Entry-Setup/-Unload, verbindet Client & Coordinator
├── api.py              # HTTP/SSE-Client für /status, /poll, /stream
├── config_flow.py       # Einrichtungsdialog + Reauth
├── const.py             # Domain, Signale, Defaults
├── coordinator.py       # Poll-Aufholen + Stream-Loop mit Backoff & Dedupe
├── device_trigger.py     # Geräte-Trigger "Neuer Alarm" fürs Automations-UI
├── sensor.py              # Letzter-Alarm- und Alarmstatus-Sensoren
├── binary_sensor.py        # Verbindungs-Sensor
├── strings.json / translations/  # UI-Texte (en/de)
└── manifest.json
```

