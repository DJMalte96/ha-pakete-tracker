# Pakete Tracker für Home Assistant

Behalte alle Paketsendungen im Blick: DHL, Amazon, DPD, Hermes, GLS und UPS in einer Übersicht. Die Übersicht erscheint als eigene **„Pakete“-App in der Seitenleiste** von Home Assistant, dazu kommen Sensoren für Automationen.

## Funktionen

- **Übersicht aller Paketdienstleister** in der Seitenleiste, mit Status, voraussichtlichem Zustelltag und direktem Link zur Sendungsverfolgung
- **Sichtbare Dienstleister frei wählbar** in der Konfiguration
- **Anmeldung pro Dienstleister**, damit Sendungen automatisch aktualisiert werden
- **Amazon-Bestellungen** werden automatisch aus den Versand-Mails erkannt
- Sendungen manuell hinzufügen (in der App oder per Service)
- Sensoren wie `sensor.pakete_tracker_pakete_unterwegs`, `sensor.pakete_tracker_pakete_heute` und einer pro Dienstleister

## Wie die Sendungen ermittelt werden

Ein Kundenkonto-Login, über den man alle eingehenden Pakete abrufen kann, bietet keiner der Dienstleister offiziell an. Inoffizielle Schnittstellen ändern sich ständig und funktionieren dann nicht mehr. Der Pakete Tracker nutzt deshalb nur Quellen, die zuverlässig funktionieren:

| Dienstleister | Erkennung neuer Sendungen | Status-Aktualisierung |
|---|---|---|
| **DHL** | E-Mail, manuell | Offizielle DHL-API (kostenloser API-Key) |
| **UPS** | E-Mail, manuell | Offizielle UPS-API (kostenlose Client-ID) |
| **Amazon** | E-Mail (Versand-/Zustellmails) | E-Mail |
| **DPD** | E-Mail, manuell | E-Mail (Predict-Benachrichtigungen) |
| **GLS** | E-Mail, manuell | E-Mail |
| **Hermes** | E-Mail, manuell | E-Mail |

**E-Mail-Erkennung:** Das Postfach wird per IMAP **nur gelesen**. Mails werden nicht als gelesen markiert, verschoben oder gelöscht. Ausgewertet werden Mails der Dienstleister selbst und Versandbestätigungen von Shops, die einen Tracking-Link oder eine Sendungsnummer enthalten.

## Installation (HACS)

1. HACS → Integrationen → ⋮ → **Benutzerdefinierte Repositories**
2. URL `https://github.com/DJMalte96/ha-pakete-tracker`, Kategorie **Integration**
3. „Pakete Tracker“ installieren und Home Assistant neu starten
4. Einstellungen → Geräte & Dienste → **Integration hinzufügen** → „Pakete Tracker“
5. Die gewünschten Dienstleister auswählen

Danach erscheint links in der Seitenleiste der Eintrag **„Pakete“**.

## Bei den Dienstleistern anmelden

Einstellungen → Geräte & Dienste → Pakete Tracker → **Konfigurieren**:

- **E-Mail-Postfach verbinden:** IMAP-Server, Benutzer und Passwort
  - web.de: `imap.web.de`, Port 993. Vorher im Web-Postfach unter *Einstellungen → POP3/IMAP Abruf* den Zugriff aktivieren.
  - GMX: `imap.gmx.net`, Port 993 (IMAP ebenfalls in den Einstellungen aktivieren)
  - Gmail: `imap.gmail.com`, Port 993, mit [App-Passwort](https://myaccount.google.com/apppasswords)
- **DHL:** Auf [developer.dhl.com](https://developer.dhl.com) registrieren, eine App anlegen, die API *„Shipment Tracking - Unified“* hinzufügen und den API-Key eintragen. Das kostenlose Kontingent umfasst 250 Abfragen pro Tag. Jede Sendung wird höchstens einmal pro Stunde abgefragt.
- **UPS:** Auf [developer.ups.com](https://developer.ups.com) eine App mit dem Produkt *„Tracking“* anlegen und Client ID und Client Secret eintragen.
- **Dienstleister & Aktualisierung:** sichtbare Dienstleister, Aktualisierungsintervall und wie lange zugestellte Pakete sichtbar bleiben

## Services

| Service | Beschreibung |
|---|---|
| `pakete_tracker.add_package` | Sendung hinzufügen (`carrier`, `tracking_number`, optional `name`) |
| `pakete_tracker.remove_package` | Sendung aus der Übersicht entfernen |
| `pakete_tracker.refresh` | Sofort aktualisieren |

## Beispiel-Automation

```yaml
automation:
  - alias: "Paket kommt heute"
    triggers:
      - trigger: time
        at: "08:00:00"
    conditions:
      - condition: numeric_state
        entity_id: sensor.pakete_tracker_pakete_heute
        above: 0
    actions:
      - action: notify.notify
        data:
          message: >
            Heute kommen {{ states('sensor.pakete_tracker_pakete_heute') }} Paket(e):
            {{ state_attr('sensor.pakete_tracker_pakete_heute', 'packages')
               | map(attribute='name') | map('default', 'Paket', true) | join(', ') }}
```
