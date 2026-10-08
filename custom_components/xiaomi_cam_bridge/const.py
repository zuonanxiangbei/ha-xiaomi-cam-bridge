"""常量定义：小米摄像头型号、go2rtc 配置键。"""

DOMAIN = "xiaomi_cam_bridge"

# 二进制文件名（位于 custom_components/xiaomi_cam_bridge/bin/）
GO2RTC_BIN = "go2rtc"
FFMPEG_BIN = "ffmpeg"

# go2rtc 监听地址（仅本机回环，外部只经 HA 8123 访问）
# 注意：HA 内置 go2rtc 集成（source=system）已占用 1984/8554/8555，
# 这里偏移到一个不冲突的区间，避免两台 go2rtc 抢端口导致起不来。
GO2RTC_API_LISTEN = "127.0.0.1:11984"
GO2RTC_RTSP_LISTEN = "127.0.0.1:18554"
GO2RTC_WEBRTC_LISTEN = "127.0.0.1:18555"

# 默认值
DEFAULT_UID = "882067089"
DEFAULT_REGION = "cn"

# 已知摄像头（型号/did 来自设备登记信息，2025-05 新款走加密 P2P）
# key       : 内部标识
# stream    : go2rtc 流名（同时作为实体 id 基础）
# did       : 设备 ID
# model     : go2rtc xiaomi 源所需的 model 参数
# name      : HA 中显示名
CAMERAS = [
    {
        "key": "cw301",
        "stream": "camera_cw301",
        "did": "1183979306",
        "model": "xiaomi.camera.cw301",
        "name": "小米室外摄像机 4C 3K",
    },
    {
        "key": "cw501d",
        "stream": "camera_cw501d",
        "did": "1176673541",
        "model": "isa.camera.cw501d",
        "name": "小米室外摄像机 4 双摄版",
    },
]

# config_entry.data 中的键
CONF_UID = "uid"
CONF_PASSWORD = "password"
CONF_REGION = "region"
CONF_IPS = "ips"  # {key: lan_ip}
