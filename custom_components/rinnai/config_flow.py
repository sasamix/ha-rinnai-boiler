"""Мастер настройки интеграции Rinnai."""
from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .api import RinnaiApi, RinnaiAuthError, RinnaiError
from .const import CONF_DEVICE_ID, CONF_ROOM_CONTROL_ID, CONF_LOCAL_ONLY, CONF_LOCAL_PORT, DEFAULT_LOCAL_PORT, DOMAIN

_MAC_RE = re.compile(r"^[0-9a-f]{12}$")
_UUID_RE = re.compile(
    r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$"
)


class RinnaiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Спрашивает идентификатор пульта и проверяет связь."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            room_control_id = (
                user_input[CONF_ROOM_CONTROL_ID].strip().lower().replace(":", "").replace("-", "")
            )
            # Local 2.2.1: идентификатор Android тоже допустим, регистр сохраняем.
            device_id = user_input.get(CONF_DEVICE_ID, "").strip()
            local_only = user_input.get(CONF_LOCAL_ONLY, True)
            port = user_input.get(CONF_LOCAL_PORT, DEFAULT_LOCAL_PORT)
            if not _MAC_RE.match(room_control_id):
                errors[CONF_ROOM_CONTROL_ID] = "invalid_id"
            elif not local_only and not (_UUID_RE.fullmatch(device_id.upper()) or re.fullmatch(r"[0-9a-fA-F]{16}", device_id)):
                errors[CONF_DEVICE_ID] = "invalid_device_id"
            else:
                await self.async_set_unique_id(room_control_id)
                self._abort_if_unique_id_configured()

                # Local 2.2.1: облако не участвует в создании локальной записи.
                if local_only:
                    return self.async_create_entry(
                        title="Газовый котёл Rinnai",
                        data={CONF_ROOM_CONTROL_ID: room_control_id,
                              CONF_DEVICE_ID: "", CONF_LOCAL_ONLY: True},
                        options={CONF_LOCAL_PORT: port},
                    )
                api = RinnaiApi(room_control_id, device_id)
                try:
                    await self.hass.async_add_executor_job(api.get_status)
                except RinnaiAuthError:
                    errors["base"] = "invalid_room_control_id"
                except RinnaiError:
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(
                        title="Газовый котёл Rinnai",
                        data={
                            CONF_ROOM_CONTROL_ID: room_control_id,
                            CONF_DEVICE_ID: device_id,
                            CONF_LOCAL_ONLY: False,
                        },
                        options={CONF_LOCAL_PORT: port},
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ROOM_CONTROL_ID,
                        default=(user_input or {}).get(CONF_ROOM_CONTROL_ID, ""),
                    ): str,
                    vol.Required(CONF_LOCAL_ONLY, default=(user_input or {}).get(CONF_LOCAL_ONLY, True)): bool,
                    vol.Required(CONF_LOCAL_PORT, default=(user_input or {}).get(CONF_LOCAL_PORT, DEFAULT_LOCAL_PORT)): vol.All(vol.Coerce(int), vol.Range(min=1024, max=65535)),
                    vol.Optional(
                        CONF_DEVICE_ID,
                        default=(user_input or {}).get(CONF_DEVICE_ID, ""),
                    ): str,
                }
            ),
            errors=errors,
        )
