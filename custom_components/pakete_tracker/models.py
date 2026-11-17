"""Datenmodelle (ohne Home-Assistant-Abhängigkeiten)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class PackageStatus(str, Enum):
    """Vereinheitlichter Sendungsstatus."""

    UNKNOWN = "unknown"
    PRE_TRANSIT = "pre_transit"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    READY_FOR_PICKUP = "ready_for_pickup"
    DELIVERED = "delivered"
    EXCEPTION = "exception"


@dataclass
class TrackingInfo:
    """Ergebnis einer Statusabfrage beim Dienstleister."""

    status: PackageStatus
    status_text: str | None = None
    expected_delivery: date | None = None
    last_update: datetime | None = None


@dataclass
class Package:
    """Eine verfolgte Sendung."""

    carrier: str
    tracking_number: str
    name: str | None = None
    status: PackageStatus = PackageStatus.UNKNOWN
    status_text: str | None = None
    expected_delivery: date | None = None
    last_update: datetime | None = None
    source: str = "manual"  # manual | email
    url: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.carrier, self.tracking_number)

    @property
    def is_active(self) -> bool:
        return self.status is not PackageStatus.DELIVERED

    def apply(self, info: TrackingInfo) -> None:
        self.status = info.status
        if info.status_text:
            self.status_text = info.status_text
        if info.expected_delivery:
            self.expected_delivery = info.expected_delivery
        if info.last_update:
            self.last_update = info.last_update

    def as_dict(self) -> dict:
        return {
            "carrier": self.carrier,
            "tracking_number": self.tracking_number,
            "name": self.name,
            "status": self.status.value,
            "status_text": self.status_text,
            "expected_delivery": (
                self.expected_delivery.isoformat() if self.expected_delivery else None
            ),
            "last_update": self.last_update.isoformat() if self.last_update else None,
            "source": self.source,
            "url": self.url,
        }
