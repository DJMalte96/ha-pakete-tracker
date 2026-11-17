"""DHL – offizielle Shipment Tracking API (Unified).

API-Key kostenlos unter https://developer.dhl.com (App anlegen,
API "Shipment Tracking - Unified" hinzufügen).
"""

from __future__ import annotations

from datetime import timedelta
import re

from aiohttp import ClientError, ClientTimeout

from ..models import PackageStatus, TrackingInfo
from .base import CarrierApi, CarrierAuthError, CarrierError, parse_date, parse_datetime

API_URL = "https://api-eu.dhl.com/track/shipments"
# Beispielnummer aus der DHL-Doku, nur zum Prüfen des API-Keys
TEST_NUMBER = "00340434161094042557"

STATUS_MAP = {
    "pre-transit": PackageStatus.PRE_TRANSIT,
    "transit": PackageStatus.IN_TRANSIT,
    "delivered": PackageStatus.DELIVERED,
    "failure": PackageStatus.EXCEPTION,
}
OUT_FOR_DELIVERY_RE = re.compile(
    r"zustellfahrzeug|in zustellung|heute zugestellt|out for delivery", re.I
)
PICKUP_RE = re.compile(r"abholbereit|zur abholung|kann .{0,60}abgeholt werden", re.I)


class DhlApi(CarrierApi):
    carrier_id = "dhl"
    # Kostenloses Kontingent: 250 Abfragen/Tag
    min_interval = timedelta(minutes=60)

    async def _request(self, number: str) -> dict | None:
        try:
            async with self._session.get(
                API_URL,
                params={"trackingNumber": number, "language": "de"},
                headers={
                    "DHL-API-Key": self._credentials["api_key"],
                    "Accept": "application/json",
                },
                timeout=ClientTimeout(total=20),
            ) as resp:
                if resp.status in (401, 403):
                    raise CarrierAuthError("DHL API-Key ungültig")
                if resp.status == 404:
                    return None
                if resp.status == 429:
                    raise CarrierError("Tageslimit der DHL-API erreicht")
                if resp.status >= 400:
                    raise CarrierError(f"DHL-API antwortet mit HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (ClientError, TimeoutError) as err:
            raise CarrierError(f"DHL nicht erreichbar: {err}") from err

    async def async_validate(self) -> None:
        await self._request(TEST_NUMBER)

    async def async_track(self, tracking_number: str) -> TrackingInfo | None:
        data = await self._request(tracking_number)
        shipments = (data or {}).get("shipments") or []
        if not shipments:
            return None
        shipment = shipments[0]
        state = shipment.get("status") or {}
        text = state.get("description") or state.get("status")
        status = STATUS_MAP.get(state.get("statusCode", ""), PackageStatus.UNKNOWN)
        if status is PackageStatus.IN_TRANSIT and text:
            if PICKUP_RE.search(text):
                status = PackageStatus.READY_FOR_PICKUP
            elif OUT_FOR_DELIVERY_RE.search(text):
                status = PackageStatus.OUT_FOR_DELIVERY

        expected = parse_date(shipment.get("estimatedTimeOfDelivery")) or parse_date(
            (shipment.get("estimatedDeliveryTimeFrame") or {}).get("estimatedFrom")
        )
        return TrackingInfo(
            status=status,
            status_text=text,
            expected_delivery=expected,
            last_update=parse_datetime(state.get("timestamp")),
        )
