"""Sensoren: je Dienstleister + Zusammenfassungen."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import PaketeConfigEntry
from .const import CARRIERS, DOMAIN
from .coordinator import PaketeCoordinator
from .models import PackageStatus


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PaketeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        ActivePackagesSensor(coordinator),
        TodayPackagesSensor(coordinator),
    ]
    entities += [CarrierSensor(coordinator, cid) for cid in coordinator.enabled]
    async_add_entities(entities)


class PaketeEntity(CoordinatorEntity[PaketeCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "Pakete"

    def __init__(self, coordinator: PaketeCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Pakete Tracker",
            entry_type=DeviceEntryType.SERVICE,
        )


class ActivePackagesSensor(PaketeEntity):
    _attr_translation_key = "active"
    _attr_icon = "mdi:truck-fast-outline"

    def __init__(self, coordinator: PaketeCoordinator) -> None:
        super().__init__(coordinator, "active")

    @property
    def native_value(self) -> int:
        return sum(p.is_active for p in self.coordinator.data.packages)


class TodayPackagesSensor(PaketeEntity):
    _attr_translation_key = "today"
    _attr_icon = "mdi:calendar-today"

    def __init__(self, coordinator: PaketeCoordinator) -> None:
        super().__init__(coordinator, "today")

    def _today(self) -> list:
        today = dt_util.now().date()
        return [
            p
            for p in self.coordinator.data.packages
            if p.is_active
            and (p.expected_delivery == today or p.status is PackageStatus.OUT_FOR_DELIVERY)
        ]

    @property
    def native_value(self) -> int:
        return len(self._today())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"packages": [p.as_dict() for p in self._today()]}


class CarrierSensor(PaketeEntity):
    _attr_icon = "mdi:package-variant-closed"

    def __init__(self, coordinator: PaketeCoordinator, carrier: str) -> None:
        super().__init__(coordinator, carrier)
        self._carrier = carrier
        self._attr_name = CARRIERS[carrier].name

    @property
    def native_value(self) -> int:
        return sum(p.is_active for p in self.coordinator.packages_for(self._carrier))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "carrier": self._carrier,
            "connection": self.coordinator.connection_state(self._carrier),
            "error": self.coordinator.carrier_error(self._carrier),
            "packages": [p.as_dict() for p in self.coordinator.packages_for(self._carrier)],
        }
