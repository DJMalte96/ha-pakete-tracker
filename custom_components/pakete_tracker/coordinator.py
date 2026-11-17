"""Zentrale Aktualisierung aller Sendungen."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .carriers import CARRIER_APIS, CarrierApi, CarrierAuthError, CarrierError
from .const import (
    AUTH_API,
    CARRIERS,
    CONF_CARRIERS,
    CONF_CREDENTIALS,
    CONF_KEEP_DELIVERED_DAYS,
    CONF_SCAN_INTERVAL,
    DEFAULT_KEEP_DELIVERED_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .email_scanner import MailboxAuthError, MailboxError, MailboxScanner, merge_findings
from .models import Package, PackageStatus, TrackingInfo
from .store import PackageStore

_LOGGER = logging.getLogger(__name__)

# Verbindungszustände je Dienstleister (für Sensoren und Panel)
CONN_CONNECTED = "connected"  # offizielle API angemeldet
CONN_EMAIL = "email"  # Erkennung über E-Mail-Postfach
CONN_MANUAL = "manual"  # nur manuell eingetragene Nummern
CONN_NOT_CONNECTED = "not_connected"  # Anmeldung erforderlich
CONN_ERROR = "error"


@dataclass
class TrackerData:
    packages: list[Package] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


class PaketeCoordinator(DataUpdateCoordinator[TrackerData]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: PackageStore) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.store = store
        selected = entry.options.get(CONF_CARRIERS, list(CARRIERS))
        self.enabled: list[str] = [c for c in CARRIERS if c in selected]
        self.keep_delivered = timedelta(
            days=entry.options.get(CONF_KEEP_DELIVERED_DAYS, DEFAULT_KEEP_DELIVERED_DAYS)
        )

        credentials: dict[str, dict[str, Any]] = entry.data.get(CONF_CREDENTIALS, {})
        session = async_get_clientsession(hass)
        self.apis: dict[str, CarrierApi] = {
            cid: cls(session, credentials[cid])
            for cid, cls in CARRIER_APIS.items()
            if cid in self.enabled and cid in credentials
        }
        email_cfg = credentials.get("email")
        self.scanner = MailboxScanner(**email_cfg) if email_cfg else None

        self.last_refresh: datetime | None = None
        self._email_packages: dict[tuple[str, str], Package] = {}
        self._api_cache: dict[tuple[str, str], tuple[datetime, TrackingInfo | None]] = {}

    # ------------------------------------------------------------------ #

    async def _async_update_data(self) -> TrackerData:
        errors: dict[str, str] = {}

        if self.scanner:
            try:
                findings = await self.hass.async_add_executor_job(self.scanner.scan)
            except MailboxAuthError as err:
                errors["email"] = f"Anmeldung am Postfach fehlgeschlagen: {err}"
            except MailboxError as err:
                errors["email"] = str(err)
            else:
                self._email_packages = merge_findings(findings)

        packages: dict[tuple[str, str], Package] = {}
        for key, pkg in self._email_packages.items():
            if pkg.carrier in self.enabled:
                # Kopie, damit API-Ergebnisse den E-Mail-Stand nicht überschreiben
                packages[key] = Package(**vars(pkg))

        for item in self.store.manual:
            key = (item["carrier"], item["tracking_number"])
            if item["carrier"] not in self.enabled:
                continue
            pkg = packages.get(key) or Package(*key, source="manual")
            if item.get("name"):
                pkg.name = item["name"]
            packages[key] = pkg

        for pkg in packages.values():
            api = self.apis.get(pkg.carrier)
            if api and pkg.status is not PackageStatus.DELIVERED:
                if info := await self._async_track(api, pkg, errors):
                    pkg.apply(info)

        now = dt_util.utcnow()
        visible: list[Package] = []
        for pkg in packages.values():
            if self.store.is_hidden(*pkg.key):
                continue
            if (
                pkg.status is PackageStatus.DELIVERED
                and pkg.last_update
                and now - pkg.last_update > self.keep_delivered
            ):
                if pkg.source == "manual":
                    # erledigte manuelle Sendungen automatisch aufräumen
                    self.hass.async_create_task(self.store.async_remove(*pkg.key))
                continue
            pkg.url = CARRIERS[pkg.carrier].tracking_url.format(pkg.tracking_number)
            visible.append(pkg)

        visible.sort(
            key=lambda p: (
                not p.is_active,
                p.expected_delivery is None,
                p.expected_delivery or dt_util.now().date(),
                p.name or p.tracking_number,
            )
        )
        self.last_refresh = now
        return TrackerData(packages=visible, errors=errors)

    async def _async_track(
        self, api: CarrierApi, pkg: Package, errors: dict[str, str]
    ) -> TrackingInfo | None:
        now = dt_util.utcnow()
        cached = self._api_cache.get(pkg.key)
        if cached and now - cached[0] < api.min_interval:
            return cached[1]
        try:
            info = await api.async_track(pkg.tracking_number)
        except CarrierAuthError as err:
            errors[pkg.carrier] = str(err)
            return None
        except CarrierError as err:
            _LOGGER.debug("Abfrage %s %s fehlgeschlagen: %s", pkg.carrier, pkg.tracking_number, err)
            errors.setdefault(pkg.carrier, str(err))
            return cached[1] if cached else None
        self._api_cache[pkg.key] = (now, info)
        return info

    # ------------------------------------------------------------------ #

    def connection_state(self, carrier: str) -> str:
        errors = self.data.errors if self.data else {}
        if CARRIERS[carrier].auth == AUTH_API:
            if carrier in errors:
                return CONN_ERROR
            if carrier in self.apis:
                return CONN_CONNECTED
        if self.scanner:
            return CONN_ERROR if "email" in errors else CONN_EMAIL
        # Amazon und API-Dienstleister brauchen eine Anmeldung, der Rest geht manuell
        if carrier == "amazon" or CARRIERS[carrier].auth == AUTH_API:
            return CONN_NOT_CONNECTED
        return CONN_MANUAL

    def carrier_error(self, carrier: str) -> str | None:
        errors = self.data.errors if self.data else {}
        return errors.get(carrier) or (errors.get("email") if self.scanner else None)

    def packages_for(self, carrier: str) -> list[Package]:
        return [p for p in (self.data.packages if self.data else []) if p.carrier == carrier]

    def panel_data(self) -> dict[str, Any]:
        """Alle Daten für die Seitenleisten-App."""
        return {
            "loaded": True,
            "last_update": self.last_refresh.isoformat() if self.last_refresh else None,
            "email_configured": self.scanner is not None,
            "carriers": [
                {
                    "id": cid,
                    "name": CARRIERS[cid].name,
                    "color": CARRIERS[cid].color,
                    "text_color": CARRIERS[cid].text_color,
                    "connection": self.connection_state(cid),
                    "error": self.carrier_error(cid),
                    "packages": [p.as_dict() for p in self.packages_for(cid)],
                }
                for cid in self.enabled
            ],
        }
