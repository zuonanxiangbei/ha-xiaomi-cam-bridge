# Xiaomi Camera Bridge (proot)

在 Home Assistant（proot / 无 add-on 环境）里，用自带的 go2rtc + ffmpeg 二进制
把小米/米家加密 P2P 摄像头（如 CW301 / CW501D）桥接成原生 `camera` 实体，
全部流量仅经回环地址，外部只经 HA 的 8123 访问，不依赖任何外部电脑。

## 原理
- 集成在 HA 进程树内拉起 `bin/go2rtc`（监听 127.0.0.1:11984/18554/18555），
  通过 go2rtc 原生小米 P2P 源连接摄像头。
- go2rtc 输出 RTSP（`rtsp://127.0.0.1:18554/camera_cw301` 等），
  自写 `camera` 实体用自带 ffmpeg 抽帧，直播走 HA `stream` 组件 HLS。

## 安装（HACS 自定义仓库）
1. HACS → 自定义仓库 → 添加本仓库 URL，类别选「集成」。
2. 下载，重启 Home Assistant。
3. 设置 → 设备与服务 → 添加集成 → 搜 `Xiaomi Camera Bridge`。
4. 填入：小米账号 UID、米家登录密码、区域 `cn`，以及两台摄像头局域网 IP（可选）。

## 排错
- 看 `/config/xiaomi_cam_bridge/go2rtc.log` 确认小米源连通。
- 端口已偏移避开 HA 内置 go2rtc（1984/8554/8555 → 11984/18554/18555）。
