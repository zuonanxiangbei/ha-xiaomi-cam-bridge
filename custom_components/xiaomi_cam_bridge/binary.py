"""二进制管理器：确保 go2rtc / ffmpeg 存在并具备可执行权限。

由于本集成通过 HA 备份通道塞入 proot，恢复时执行位可能丢失，
运行时强制 chmod 0o755 作为兜底。
"""

import os

from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, GO2RTC_BIN, FFMPEG_BIN


class BinaryManager:
    """管理 custom_components/<domain>/bin 下的原生二进制。"""

    def __init__(self, hass):
        self.hass = hass
        self.base = hass.config.path("custom_components", DOMAIN)
        self.bin_dir = os.path.join(self.base, "bin")

    def _path(self, name: str) -> str:
        return os.path.join(self.bin_dir, name)

    @property
    def go2rtc_path(self) -> str:
        return self._path(GO2RTC_BIN)

    @property
    def ffmpeg_path(self) -> str:
        return self._path(FFMPEG_BIN)

    async def ensure(self) -> None:
        """校验两个二进制存在，并保证可执行权限。"""
        for name in (GO2RTC_BIN, FFMPEG_BIN):
            path = self._path(name)
            if not os.path.isfile(path):
                raise ConfigEntryNotReady(
                    f"缺少二进制文件：{path}。请确认集成包 bin/ 目录包含 {name}。"
                )
            # 兜底：确保执行位（恢复可能丢失）
            try:
                await self.hass.async_add_executor_job(
                    os.chmod, path, 0o755
                )
            except OSError as err:
                raise ConfigEntryNotReady(f"无法设置 {name} 可执行权限：{err}")
