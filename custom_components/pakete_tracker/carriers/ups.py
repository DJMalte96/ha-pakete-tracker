"""UPS – offizielle Tracking API (OAuth Client Credentials).

Zugangsdaten unter https://developer.ups.com (App anlegen, Produkt
"Tracking" auswählen) -> Client ID und Client Secret.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import time
from typing import Any
import uuid

from aiohttp import BasicAuth, ClientError, ClientTimeout

from ..models import PackageStatus, TrackingInfo
from .base import CarrierApi, CarrierAuthError, CarrierError

TOKEN_URL = "https://onlinetools.ups.com/security/v1/oauth/token"
TRACK_URL = "https://onlinetools.ups.com/api/track/v1/details/{}"

STATUS_MAP = {
    "M": PackageStatus.PRE_TRANSIT,
    "MV": PackageStatus.PRE_TRANSIT,
    "P": PackageStatus.IN_TRANSIT,
    "I": PackageStatus.IN_TRANSIT,
    "O": PackageStatus.OUT_FOR_DELIVERY,
    "D": PackageStatus.DELIVERED,
    "X": PackageStatus.EXCEPTION,
    "RS": PackageStatus.EXCEPTION,
}


def _ups_date(value: str | None) -> date | None:
    try:
        return datetime.strptime(value or "", "%Y%m%d").date()
    except ValueError:
        return None


class UpsApi(CarrierApi):
    carrier_id = "ups"
    min_interval = timedelta(minutes=30)

    _token: str | None = None
    _token_expires: float = 0

    async def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires - 60:
            return self._token
        try:
            async with self._session.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=BasicAuth(
                    self._credentials["client_id"], self._credentials["client_secret"]
                ),
                timeout=ClientTimeout(total=20),
            ) as resp:
                if resp.status in (400, 401, 403):
                    raise CarrierAuthError("UPS Client-ID oder Secret ungültig")
                if resp.status >= 400:
                    raise CarrierError(f"UPS-Anmeldung: HTTP {resp.status}")
                data: dict[str, Any] = await resp.json(content_type=None)
        except (ClientError, TimeoutError) as err:
            raise CarrierError(f"UPS nicht erreichbar: {err}") from err
        self._token = data["access_token"]
        self._token_expires = time.monotonic() + int(data.get("expires_in", 3600))
        return self._token

    async def async_validate(self) -> None:
        await self._get_token()

    async def async_track(self, tracking_number: str) -> TrackingInfo | None:
        token = await self._get_token()
        try:
            async with self._session.get(
                TRACK_URL.format(tracking_number),
                params={"locale": "de_DE", "returnSignature": "false"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "transId": uuid.uuid4().hex,
                    "transactionSrc": "pakete_tracker",
                },
                timeout=ClientTimeout(total=20),
            ) as resp:
                if resp.status == 401:
                    self._token = None
                    raise CarrierAuthError("UPS-Token abgelehnt")
                if resp.status == 404:
                    return None
                if resp.status >= 400:
                    raise CarrierError(f"UPS-API antwortet mit HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except (ClientError, TimeoutError) as err:
            raise CarrierError(f"UPS nicht erreichbar: {err}") from err

        try:
            package = data["trackResponse"]["shipment"][0]["package"][0]
        except (KeyError, IndexError, TypeError):
            return None

        activity = (package.get("activity") or [{}])[0]
        state = activity.get("status") or {}
        current = package.get("currentStatus") or {}
        status = STATUS_MAP.get(state.get("type", ""), PackageStatus.UNKNOWN)

        expected = None
        for entry in package.get("deliveryDate") or []:
            if entry.get("type") in ("SDD", "RDD", "DEL"):
                expected = _ups_date(entry.get("date")) or expected

        last_update = None
        if activity.get("date"):
            try:
                last_update = datetime.strptime(
                    activity["date"] + activity.get("time", "000000"), "%Y%m%d%H%M%S"
                ).astimezone()
            except ValueError:
                pass

        return TrackingInfo(
            status=status,
            status_text=current.get("description") or state.get("description"),
            expected_delivery=expected,
            last_update=last_update,
        )
