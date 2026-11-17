"""Pakete Tracker – Übersicht aller Sendungen in Home Assistant."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.typing import ConfigType

from .const import CARRIERS, DOMAIN, PANEL_URL, SIGNAL_UPDATE, STATIC_URL, VERSION
from .coordinator import PaketeCoordinator
from .store import PackageStore

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type PaketeConfigEntry = ConfigEntry[PaketeCoordinator]

ATTR_CARRIER = "carrier"
ATTR_TRACKING_NUMBER = "tracking_number"
ATTR_NAME = "name"

ADD_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CARRIER): vol.In(list(CARRIERS)),
        vol.Required(ATTR_TRACKING_NUMBER): vol.All(cv.string, vol.Length(min=4, max=40)),
        vol.Optional(ATTR_NAME): vol.Any(None, cv.string),
    }
)
REMOVE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CARRIER): vol.In(list(CARRIERS)),
        vol.Required(ATTR_TRACKING_NUMBER): cv.string,
    }
)


def _get_coordinator(hass: HomeAssistant) -> PaketeCoordinator | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    return None


def _require_coordinator(hass: HomeAssistant) -> PaketeCoordinator:
    if coordinator := _get_coordinator(hass):
        return coordinator
    raise ServiceValidationError("Pakete Tracker ist nicht eingerichtet")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Services und WebSocket-Befehle einmalig registrieren."""

    async def add_package(call: ServiceCall) -> None:
        coordinator = _require_coordinator(hass)
        carrier = call.data[ATTR_CARRIER]
        if carrier not in coordinator.enabled:
            raise ServiceValidationError(
                f"{CARRIERS[carrier].name} ist in der Konfiguration nicht aktiviert"
            )
        await coordinator.store.async_add(
            carrier, call.data[ATTR_TRACKING_NUMBER], call.data.get(ATTR_NAME) or None
        )
        await coordinator.async_request_refresh()

    async def remove_package(call: ServiceCall) -> None:
        coordinator = _require_coordinator(hass)
        await coordinator.store.async_remove(
            call.data[ATTR_CARRIER], call.data[ATTR_TRACKING_NUMBER]
        )
        await coordinator.async_request_refresh()

    async def refresh(call: ServiceCall) -> None:
        await _require_coordinator(hass).async_request_refresh()

    hass.services.async_register(DOMAIN, "add_package", add_package, ADD_SCHEMA)
    hass.services.async_register(DOMAIN, "remove_package", remove_package, REMOVE_SCHEMA)
    hass.services.async_register(DOMAIN, "refresh", refresh)

    websocket_api.async_register_command(hass, ws_subscribe)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PaketeConfigEntry) -> bool:
    store = PackageStore(hass)
    await store.async_load()

    coordinator = PaketeCoordinator(hass, entry, store)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    entry.async_on_unload(
        coordinator.async_add_listener(lambda: async_dispatcher_send(hass, SIGNAL_UPDATE))
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _async_register_panel(hass)
    async_dispatcher_send(hass, SIGNAL_UPDATE)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PaketeConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        frontend.async_remove_panel(hass, PANEL_URL)
        async_dispatcher_send(hass, SIGNAL_UPDATE)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: PaketeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Eintrag „Pakete“ in der Seitenleiste."""
    if not hass.data.get(f"{DOMAIN}_static"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), False)]
        )
        hass.data[f"{DOMAIN}_static"] = True

    if PANEL_URL in hass.data.get(frontend.DATA_PANELS, {}):
        return
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="pakete-tracker-panel",
        sidebar_title="Pakete",
        sidebar_icon="mdi:package-variant-closed",
        module_url=f"{STATIC_URL}/pakete-panel.js?v={VERSION}",
        require_admin=False,
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Liefert die Paketdaten und jede weitere Aktualisierung an das Panel."""

    @callback
    def forward() -> None:
        coordinator = _get_coordinator(hass)
        data = coordinator.panel_data() if coordinator else {"loaded": False, "carriers": []}
        data["is_admin"] = connection.user.is_admin
        connection.send_message(websocket_api.event_message(msg["id"], data))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL_UPDATE, forward)
    connection.send_result(msg["id"])
    forward()
