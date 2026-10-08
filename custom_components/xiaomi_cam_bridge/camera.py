"""摄像头平台：基于 go2rtc 的 RTSP 流，用自带 ffmpeg 渲染原生 camera 实体。

直接继承 camera.Camera，自持 FFmpegManager / HassFFmpeg，避免依赖
可能随版本变动的 camera.ffmpeg.FFmpegCamera。直播走 stream 组件（HLS），
依赖全局 hass.data[DATA_FFMPEG]（已在 __init__ 注入）。
"""

import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import FFmpegManager, HassFFmpeg
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CAMERAS, GO2RTC_RTSP_LISTEN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """为每个已知摄像头创建一个桥接摄像头实体。"""
    ffmpeg_path = hass.data[DOMAIN]["ffmpeg_path"]
    entities = []
    for cam in CAMERAS:
        rtsp_url = f"rtsp://{GO2RTC_RTSP_LISTEN}/{cam['stream']}"
        entities.append(XiaomiBridgeCamera(hass, cam, rtsp_url, ffmpeg_path))
    async_add_entities(entities)


class XiaomiBridgeCamera(Camera):
    """桥接摄像头：用自带 ffmpeg 从 go2rtc 的 RTSP 取流。"""

    def __init__(self, hass, cam_info, rtsp_url, ffmpeg_path):
        super().__init__()
        self._cam_info = cam_info
        self._rtsp = rtsp_url
        self._manager = FFmpegManager(hass, executable=ffmpeg_path)
        self._ffmpeg = HassFFmpeg(self._manager, _LOGGER)
        self._attr_unique_id = f"{DOMAIN}_{cam_info['stream']}"
        self._attr_name = cam_info["name"]
        self._attr_device_info = {
            "identifiers": {(DOMAIN, DOMAIN)},
            "name": "小米摄像头桥接",
            "manufacturer": "Xiaomi",
            "model": cam_info["model"],
        }
        # 支持直播（HLS/WebRTC via stream 组件）
        self._attr_supported_features = CameraEntityFeature.STREAM

    @property
    def stream_source(self):
        """返回直播源，供 HA stream 组件转码。"""
        return self._rtsp

    async def async_camera_image(self, width=None, height=None):
        """静态取帧：用 ffmpeg 从 RTSP 抽一帧 JPEG。"""
        extra = "-rtsp_transport tcp"
        if width and height:
            extra += f" -vf scale={width}:{height}"
        else:
            extra += " -vf scale=960:-1"
        try:
            image = await self._ffmpeg.async_get_image(self._rtsp, extra_cmd=extra)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("获取 %s 画面失败：%s", self._attr_name, err)
            return None
        return image
