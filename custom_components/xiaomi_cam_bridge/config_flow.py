"""配置流：采集小米 uid / 密码 / 区域，以及每台摄像头的局域网 IP。"""

from homeassistant import config_entries
import voluptuous as vol

from .const import (
    DOMAIN,
    DEFAULT_UID,
    DEFAULT_REGION,
    CAMERAS,
    CONF_UID,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_IPS,
)


class XiaomiCamBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """小米摄像头桥接配置流。"""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            # 收集各摄像头 IP，放入 ips 字典
            ips = {}
            for cam in CAMERAS:
                ip = user_input.pop(f"ip_{cam['key']}", "").strip()
                if ip:
                    ips[cam["key"]] = ip
            user_input[CONF_IPS] = ips
            return self.async_create_entry(title="小米摄像头桥接", data=user_input)

        # 动态构建 schema：uid / password / region + 每台摄像头 IP
        schema = {
            vol.Required(CONF_UID, default=DEFAULT_UID): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(CONF_REGION, default=DEFAULT_REGION): str,
        }
        for cam in CAMERAS:
            schema[
                vol.Optional(
                    f"ip_{cam['key']}",
                    description={"suggested_value": ""},
                )
            ] = str

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "note": (
                    "密码为米家 App 登录密码（go2rtc 用以向云端换取设备令牌）。"
                    "摄像头局域网 IP 不填则交由 go2rtc 从云端解析，"
                    "填了更稳（形如 192.168.3.x）。"
                )
            },
        )
