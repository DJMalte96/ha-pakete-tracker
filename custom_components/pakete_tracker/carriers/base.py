"""Basisklasse für Dienstleister mit offizieller API."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from aiohttp import ClientSession

from ..models import TrackingInfo


class CarrierError(Exception):
    """Fehler bei der Abfrage eines Dienstleisters."""


class CarrierAuthError(CarrierError):
    """Zugangsdaten ungültig."""


class CarrierApi:
    """Gemeinsame Schnittstelle der API-Anbindungen."""

    carrier_id: str = ""
    #: Mindestabstand zwischen zwei Abfragen derselben Sendung (Kontingente schonen)
    min_interval = timedelta(minutes=30)

    def __init__(self, session: ClientSession, credentials: dict[str, Any]) -> None:
        self._session = session
        self._credentials = credentials

    async def async_validate(self) -> None:
        """Prüft die Zugangsdaten, wirft CarrierAuthError bei Fehlern."""
        raise NotImplementedError

    async def async_track(self, tracking_number: str) -> TrackingInfo | None:
        """Fragt den Status ab. None = Sendung (noch) nicht bekannt."""
        raise NotImplementedError


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_date(value: str | None) -> date | None:
    if dt := parse_datetime(value):
        return dt.date()
    return None
