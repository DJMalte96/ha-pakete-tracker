"""API-Anbindungen der Paketdienstleister."""

from __future__ import annotations

from .base import CarrierApi, CarrierAuthError, CarrierError
from .dhl import DhlApi
from .ups import UpsApi

# Dienstleister mit offizieller API. Alle anderen werden über
# Benachrichtigungs-Mails bzw. manuell eingetragene Nummern verfolgt.
CARRIER_APIS: dict[str, type[CarrierApi]] = {
    DhlApi.carrier_id: DhlApi,
    UpsApi.carrier_id: UpsApi,
}

__all__ = ["CARRIER_APIS", "CarrierApi", "CarrierAuthError", "CarrierError"]
