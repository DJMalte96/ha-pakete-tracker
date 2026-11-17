"""Persistente Liste manuell hinzugefügter und ausgeblendeter Sendungen."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

HIDDEN_RETENTION = timedelta(days=90)


def normalize_number(carrier: str, number: str) -> str:
    number = number.strip()
    return number if carrier == "amazon" else number.replace(" ", "").upper()


class PackageStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.packages")
        self.manual: list[dict[str, str | None]] = []
        self.hidden: dict[str, str] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.manual = data.get("manual", [])
        self.hidden = data.get("hidden", {})

    async def _async_save(self) -> None:
        cutoff = dt_util.utcnow() - HIDDEN_RETENTION
        self.hidden = {
            k: v
            for k, v in self.hidden.items()
            if (ts := dt_util.parse_datetime(v)) is None or ts > cutoff
        }
        await self._store.async_save({"manual": self.manual, "hidden": self.hidden})

    def is_hidden(self, carrier: str, number: str) -> bool:
        return f"{carrier}:{number}" in self.hidden

    async def async_add(self, carrier: str, number: str, name: str | None) -> None:
        number = normalize_number(carrier, number)
        self.manual = [
            m for m in self.manual if (m["carrier"], m["tracking_number"]) != (carrier, number)
        ]
        self.manual.append({"carrier": carrier, "tracking_number": number, "name": name})
        self.hidden.pop(f"{carrier}:{number}", None)
        await self._async_save()

    async def async_remove(self, carrier: str, number: str) -> None:
        number = normalize_number(carrier, number)
        self.manual = [
            m for m in self.manual if (m["carrier"], m["tracking_number"]) != (carrier, number)
        ]
        self.hidden[f"{carrier}:{number}"] = dt_util.utcnow().isoformat()
        await self._async_save()
