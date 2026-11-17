"""Konstanten für den Pakete Tracker."""

from __future__ import annotations

from dataclasses import dataclass

DOMAIN = "pakete_tracker"
VERSION = "0.1.0"  # identisch mit manifest.json halten

CONF_CARRIERS = "carriers"
CONF_CREDENTIALS = "credentials"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_KEEP_DELIVERED_DAYS = "keep_delivered_days"

DEFAULT_SCAN_INTERVAL = 30  # Minuten
DEFAULT_KEEP_DELIVERED_DAYS = 3

# Zugangsart pro Dienstleister
AUTH_API = "api"  # offizielle API mit eigenen Zugangsdaten
AUTH_EMAIL = "email"  # Erkennung über Benachrichtigungs-Mails

PANEL_URL = "pakete"
STATIC_URL = "/pakete_tracker_static"
SIGNAL_UPDATE = f"{DOMAIN}_update"


@dataclass(frozen=True)
class CarrierInfo:
    """Stammdaten eines Paketdienstleisters."""

    id: str
    name: str
    color: str
    text_color: str
    auth: str
    tracking_url: str


CARRIERS: dict[str, CarrierInfo] = {
    c.id: c
    for c in (
        CarrierInfo(
            "dhl",
            "DHL",
            "#FFCC00",
            "#D40511",
            AUTH_API,
            "https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html?piececode={}",
        ),
        CarrierInfo(
            "amazon",
            "Amazon",
            "#232F3E",
            "#FF9900",
            AUTH_EMAIL,
            "https://www.amazon.de/gp/your-account/order-details?orderID={}",
        ),
        CarrierInfo(
            "dpd",
            "DPD",
            "#DC0032",
            "#FFFFFF",
            AUTH_EMAIL,
            "https://tracking.dpd.de/status/de_DE/parcel/{}",
        ),
        CarrierInfo(
            "hermes",
            "Hermes",
            "#0091CD",
            "#FFFFFF",
            AUTH_EMAIL,
            "https://www.myhermes.de/empfangen/sendungsverfolgung/sendungsinformation#{}",
        ),
        CarrierInfo(
            "gls",
            "GLS",
            "#061AB1",
            "#FFD100",
            AUTH_EMAIL,
            "https://www.gls-pakete.de/sendungsverfolgung?match={}",
        ),
        CarrierInfo(
            "ups",
            "UPS",
            "#351C15",
            "#FFB500",
            AUTH_API,
            "https://www.ups.com/track?loc=de_DE&tracknum={}",
        ),
    )
}
