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

    def _existing_data(self) -> dict:
        """安全取正在重配置条目的已有数据。

        注意：HA 2026.9 的 ConfigFlow 实例没有 `config_entry` 属性（那是
        OptionsFlow 的），直接访问会 AttributeError → 配置向导 500。
        统一从 context['entry_id'] 取，user / reconfigure 两种入口都兼容。
        """
        entry_id = (self.context or {}).get("entry_id")
        if not entry_id:
            return {}
        entry = self.hass.config_entries.async_get_entry(entry_id)
        return (entry.data or {}) if entry is not None else {}

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            ips = {}
            for cam in CAMERAS:
                ip = user_input.pop(f"ip_{cam['key']}", "").strip()
                if ip:
                    ips[cam["key"]] = ip
            user_input[CONF_IPS] = ips
            # reconfigure 流程（context 带 entry_id）必须用 update+abort，
            # 否则 async_create_entry 会抛 HomeAssistantError（HA 2026.9）：
            #   "Creates a new entry in a 'reconfigure' flow, when it is
            #    expected to update an existing entry and abort"
            # → 配置向导直接报 "Unknown error occurred"。这是截图的真因。
            if self.context.get("entry_id"):
                return self.async_update_reload_and_abort(data=user_input)
            return self.async_create_entry(title="小米摄像头桥接", data=user_input)

        existing = self._existing_data()
        saved_ips = existing.get(CONF_IPS, {}) or {}

        schema = {
            vol.Required(
                CONF_UID, default=existing.get(CONF_UID, DEFAULT_UID)
            ): str,
            # 云 Token 是密钥，默认留空强制用户填写（哪怕重配置也不回显旧值）
            vol.Required(CONF_PASSWORD, default=""): str,
            vol.Required(
                CONF_REGION, default=existing.get(CONF_REGION, DEFAULT_REGION)
            ): str,
        }
        for cam in CAMERAS:
            schema[
                vol.Optional(
                    f"ip_{cam['key']}", default=saved_ips.get(cam["key"], "")
                )
            ] = str

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "note": (
                    "「云 Token」不是米家登录密码！go2rtc 静态配置需要小米云 Token（形如 V1:xxxx）。\n"
                    "获取方式（任选其一）：\n"
                    "  1) 浏览器登录 account.xiaomi.com → F12 → Application → Cookies → 复制 passToken；"
                    "填入时若没有 V1: 前缀会自动补。\n"
                    "  2) PC 上下载 go2rtc，开 WebUI(localhost:1984) 登录小米账号，"
                    "从生成的 go2rtc.yaml 里抄 V1: 串。\n"
                    "摄像头局域网 IP 强烈建议填写（形如 192.168.3.x），不填则交给 go2rtc 云端解析、可能连不上。"
                )
            },
        )

    async def async_step_reconfigure(self, user_input=None):
        return await self.async_step_user(user_input)
