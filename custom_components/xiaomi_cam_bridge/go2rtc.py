"""go2rtc 控制器：生成配置、拉起子进程、管理生命周期。"""

import asyncio
import logging
import os

from homeassistant.core import HomeAssistant

from .binary import BinaryManager
from .const import (
    CAMERAS,
    CONF_UID,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_IPS,
    GO2RTC_API_LISTEN,
    GO2RTC_RTSP_LISTEN,
    GO2RTC_WEBRTC_LISTEN,
)

_LOGGER = logging.getLogger(__name__)


class Go2rtcController:
    """在 HA 进程树内拉起 go2rtc 子进程。"""

    def __init__(self, hass: HomeAssistant, entry_data: dict, bin_mgr: BinaryManager):
        self.hass = hass
        self.entry_data = entry_data
        self.bin_mgr = bin_mgr
        self.proc: asyncio.subprocess.Process | None = None
        self.base = hass.config.path("custom_components", "xiaomi_cam_bridge")
        self.config_path = os.path.join(self.base, "go2rtc.yaml")
        self.log_path = os.path.join(self.base, "go2rtc.log")

    def _render_yaml(self) -> str:
        uid = self.entry_data[CONF_UID]
        password = self.entry_data[CONF_PASSWORD]
        region = self.entry_data.get(CONF_REGION, "cn")
        ips = self.entry_data.get(CONF_IPS, {}) or {}

        lines = []
        # 云端账号（密码在此，URL 内不放密码）
        lines.append("xiaomi:")
        lines.append(f'  {uid}: "{password}"')
        lines.append("")
        # 流定义
        lines.append("streams:")
        for cam in CAMERAS:
            ip = ips.get(cam["key"], "").strip()
            if ip:
                host_part = f"{region}@{ip}"
            else:
                # 不填 IP 时交由 go2rtc 从云端解析局域网地址
                host_part = f"{region}"
            src = (
                f"xiaomi://{uid}:{host_part}"
                f"?did={cam['did']}&model={cam['model']}"
            )
            lines.append(f"  {cam['stream']}:")
            lines.append(f"    - {src}")
        lines.append("")
        # 仅监听回环，外部经 HA 8123 访问
        lines.append("api:")
        lines.append(f'  listen: "{GO2RTC_API_LISTEN}"')
        lines.append("rtsp:")
        lines.append(f'  listen: "{GO2RTC_RTSP_LISTEN}"')
        lines.append("webrtc:")
        lines.append(f'  listen: "{GO2RTC_WEBRTC_LISTEN}"')
        lines.append("log:")
        lines.append('  level: info')
        lines.append(f'  file: "{self.log_path}"')
        lines.append("")
        return "\n".join(lines)

    async def start(self) -> None:
        yaml_text = await self.hass.async_add_executor_job(self._render_yaml)
        await self.hass.async_add_executor_job(self._write_yaml, yaml_text)

        self.proc = await asyncio.create_subprocess_exec(
            self.bin_mgr.go2rtc_path,
            "-config",
            self.config_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            close_fds=True,
        )
        _LOGGER.info(
            "go2rtc 已启动 pid=%s，配置：%s", self.proc.pid, self.config_path
        )

    def _write_yaml(self, text: str) -> None:
        with open(self.config_path, "w", encoding="utf-8") as fh:
            fh.write(text)

    async def stop(self) -> None:
        if self.proc is None:
            return
        try:
            self.proc.terminate()
            await asyncio.wait_for(self.proc.wait(), timeout=10)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
        finally:
            self.proc = None
