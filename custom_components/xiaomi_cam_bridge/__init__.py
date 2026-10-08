"""Xiaomi Camera Bridge (proot) 主入口。

在 HA 进程树内拉起 go2rtc（连接小米加密 P2P），并注入自带 ffmpeg
管理器，注册原生 camera 实体。所有流量仅经回环地址，外部只经 8123 访问。
"""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.ffmpeg import FFmpegManager
from homeassistant.components.ffmpeg.const import DATA_FFMPEG

from .binary import BinaryManager
from .go2rtc import Go2rtcController
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """设置集成。"""
    bin_mgr = BinaryManager(hass)
    await bin_mgr.ensure()

    # 全局注入自带 ffmpeg 二进制：HA 的 stream 组件（直播 HLS 流转码）依赖
    # hass.data[DATA_FFMPEG]，不设置会在开直播时报错。
    # 同时把路径交给摄像头平台做实体级兜底，避免被用户其它 ffmpeg: 配置覆盖。
    hass.data[DATA_FFMPEG] = FFmpegManager(hass, executable=bin_mgr.ffmpeg_path)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["ffmpeg_path"] = bin_mgr.ffmpeg_path

    # 拉起 go2rtc 子进程
    ctrl = Go2rtcController(hass, entry.data, bin_mgr)
    try:
        await ctrl.start()
    except OSError as err:
        _LOGGER.error("启动 go2rtc 失败：%s", err)
        from homeassistant.exceptions import ConfigEntryNotReady

        raise ConfigEntryNotReady(f"启动 go2rtc 失败：{err}")

    hass.data[DOMAIN]["controller"] = ctrl

    # 卸载时清理 go2rtc 子进程
    async def _async_cleanup() -> None:
        await _cleanup(hass)

    entry.async_on_unload(_async_cleanup)

    # 转发到摄像头平台
    await hass.config_entries.async_forward_entry_setups(entry, ["camera"])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载集成。"""
    await _cleanup(hass)
    await hass.config_entries.async_unload_platforms(entry, ["camera"])
    return True


async def _cleanup(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN)
    if not data:
        return
    ctrl = data.get("controller")
    if ctrl is not None:
        await ctrl.stop()
        data["controller"] = None
