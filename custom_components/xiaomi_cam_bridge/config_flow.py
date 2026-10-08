"""配置流：采集小米 uid / 云 Token / 区域，以及每台摄像头的局域网 IP。"""

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
            ips = {}
            for cam in CAMERAS:
                ip = user_input.pop(f"ip_{cam['key']}", "").strip()
                if ip:
                    ips[cam["key"]] = ip
            user_input[CONF_IPS] = ips
            return self.async_create_entry(title="小米摄像头桥接", data=user_input)

        existing = {}
        if self.config_entry is not None:
            existing = self.config_entry.data or {}

        def _val(key, default=""):
            v = existing.get(key, default)
            return v if v is not None else default

        schema = {
            vol.Required(CONF_UID, default=_val(CONF_UID, DEFAULT_UID)): str,
            # 云 Token 是密钥，默认留空强制用户填写（哪怕重配置也不回显旧值）
            vol.Required(CONF_PASSWORD, default=""): str,
            vol.Required(CONF_REGION, default=_val(CONF_REGION, DEFAULT_REGION)): str,
        }
        for cam in CAMERAS:
            schema[vol.Optional(f"ip_{cam['key']}", default=_val(f"ip_{cam['key']}"))] = str

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "note": (
                    "「云 Token」不是米家登录密码！go2rtc 静态配置需要小米云 Token（形如 V1:xxxx）。\n"
                    "获取方式（任选其一）：\n"
                    "  1) 浏览器装「小米token助手」扩展，登录 mi.com 后复制 passtoken；"
                    "填入时若没有 V1: 前缀会自动补。\n"
                    "  2) PC 上下载 go2rtc，开 WebUI(localhost:1984) 登录小米账号，"
                    "从生成的 go2rtc.yaml 里抄 V1: 串。\n"
                    "摄像头局域网 IP 强烈建议填写（形如 192.168.3.x），不填则交给 go2rtc 云端解析、可能连不上。"
                )
            },
        )

    async def async_step_reconfigure(self, user_input=None):
        return await self.async_step_user(user_input)
