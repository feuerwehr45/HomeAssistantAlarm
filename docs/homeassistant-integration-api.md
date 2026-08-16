# GroupAlarm HomeAssistant-Integration – API-Referenz

Diese Datei beschreibt die REST-API, gegen die eine eigenständige HomeAssistant-Custom-Integration
(`custom_components/groupalarm/...`) sprechen muss, um Alarme aus GroupAlarm abzurufen und in
HomeAssistant als Events/Entities verfügbar zu machen. Sie richtet sich an das separate Projekt, in
dem die Integration implementiert wird – nicht an den GroupAlarm-Server selbst.

## Architektur in Kürze

- **HomeAssistant ist der Client.** Die Integration authentifiziert sich mit einer UUID + einem
  API-Key gegen den GroupAlarm-Server. Es gibt keinen eingehenden Webhook auf HA-Seite – die
  HA-Instanz muss von außen nicht erreichbar sein, die Verbindung wird immer von HA aus aufgebaut.
- **Zwei Kanäle für dieselben Daten:**
  - **SSE-Stream (`/stream`, primär, echtzeitnah)** – HA hält eine langlebige HTTP-Verbindung offen,
    über die der Server neue Alarme sofort pusht (relevant für "Licht an bei Einsatz"-Automationen,
    die keine Verzögerung vertragen).
  - **Polling (`/poll`, Fallback + Aufholen nach Verbindungsabbruch)** – klassisches Request/Response,
    liefert alles seit einer zuletzt gesehenen `id`. Wird gebraucht, weil ein SSE-Stream nach einem
    Verbindungsabbruch (Neustart, Netzwerkausfall) keine automatische Nachlieferung verpasster Events
    kennt – dafür ruft man einmalig `/poll?since_id=<letzte bekannte id>` auf.
  - Empfehlung: `/stream` für den Normalbetrieb nutzen, `/poll` beim Start der Integration und nach
    jedem Reconnect des Streams, um die Lücke zu schließen. Details siehe unten unter "Empfohlene
    Strategie".
- **Eine Verbindung = ein API-Key**, der im GroupAlarm-Profil unter *Profil → HomeAssistant* erzeugt
  wird. Eine Verbindung kann für mehrere GroupAlarm-Organisationen freigeschaltet werden (Einstellung
  erfolgt serverseitig in den Schatten-Organisationseinstellungen, nicht über diese API).
- **Alarme werden dauerhaft geloggt** (Tabelle `alarm_log`, auto-increment `id`). Sowohl `/stream` als
  auch `/poll` liefern Einträge in identischer Form (gleiche Feldnamen, gleiche `id`) – die Integration
  kann Alarme aus beiden Quellen anhand der `id` deduplizieren, falls z.B. ein per SSE bereits
  empfangener Alarm durch den Aufhol-Poll nach einem Reconnect erneut auftaucht.

## Basis-URL

Von `getApiBaseUrl()` im GroupAlarm-Webinterface übernommen bzw. im Profil unter der Verbindung
angezeigt:

- Produktiv: `https://api.groupalarm.org`
- Selbst gehostet: `http://<server>:<port>` (Standardport 7000), z.B. `http://192.168.1.10:7000`

Alle Pfade unten sind relativ zu `<BASE_URL>/api`.

## Authentifizierung

Jede Verbindung hat eine **UUID** (Teil des URL-Pfads) und einen **API-Key** (Header). Beide werden
beim Erzeugen der Verbindung im GroupAlarm-Profil einmalig angezeigt.

```
Authorization: Bearer <api_key>
```

- Fehlt der Header oder ist er falsch formatiert → `401` `{"error": "Authorization: Bearer <api_key> Header fehlt"}`
- Unbekannte UUID → `404` `{"error": "Verbindung nicht gefunden"}`
- Falscher Key → `401` `{"error": "Ungültiger API-Key"}`

Empfehlung für den HomeAssistant Config-Flow: drei Felder abfragen – **Host/Base-URL**,
**Verbindungs-UUID**, **API-Key** – und beim Setup direkt gegen den `/status`-Endpunkt validieren
(siehe unten).

## Endpunkte

### `GET /homeassistant/{uuid}/status`

Validiert die Zugangsdaten und liefert Kontext für den Config-Flow (Name der Verbindung, welche
Organisationen aktuell freigeschaltet sind). Sollte beim Einrichten der Integration einmalig
aufgerufen werden, um Host/UUID/Key zu prüfen, bevor der Config-Entry gespeichert wird.

**Request**

```
GET /homeassistant/3f9c2b1a-4e2d-4a3b-9c1a-1234567890ab/status
Authorization: Bearer 9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c...
```

**Response `200`**

```json
{
  "connectionName": "Zuhause",
  "subscribedOrganizations": [
    { "uuid": "9b1c...-org1", "name": "Freiwillige Feuerwehr Musterstadt" },
    { "uuid": "9b1c...-org2", "name": "DLRG Musterstadt" }
  ]
}
```

`subscribedOrganizations` kann leer sein, wenn der Nutzer die Verbindung noch keiner Organisation
zugeordnet hat (dann liefert `/poll` auch keine Alarme). Das ist kein Fehler.

### `GET /homeassistant/{uuid}/stream` (Server-Sent Events, primärer Echtzeit-Kanal)

Öffnet eine langlebige HTTP-Verbindung. Der Server pusht jeden neuen Alarm sofort als `alarm`-Event,
sobald er in GroupAlarm ausgelöst wird (typischerweise < 1 Sekunde Latenz).

**Wichtig – Header:**

```
Accept: text/event-stream
Authorization: Bearer <api_key>
```

Ohne `Accept: text/event-stream` schaltet der Server nicht in den SSE-Modus. `aiohttp` in Python
setzt diesen Header nicht automatisch – explizit mitgeben.

**⚠️ Auth-Fehler kommen NICHT als HTTP-Statuscode.** Aus technischen Gründen (SSE-Antworten committen
den HTTP-Status `200` bereits, bevor der Server die Zugangsdaten prüfen kann) wird ein ungültiger
UUID/Key **nicht** mit `401`/`404` beantwortet, sondern die Verbindung wird mit `200` geöffnet, liefert
sofort ein `error`-Event und wird dann vom Server geschlossen. Die Integration muss also **immer**
das erste Event auswerten, bevor sie die Verbindung als erfolgreich etabliert betrachtet.

**Event-Typen**

| Event       | Wann                                   | Daten (SSE `data:`-Feld)                                   |
|-------------|------------------------------------------|--------------------------------------------------------------|
| `connected` | Einmalig direkt nach erfolgreicher Auth   | `{"connectionName": "Zuhause"}`                              |
| `alarm`     | Bei jedem neuen Alarm einer freigeschalteten Organisation | Gleiche Struktur wie ein Eintrag in `poll`'s `alarms[]` (siehe unten) |
| `error`     | Bei Auth-Fehlern, danach wird die Verbindung geschlossen | `{"error": "Ungültiger API-Key"}` o.ä. |
| *(Kommentar, kein Event)* | Alle 25s als Heartbeat, damit Proxys die Verbindung nicht wegen Inaktivität kappen | Zeile beginnt mit `:` (SSE-Kommentar) – vom `EventSource`-Standard ohnehin ignoriert, ggf. beim manuellen Parsen explizit überspringen |

**Beispiel-Rohdaten des Streams**

```
: ping

event: connected
data: {"connectionName":"Zuhause"}

event: alarm
data: {"id":143,"organizationUuid":"9b1c...-org1","organization":"Freiwillige Feuerwehr Musterstadt","message":"Alarm von: ...","timestamp":"2026-08-16T18:45:24.714Z","rawAlarm":{"...":"..."}}
```

Reconnect-Verhalten: Bricht die Verbindung ab (Netzwerk, Server-Neustart), liefert der Stream **keine**
rückwirkenden Events nach – nach einem Reconnect sofort `/poll?since_id=<letzte gesehene id>` aufrufen,
um die Lücke zu schließen (siehe "Empfohlene Strategie" unten).

### `GET /homeassistant/{uuid}/poll?since_id=<long>&limit=<int>`

Liefert alle Alarme mit `id > since_id` (aufsteigend sortiert) für die Organisationen, die dieser
Verbindung zugeordnet sind.

**Query-Parameter**

| Parameter  | Typ  | Pflicht | Default | Beschreibung                                                        |
|------------|------|---------|---------|----------------------------------------------------------------------|
| `since_id` | long | nein    | `0`     | Nur Alarme mit `id` größer als dieser Wert liefern.                   |
| `limit`    | int  | nein    | `50`    | Maximale Anzahl Einträge pro Aufruf (Server begrenzt auf `1`–`200`).  |

Beim allerersten Poll `since_id` weglassen oder `0` senden – das liefert die letzten `limit` Alarme
(nicht die komplette Historie), damit die Integration nicht bei jedem Erst-Setup Tausende alte Alarme
bekommt. Danach immer die zuletzt empfangene `id` (siehe `latestId` in der Antwort) persistieren und
beim nächsten Poll mitschicken.

**Request**

```
GET /homeassistant/3f9c2b1a-4e2d-4a3b-9c1a-1234567890ab/poll?since_id=142&limit=50
Authorization: Bearer 9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c...
```

**Response `200`**

```json
{
  "latestId": 145,
  "alarms": [
    {
      "id": 143,
      "organizationUuid": "9b1c...-org1",
      "organization": "Freiwillige Feuerwehr Musterstadt",
      "message": "Alarm von: Freiwillige Feuerwehr Musterstadt\nDatum/Uhrzeit 16.08.2026 20:45\nB2.01 Gebäudebrand - Musterstraße 1, 12345 Musterstadt",
      "timestamp": "2026-08-16T18:45:24.714Z",
      "rawAlarm": { "...": "quellenabhängig, siehe unten" }
    },
    {
      "id": 144,
      "organizationUuid": "9b1c...-org2",
      "organization": "DLRG Musterstadt",
      "message": "Alarm von: DLRG Musterstadt\n...",
      "timestamp": "2026-08-16T19:02:11.003Z",
      "rawAlarm": { "...": "..." }
    },
    {
      "id": 145,
      "organizationUuid": "9b1c...-org1",
      "organization": "Freiwillige Feuerwehr Musterstadt",
      "message": "...",
      "timestamp": "2026-08-16T19:10:00.512Z",
      "rawAlarm": { "...": "..." }
    }
  ]
}
```

- `alarms` ist leer, wenn seit `since_id` nichts Neues passiert ist – **kein** Fehler, einfach beim
  nächsten Intervall erneut pollen.
- `latestId` ist auch dann gesetzt, wenn `alarms` leer ist (dann gleich `since_id`) – immer daraus statt
  aus `alarms[alarms.length-1].id` lesen, um Off-by-one-Fehler zu vermeiden.
- `message` ist die bereits fertig formatierte Alarmtext (gemäß dem in GroupAlarm konfigurierten
  Alarmschema der jeweiligen Organisation).
- `rawAlarm` enthält die Rohdaten der Alarmquelle (Struktur unterscheidet sich je nach `alarmSource`
  der Organisation: `ALAMOS` liefert z.B. `unit`/`keyword`/`optionalContent.latitude`/`longitude`,
  `GROUPALARM` liefert u.a. `resources`/`alarmResources`/`startDate`). Nicht blind auf ein festes Schema
  verlassen – nur die Felder lesen, die für die eigene Automation relevant sind, und den Rest
  ignorieren.

**Fehlerfälle**: gleiche Auth-Fehler wie bei `/status` (`401`/`404`); zusätzlich `400` bei
nicht-numerischem `since_id`.

## Empfohlene Strategie: SSE für Echtzeit, Polling nur zum Aufholen

Für den Zweck "Automation soll bei einem Alarm quasi sofort auslösen" ist `/stream` der richtige
Kanal – `/poll` allein würde bei einem vertretbaren Intervall (Sekunden) immer eine gewisse Verzögerung
bedeuten, die für "Licht an bei Einsatz" spürbar wäre.

1. **Beim Start der Integration** (und nach jedem Reconnect des Streams): `/poll?since_id=<letzte
   gespeicherte id>` aufrufen, um Alarme nachzuholen, die während der Downtime aufgetreten sind.
   `latestId` aus der Antwort speichern.
2. **Danach `/stream` öffnen** und dauerhaft offen halten. Jedes `alarm`-Event feuert sofort ein
   HomeAssistant-Event (z.B. `hass.bus.async_fire("groupalarm_alarm", {...})`) mit `organization`,
   `organizationUuid`, `message`, `rawAlarm`, `timestamp`, `id`. Die `id` zusätzlich als "zuletzt
   gesehen" persistieren (`hass.data[DOMAIN][entry.entry_id]["latest_id"]`), damit der nächste
   Aufhol-Poll (Schritt 1) korrekt anschließt.
3. **Bricht der Stream ab**, mit exponentiellem Backoff (z.B. 1s, 2s, 4s, ... max. 30s) neu verbinden.
   Vor jedem Reconnect-Versuch wieder mit Schritt 1 (Aufhol-Poll) beginnen, damit während der
   Unterbrechung nichts verloren geht.
4. **Dedupe über `id`**: Da ein per SSE bereits verarbeiteter Alarm durch einen nachfolgenden
   Aufhol-Poll erneut geliefert werden kann (falls der Stream kurz vor dem Reconnect noch etwas
   empfangen hatte), die zuletzt verarbeiteten `id`s kurzzeitig merken (z.B. letzte 50) und doppelte
   `id`s beim Event-Feuern überspringen.
5. **Zusätzlich zum Event** kann pro Organisation eine `sensor`-Entity mit dem letzten Alarmtext als
   State geführt werden (nützlich für Dashboards/History) – das Event bleibt aber der primäre
   Mechanismus für Automationen, nicht der Entity-State (State-Changes lösen in HA nicht zuverlässig
   bei jedem identischen Folge-Update aus).
6. Verbindungsfehler (Stream tot, Poll schlägt fehl, 401 nach Key-Rotation/Löschen im GroupAlarm-Profil)
   über `UpdateFailed`/einen Config-Entry-Reload-Trigger melden, damit HomeAssistant den Integrationsstatus
   sichtbar auf "nicht verfügbar" setzt statt still zu veralten.

## Beispiel: Python (aiohttp) – Stream mit Aufhol-Poll und Reconnect

```python
import asyncio
import json
import aiohttp

class GroupAlarmClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, uuid: str, api_key: str):
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.uuid = uuid
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.latest_id = 0

    async def poll_once(self, limit: int = 50) -> list[dict]:
        """Aufhol-Poll: alles seit self.latest_id abrufen, latest_id aktualisieren."""
        url = f"{self.base_url}/api/homeassistant/{self.uuid}/poll"
        params = {"since_id": self.latest_id, "limit": limit}
        async with self.session.get(url, headers=self.headers, params=params,
                                     timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 401:
                raise InvalidAuth
            if resp.status == 404:
                raise ConnectionNotFound
            resp.raise_for_status()
            data = await resp.json()
            self.latest_id = data["latestId"]
            return data["alarms"]

    async def stream_forever(self, on_alarm):
        """Läuft dauerhaft: Aufhol-Poll, dann SSE-Stream, mit Backoff-Reconnect bei Abbruch."""
        backoff = 1
        while True:
            for alarm in await self.poll_once():
                on_alarm(alarm)

            try:
                url = f"{self.base_url}/api/homeassistant/{self.uuid}/stream"
                headers = {**self.headers, "Accept": "text/event-stream"}
                async with self.session.get(url, headers=headers,
                                             timeout=aiohttp.ClientTimeout(total=None)) as resp:
                    event_name, data_lines = None, []
                    async for raw_line in resp.content:
                        line = raw_line.decode("utf-8").rstrip("\n")
                        if line.startswith(":"):
                            continue  # Heartbeat-Kommentar, ignorieren
                        if line == "":
                            # Leerzeile = Event komplett
                            if event_name and data_lines:
                                payload = json.loads("".join(data_lines))
                                if event_name == "error":
                                    raise InvalidAuth(payload.get("error"))
                                elif event_name == "alarm":
                                    self.latest_id = max(self.latest_id, payload["id"])
                                    on_alarm(payload)
                                # "connected" wird hier ignoriert, ist nur eine Bestätigung
                            event_name, data_lines = None, []
                            continue
                        if line.startswith("event:"):
                            event_name = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[len("data:"):].strip())
                backoff = 1  # sauberer Verbindungsschluss - Backoff zurücksetzen
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass  # unten wird ohnehin mit Backoff erneut verbunden

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
```

`on_alarm(payload)` ist die Stelle, an der die Integration `hass.bus.async_fire("groupalarm_alarm",
payload)` aufruft (plus Dedupe über `payload["id"]`, siehe Schritt 4 oben).

## Verbindung verwalten (nicht Teil der HA-Integration, nur zur Einordnung)

Die UUID+Key-Erzeugung sowie Zuordnung zu Organisationen passiert ausschließlich im GroupAlarm-
Webinterface (*Profil → HomeAssistant* zum Erstellen/Löschen, *Schatten-Organisationen* zum Zuordnen).
Es gibt bewusst keinen API-Endpunkt, über den die HA-Integration selbst Verbindungen anlegt oder
Organisationen zuordnet – das bleibt eine manuelle Aktion des GroupAlarm-Nutzers.
