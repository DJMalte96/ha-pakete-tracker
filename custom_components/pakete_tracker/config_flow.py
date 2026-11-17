"""Einrichtung und Optionen: Dienstleister wählen und anmelden."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .carriers import CARRIER_APIS, CarrierAuthError, CarrierError
from .const import (
    CARRIERS,
    CONF_CARRIERS,
    CONF_CREDENTIALS,
    CONF_KEEP_DELIVERED_DAYS,
    CONF_SCAN_INTERVAL,
    DEFAULT_KEEP_DELIVERED_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .email_scanner import MailboxAuthError, MailboxError, MailboxScanner

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
TEXT = TextSelector()


def _carrier_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=c.id, label=c.name) for c in CARRIERS.values()],
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )


def _minutes(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX,
            unit_of_measurement="min",
        )
    )


class PaketeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_CARRIERS]:
                errors["base"] = "no_carriers"
            else:
                return self.async_create_entry(
                    title="Pakete Tracker",
                    data={CONF_CREDENTIALS: {}},
                    options={
                        CONF_CARRIERS: user_input[CONF_CARRIERS],
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                        CONF_KEEP_DELIVERED_DAYS: DEFAULT_KEEP_DELIVERED_DAYS,
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_CARRIERS, default=list(CARRIERS)): _carrier_selector()}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PaketeOptionsFlow:
        return PaketeOptionsFlow()


class PaketeOptionsFlow(OptionsFlow):
    """Menü: Einstellungen, Anmeldung je Dienstleister, E-Mail-Postfach."""

    @property
    def _credentials(self) -> dict[str, dict[str, Any]]:
        return dict(self.config_entry.data.get(CONF_CREDENTIALS, {}))

    def _status(self, key: str) -> str:
        return "✅ verbunden" if key in self._credentials else "⚪ nicht verbunden"

    def _save_credentials(self, key: str, value: dict[str, Any] | None) -> ConfigFlowResult:
        credentials = self._credentials
        if value is None:
            credentials.pop(key, None)
        else:
            credentials[key] = value
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, CONF_CREDENTIALS: credentials}
        )
        return self.async_create_entry(data=dict(self.config_entry.options))

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        enabled = self.config_entry.options.get(CONF_CARRIERS, [])
        menu = ["settings", "email"]
        menu += [cid for cid in CARRIER_APIS if cid in enabled]
        return self.async_show_menu(
            step_id="init",
            menu_options=menu,
            description_placeholders={
                "email": self._status("email"),
                "dhl": self._status("dhl"),
                "ups": self._status("ups"),
            },
        )

    # ---------------------------------------------------------------- #

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        options = self.config_entry.options
        if user_input is not None:
            if not user_input[CONF_CARRIERS]:
                errors["base"] = "no_carriers"
            else:
                return self.async_create_entry(
                    data={
                        CONF_CARRIERS: user_input[CONF_CARRIERS],
                        CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                        CONF_KEEP_DELIVERED_DAYS: int(user_input[CONF_KEEP_DELIVERED_DAYS]),
                    }
                )
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CARRIERS, default=options.get(CONF_CARRIERS, list(CARRIERS))
                    ): _carrier_selector(),
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): _minutes(10, 1440),
                    vol.Required(
                        CONF_KEEP_DELIVERED_DAYS,
                        default=options.get(CONF_KEEP_DELIVERED_DAYS, DEFAULT_KEEP_DELIVERED_DAYS),
                    ): NumberSelector(
                        NumberSelectorConfig(min=0, max=30, step=1, mode=NumberSelectorMode.BOX)
                    ),
                }
            ),
            errors=errors,
        )

    # ---------------------------------------------------------------- #

    async def _async_api_login(
        self, carrier: str, user_input: dict[str, Any] | None, fields: dict[str, Any]
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("logout"):
                return self._save_credentials(carrier, None)
            creds = {k: user_input[k].strip() for k in fields}
            api = CARRIER_APIS[carrier](async_get_clientsession(self.hass), creds)
            try:
                await api.async_validate()
            except CarrierAuthError:
                errors["base"] = "invalid_auth"
            except CarrierError:
                errors["base"] = "cannot_connect"
            else:
                return self._save_credentials(carrier, creds)

        current = self._credentials.get(carrier, {})
        schema: dict[Any, Any] = {
            vol.Required(key, default=current.get(key, "")): selector
            for key, selector in fields.items()
        }
        if current:
            schema[vol.Optional("logout", default=False)] = bool
        return self.async_show_form(
            step_id=carrier, data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_dhl(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_api_login("dhl", user_input, {"api_key": PASSWORD})

    async def async_step_ups(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_api_login(
            "ups", user_input, {"client_id": TEXT, "client_secret": PASSWORD}
        )

    # ---------------------------------------------------------------- #

    async def async_step_email(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = self._credentials.get("email", {})
        if user_input is not None:
            if user_input.get("logout"):
                return self._save_credentials("email", None)
            cfg = {
                "host": user_input["host"].strip(),
                "port": int(user_input["port"]),
                "username": user_input["username"].strip(),
                # leeres Passwort beim Bearbeiten = bisheriges behalten
                "password": user_input.get("password") or current.get("password", ""),
                "folder": user_input["folder"].strip() or "INBOX",
                "days": int(user_input["days"]),
            }
            try:
                await self.hass.async_add_executor_job(MailboxScanner(**cfg).test_login)
            except MailboxAuthError:
                errors["base"] = "invalid_auth"
            except MailboxError:
                errors["base"] = "cannot_connect"
            else:
                return self._save_credentials("email", cfg)

        schema: dict[Any, Any] = {
            vol.Required("host", default=current.get("host", "imap.web.de")): TEXT,
            vol.Required("port", default=current.get("port", 993)): NumberSelector(
                NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
            ),
            vol.Required("username", default=current.get("username", "")): TEXT,
            (vol.Optional if current else vol.Required)("password"): PASSWORD,
            vol.Required("folder", default=current.get("folder", "INBOX")): TEXT,
            vol.Required("days", default=current.get("days", 14)): NumberSelector(
                NumberSelectorConfig(min=1, max=60, step=1, mode=NumberSelectorMode.BOX)
            ),
        }
        if current:
            schema[vol.Optional("logout", default=False)] = bool
        return self.async_show_form(step_id="email", data_schema=vol.Schema(schema), errors=errors)
