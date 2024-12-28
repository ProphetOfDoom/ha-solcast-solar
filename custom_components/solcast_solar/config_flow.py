"""Config flow for Solcast Solar integration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any

import aiofiles
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    API_QUOTA,
    AUTO_UPDATE,
    BRK_ESTIMATE,
    BRK_ESTIMATE10,
    BRK_ESTIMATE90,
    BRK_HALFHOURLY,
    BRK_HOURLY,
    BRK_SITE,
    BRK_SITE_DETAILED,
    CONFIG_DAMP,
    CONFIG_VERSION,
    CUSTOM_HOUR_SENSOR,
    DOMAIN,
    HARD_LIMIT_API,
    KEY_ESTIMATE,
    SITE_DAMP,
    TITLE,
)

_LOGGER = logging.getLogger(__name__)

LIKE_SITE_ID = r"^[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}$"


def validate_api_key(user_input: dict[str, Any]) -> tuple[str, int, str | None]:
    """Validate the API key.

    Arguments:
        user_input (dict[str, Any]): The user input.

    Returns:
        tuple[str, int, str | None]: The API key, API count, and abort result.

    """
    api_key = user_input[CONF_API_KEY].replace(" ", "")
    api_key = [s for s in api_key.split(",") if s]
    for index, key in enumerate(api_key):
        if re.match(LIKE_SITE_ID, key):
            return "", 0, "API key looks like a site ID"
        for i, k in enumerate(api_key):
            if index != i and key == k:
                return "", 0, "Duplicate API key specified"
    api_count = len(api_key)
    api_key = ",".join(api_key)
    return api_key, api_count, None


def validate_api_limit(user_input: dict[str, Any], api_count: int) -> tuple[str, str | None]:
    """Validate the API limit.

    Arguments:
        user_input (dict[str, Any]): The user input.
        api_count (int): The number of API keys.

    Returns:
        tuple[str, str | None]: The API limit, and abort result.

    """
    api_quota = user_input[API_QUOTA].replace(" ", "")
    api_quota = [s for s in api_quota.split(",") if s]
    for q in api_quota:
        if not q.isnumeric():
            return "", "API limit is not a number"
        if int(q) < 1:
            return "", "API limit must be one or greater"
    if len(api_quota) > api_count:
        return "", "There are more API limit counts entered than keys"
    api_quota = ",".join(api_quota)
    return api_quota, None


@config_entries.HANDLERS.register(DOMAIN)
class SolcastSolarFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    # 5 started 4.0.8
    # 6 started 4.0.15
    # 7 started 4.0.16
    # 8 started 4.0.39
    # 9 started 4.1.3
    # 10 unreleased
    # 11 unreleased
    # 12 started 4.1.8
    # 14 started 4.2.4

    VERSION = CONFIG_VERSION

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> SolcastSolarOptionFlowHandler:
        """Get the options flow for this handler.

        Arguments:
            config_entry (ConfigEntry): The integration entry instance, contains the configuration.

        Returns:
            SolcastSolarOptionFlowHandler: The config flow handler instance.

        """
        return SolcastSolarOptionFlowHandler(config_entry)  # pragma: no cover, never called by tests

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a flow initiated by the user.

        Arguments:
            user_input (dict[str, Any] | None, optional): The config submitted by a user. Defaults to None.

        Returns:
            FlowResult: The form to show.

        """
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            api_key, api_count, abort = validate_api_key(user_input)
            if abort is not None:
                return self.async_abort(reason=abort)

            api_quota, abort = validate_api_limit(user_input, api_count)
            if abort is not None:
                return self.async_abort(reason=abort)

            options = {
                CONF_API_KEY: api_key,
                API_QUOTA: api_quota,
                AUTO_UPDATE: int(user_input[AUTO_UPDATE]),
                # Remaining options set to default
                CUSTOM_HOUR_SENSOR: 1,
                HARD_LIMIT_API: "100.0",
                KEY_ESTIMATE: "estimate",
                BRK_ESTIMATE: True,
                BRK_ESTIMATE10: True,
                BRK_ESTIMATE90: True,
                BRK_SITE: True,
                BRK_HALFHOURLY: True,
                BRK_HOURLY: True,
                BRK_SITE_DETAILED: False,
            }
            damp = {f"damp{factor:02d}": 1.0 for factor in range(24)}
            return self.async_create_entry(title=TITLE, data={}, options=options | damp)

        solcast_json_exists = Path(f"{self.hass.config.config_dir}/solcast.json").is_file()
        _LOGGER.debug(
            "File solcast.json %s",
            "exists, defaulting to auto-update off" if solcast_json_exists else "does not exist, defaulting to auto-update on",
        )

        update: list[SelectOptionDict] = [
            SelectOptionDict(label="none", value="0"),
            SelectOptionDict(label="sunrise_sunset", value="1"),
            SelectOptionDict(label="all_day", value="2"),
        ]

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY, default=""): str,
                    vol.Required(API_QUOTA, default="10"): str,
                    vol.Required(AUTO_UPDATE, default=str(int(not solcast_json_exists))): SelectSelector(
                        SelectSelectorConfig(options=update, mode=SelectSelectorMode.DROPDOWN, translation_key="auto_update")
                    ),
                }
            ),
        )


class SolcastSolarOptionFlowHandler(OptionsFlow):
    """Handle options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow.

        Arguments:
            config_entry (ConfigEntry): The integration entry instance, contains the configuration.

        """
        self._entry = config_entry
        self._options = config_entry.options
        self._all_config_data = None

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Initialise main options flow step.

        Arguments:
            user_input (dict, optional): The input provided by the user. Defaults to None.

        Returns:
            Any: Either an error, or the configuration dialogue results.

        """
        errors = {}

        if user_input is not None:
            try:
                all_config_data = {**self._options}

                all_config_data[CONF_API_KEY], api_count, abort = validate_api_key(user_input)
                if abort is not None:
                    return self.async_abort(reason=abort)

                all_config_data[API_QUOTA], abort = validate_api_limit(user_input, api_count)
                if abort is not None:
                    return self.async_abort(reason=abort)

                # Validate the custom hours sensor
                custom_hour_sensor = user_input[CUSTOM_HOUR_SENSOR]
                if custom_hour_sensor < 1 or custom_hour_sensor > 144:
                    return self.async_abort(reason="Custom sensor not between 1 and 144")
                all_config_data[CUSTOM_HOUR_SENSOR] = custom_hour_sensor

                # Validate the hard limit
                hard_limit = user_input[HARD_LIMIT_API]
                to_set = []
                for h in hard_limit.split(","):
                    h = h.strip()
                    if not h.replace(".", "", 1).isdigit():
                        return self.async_abort(reason="Hard limit is not a positive number")
                    val = float(h)
                    to_set.append(f"{val:.1f}")
                if len(to_set) > api_count:
                    return self.async_abort(reason="There are more hard limits entered than keys")
                hard_limit = ",".join(to_set)
                all_config_data[HARD_LIMIT_API] = hard_limit

                # Disable granular dampening
                if user_input.get(SITE_DAMP) is not None:
                    all_config_data[SITE_DAMP] = user_input[SITE_DAMP]

                all_config_data[AUTO_UPDATE] = int(user_input[AUTO_UPDATE])
                all_config_data[KEY_ESTIMATE] = user_input[KEY_ESTIMATE]
                all_config_data[BRK_ESTIMATE] = user_input[BRK_ESTIMATE]
                all_config_data[BRK_ESTIMATE10] = user_input[BRK_ESTIMATE10]
                all_config_data[BRK_ESTIMATE90] = user_input[BRK_ESTIMATE90]
                all_config_data[BRK_HALFHOURLY] = user_input[BRK_HALFHOURLY]
                all_config_data[BRK_HOURLY] = user_input[BRK_HOURLY]
                site_breakdown = user_input[BRK_SITE]
                all_config_data[BRK_SITE] = site_breakdown
                site_detailed = user_input[BRK_SITE_DETAILED]
                all_config_data[BRK_SITE_DETAILED] = site_detailed

                self._all_config_data = all_config_data

                if user_input.get(CONFIG_DAMP):
                    return await self.async_step_dampen()

                self.hass.config_entries.async_update_entry(self._entry, title=TITLE, options=all_config_data)

                return self.async_create_entry(title=TITLE, data=None)  # pragma: no cover, works at runtime never called by tests
            except Exception as e:  # noqa: BLE001
                errors["base"] = str(e)

        update: list[SelectOptionDict] = [
            SelectOptionDict(label="none", value="0"),
            SelectOptionDict(label="sunrise_sunset", value="1"),
            SelectOptionDict(label="all_day", value="2"),
        ]

        forecasts: list[SelectOptionDict] = [
            SelectOptionDict(label="estimate", value="estimate"),
            SelectOptionDict(label="estimate10", value="estimate10"),
            SelectOptionDict(label="estimate90", value="estimate90"),
        ]

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY, default=self._options.get(CONF_API_KEY)): str,
                    vol.Required(API_QUOTA, default=self._options[API_QUOTA]): str,
                    vol.Required(AUTO_UPDATE, default=str(int(self._options[AUTO_UPDATE]))): SelectSelector(
                        SelectSelectorConfig(options=update, mode=SelectSelectorMode.DROPDOWN, translation_key="auto_update")
                    ),
                    vol.Required(KEY_ESTIMATE, default=self._options.get(KEY_ESTIMATE, "estimate")): SelectSelector(
                        SelectSelectorConfig(options=forecasts, mode=SelectSelectorMode.DROPDOWN, translation_key="key_estimate")
                    ),
                    vol.Required(CUSTOM_HOUR_SENSOR, default=self._options[CUSTOM_HOUR_SENSOR]): int,
                    vol.Required(HARD_LIMIT_API, default=self._options.get(HARD_LIMIT_API)): str,
                    vol.Optional(BRK_ESTIMATE10, default=self._options[BRK_ESTIMATE10]): bool,
                    vol.Optional(BRK_ESTIMATE, default=self._options[BRK_ESTIMATE]): bool,
                    vol.Optional(BRK_ESTIMATE90, default=self._options[BRK_ESTIMATE90]): bool,
                    vol.Optional(BRK_SITE, default=self._options[BRK_SITE]): bool,
                    vol.Optional(BRK_HALFHOURLY, default=self._options[BRK_HALFHOURLY]): bool,
                    vol.Optional(BRK_HOURLY, default=self._options[BRK_HOURLY]): bool,
                    vol.Optional(BRK_SITE_DETAILED, default=self._options[BRK_SITE_DETAILED]): bool,
                    (
                        vol.Optional(CONFIG_DAMP, default=False)
                        if not self._options[SITE_DAMP]
                        else vol.Optional(SITE_DAMP, default=self._options[SITE_DAMP])
                    ): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_dampen(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the hourly dampening factors sub-option.

        Arguments:
            user_input (dict[str, Any] | None): The input provided by the user. Defaults to None.

        Returns:
            FlowResult: The configuration dialogue results.

        """

        errors = {}
        if self._all_config_data is None:
            all_config_data = {**self._options}
        else:
            all_config_data = self._all_config_data
        extant = {f"damp{factor:02d}": all_config_data[f"damp{factor:02d}"] for factor in range(24)}

        if user_input is not None:
            try:
                for factor in range(24):
                    all_config_data[f"damp{factor:02d}"] = user_input[f"damp{factor:02d}"]
                all_config_data[SITE_DAMP] = False

                self.hass.config_entries.async_update_entry(self._entry, title=TITLE, options=all_config_data)
                return self.async_create_entry(title=TITLE, data=None)  # pragma: no cover, works at runtime never called by tests
            except:  # noqa: E722
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="dampen",
            data_schema=vol.Schema(
                {
                    vol.Required(f"damp{factor:02d}", description={"suggested_value": extant[f"damp{factor:02d}"]}): vol.All(
                        vol.Coerce(float), vol.Range(min=0.0, max=1.0)
                    )
                    for factor in range(24)
                }
            ),
            errors=errors,
        )
