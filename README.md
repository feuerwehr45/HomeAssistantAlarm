# GroupAlarm für Home Assistant

Eine [HACS](https://hacs.xyz/)-Custom-Integration, die Alarme von einem
GroupAlarm-Server (siehe [`docs/homeassistant-integration-api.md`](docs/homeassistant-integration-api.md))
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
   Wird die Zuordnung nach der Einrichtung geändert, muss die Integration
   neu geladen werden (*Einstellungen → Geräte & Dienste → HomeAssistantAlarm →
   Neu laden*), damit neue Organisations-Sensoren angelegt werden.

## Was die Integration bereitstellt

### Event `groupalarm_alarm`

Für jeden neuen Alarm wird ein Event `groupalarm_alarm` gefeuert – das ist
der primäre Mechanismus für Automationen. Payload:

| Feld               | Beschreibung                                              |
|--------------------|------------------------------------------------------------|
| `id`               | Fortlaufende Alarm-ID (zur Dedupe)                          |
| `organization`     | Name der auslösenden Organisation                           |
| `organizationUuid` | UUID der Organisation                                       |
| `message`          | Fertig formatierter Alarmtext                               |
| `timestamp`        | Zeitpunkt des Alarms (ISO 8601)                              |
| `rawAlarm`         | Rohdaten der Alarmquelle (Struktur variiert je nach Quelle)  |

Beispiel-Automation (YAML):

```yaml
automation:
  - alias: "Licht an bei Einsatz"
    trigger:
      - platform: event
        event_type: groupalarm_alarm
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
        event_type: groupalarm_alarm
        event_data:
          organizationUuid: "9b1c...-org1"
    action:
      - service: notify.mobile_app_handy
        data:
          title: "Einsatzalarm"
          message: "{{ trigger.event.data.message }}"
```

### Sensor-Entities

- **`Letzter Alarm`** – zeigt den zuletzt empfangenen Alarmtext über alle
  freigeschalteten Organisationen hinweg.
- **`<Organisation> letzter Alarm`** – ein Sensor je Organisation, die zum
  Zeitpunkt der Einrichtung freigeschaltet war.

Alle Sensoren tragen als Attribute `id`, `organization`,
`organization_uuid`, `timestamp` und `raw_alarm`. Sensoren dienen
Dashboards/History – für Automationen sollte immer das `groupalarm_alarm`-
Event genutzt werden, da Zustandsänderungen bei identischen Folge-Updates
nicht zuverlässig auslösen.

## Fehlerbehandlung

- Ist der API-Key ungültig oder wurde die Verbindung im GroupAlarm-Profil
  gelöscht, fordert Home Assistant automatisch eine erneute
  Authentifizierung an (*Einstellungen → Geräte & Dienste* → Hinweis bei
  der Integration).
- Netzwerkprobleme führen zu automatischen Reconnect-Versuchen mit
  exponentiellem Backoff; die Sensoren werden währenddessen als "nicht
  verfügbar" markiert.

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
├── sensor.py             # Sensor-Entities
├── strings.json / translations/  # UI-Texte (en/de)
└── manifest.json
```

