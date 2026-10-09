"""go2rtc 控制器：生成配置、拉起子进程、管理生命周期。"""

import asyncio
import logging
import os
import subprocess
import urllib.request

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
        self._log_fh = None
        self.base = hass.config.path("custom_components", "xiaomi_cam_bridge")
        self.config_path = os.path.join(self.base, "go2rtc.yaml")
        self.log_path = os.path.join(self.base, "go2rtc.log")

    def _render_yaml(self) -> str:
        uid = self.entry_data[CONF_UID]
        password = self.entry_data[CONF_PASSWORD]
        region = self.entry_data.get(CONF_REGION, "cn")
        ips = self.entry_data.get(CONF_IPS, {}) or {}

        lines = []
        # 显式声明要启用的 go2rtc 模块白名单（app.modules）。
        # 正常官方全量构建下 app.Modules 为 nil → 全部模块初始化；但部分 go2rtc
        # 构建/分支会默认带 modules 白名单（或按需裁剪），导致 xiaomi / webrtc 等
        # 模块不被初始化，表现为 `streams: unsupported scheme: xiaomi://` 且 webrtc
        # 监听缺失。这里显式列出全部官方模块名（含 xiaomi / webrtc），保证桥接所需
        # 模块必然初始化，与官方全量构建行为一致，且不依赖任何默认白名单。
        lines.append("app:")
        lines.append("  modules:")
        for _m in (
            "api", "ws", "streams", "http", "rtsp", "webrtc", "mp4", "hls",
            "mjpeg", "hass", "homekit", "onvif", "rtmp", "webtorrent",
            "wyoming", "echo", "exec", "expr", "ffmpeg", "alsa", "v4l2",
            "bubble", "doorbird", "dvrip", "eseecloud", "flussonic", "gopro",
            "isapi", "ivideon", "mpegts", "multitrans", "nest", "ring",
            "roborock", "tapo", "tuya", "wyze", "xiaomi", "yandex", "debug",
            "ngrok", "pinggy", "srtp",
        ):
            lines.append(f"    - {_m}")
        lines.append("")
        # 小米云凭证：go2rtc 静态配置要求的是「云 Token」（形如 V1:xxxx），
        # 不是米家账号密码！Token 来源：
        #   1) 浏览器扩展「小米token助手」登录米家网页后复制的 passtoken；
        #   2) PC 端 go2rtc WebUI 登录小米账号后自动写入的 V1: 串。
        # 若只填了裸 token（无 V1: 前缀），这里自动补上。
        token = (password or "").strip()
        if token and not token.startswith("V1:"):
            token = "V1:" + token
        lines.append("xiaomi:")
        lines.append(f'  {uid}: "{token}"')
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
        # 日志落盘不依赖 go2rtc 自身的 file 输出（v1.9.14 实测不认
        # `outputs: - file`），而是由 start() 把子进程 stdout/stderr 重定向到
        # go2rtc.log 文件句柄——版本无关、必定落盘。这里只设 level。
        lines.append("log:")
        lines.append('  level: info')
        lines.append("")
        return "\n".join(lines)

    async def start(self) -> None:
        yaml_text = await self.hass.async_add_executor_job(self._render_yaml)
        await self.hass.async_add_executor_job(self._write_yaml, yaml_text)

        # 不管 go2rtc 版本是否支持 log.outputs 的 file 输出（v1.9.14 实测未落盘），
        # 直接把子进程 stdout/stderr 重定向到日志文件——版本无关、必定落盘。
        try:
            log_fh = await self.hass.async_add_executor_job(self._open_log_file)
        except OSError:
            self._log_fh = None
            log_fh = asyncio.subprocess.DEVNULL
        self.proc = await asyncio.create_subprocess_exec(
            self.bin_mgr.go2rtc_path,
            "-config",
            self.config_path,
            stdout=log_fh,
            stderr=log_fh,
            close_fds=True,
        )
        _LOGGER.info(
            "go2rtc 已启动 pid=%s，配置：%s", self.proc.pid, self.config_path
        )
        # 延迟读取 go2rtc.log 末尾，以 WARNING 级输出（/api/error_log 可见），
        # 便于远程排障小米源连通状态。
        self.hass.async_create_task(self._diagnose_log())

    async def _diagnose_log(self) -> None:
        """延迟拉起诊断。

        关键时序修正（v0.1.8）：必须先「强制 ffmpeg 拉流」触发 go2rtc 真正去连
        小米云/P2P，go2rtc 才会把 auth/connect 报错写进 go2rtc.log；**之后**再读
        日志尾，否则只能截到 6 行启动日志（旧版 bug：先读日志后拉流 → 永远看不到
        真实错误）。

        诊断四步：
          1) 进程存活状态
          2) 摄像头 IP 网络可达性（从 HA 进程侧 TCP 探测，验证是否跨网段摸不到）
          3) 强制 ffmpeg 拉流 8s×2（触发 xiaomi 源连接，并捕获 stderr 报错）
          4) 等 5s 让 go2rtc 落盘错误后，读取 go2rtc.log 末尾（含真实 xiaomi 报错）
          5) 本地 API /api/streams 流状态
        """
        await asyncio.sleep(15)
        # 1) 进程存活状态
        if self.proc is not None:
            _LOGGER.warning(
                "[xmb-go2rtc] 进程状态 pid=%s returncode=%s（None=仍在运行）",
                self.proc.pid, self.proc.returncode,
            )
        else:
            _LOGGER.warning("[xmb-go2rtc] 进程对象为空（start 未成功创建子进程）")
        # 2) 摄像头 IP 网络可达性（从 HA 进程侧探测，绕过用户本地网络认知偏差）
        try:
            reach = await self.hass.async_add_executor_job(self._probe_reachability)
            _LOGGER.warning("[xmb-go2rtc] 摄像头 IP 可达性：%s", reach)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("[xmb-go2rtc] 可达性探测异常：%s", err)
        # 3) 强制拉流探测（触发真实连接，并捕获 stderr 报错）—— 必须在读日志之前
        try:
            probe = await self.hass.async_add_executor_job(self._probe_streams)
            _LOGGER.warning("[xmb-go2rtc] 强制拉流探测：%s", probe)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("[xmb-go2rtc] 拉流探测异常：%s", err)
        # 4) 等 go2rtc 把 xiaomi 源报错落盘后再读日志尾
        await asyncio.sleep(5)
        try:
            lines = await self.hass.async_add_executor_job(self._read_log_tail, 120)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("[xmb-go2rtc] 读取 go2rtc.log 失败：%s", err)
            lines = []
        _LOGGER.warning("[xmb-go2rtc] === go2rtc.log 末 %d 行（含 xiaomi 源报错）===", len(lines))
        for ln in lines:
            _LOGGER.warning("[xmb-go2rtc] %s", ln)
        # 4b) 读取日志头（启动阶段 + 任何 panic 堆栈），确认模块初始化顺序与是否崩溃
        try:
            head = await self.hass.async_add_executor_job(self._read_log_head, 40)
        except Exception as err:  # noqa: BLE001
            head = []
        _LOGGER.warning("[xmb-go2rtc] === go2rtc.log 首 %d 行（启动/panic 堆栈）===", len(head))
        for ln in head:
            _LOGGER.warning("[xmb-go2rtc] %s", ln)
        # 5) 本地 API：流是否已注册、producer 状态
        try:
            api = await self.hass.async_add_executor_job(self._probe_api)
            _LOGGER.warning("[xmb-go2rtc] API /api/streams: %s", api[:1500])
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "[xmb-go2rtc] 查询 go2rtc API 失败（进程未起/端口未监听）：%s", err
            )
        # 5b) 已注册源 scheme 列表（定位 unsupported scheme 的权威依据）
        # 若返回里没有 "xiaomi" → xiaomi 模块根本没初始化（白名单/构建裁剪/初始化
        # 崩溃）；若包含 "xiaomi" 却仍报 unsupported → 源串格式问题。
        try:
            schemes = await self.hass.async_add_executor_job(self._probe_schemes)
            _LOGGER.warning("[xmb-go2rtc] API /api/schemes: %s", schemes[:800])
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("[xmb-go2rtc] 查询 schemes 失败（进程未起/端口未监听）：%s", err)

    def _probe_api(self) -> str:
        url = f"http://{GO2RTC_API_LISTEN}/api/streams"
        try:
            with urllib.request.urlopen(url, timeout=6) as resp:
                return resp.read().decode("utf-8", "replace")[:1500]
        except Exception as err:  # noqa: BLE001
            return f"(请求异常: {err})"

    def _probe_streams(self) -> str:
        """用自带 ffmpeg 对每个摄像头 RTSP 拉流 8s，触发 go2rtc 连小米云/P2P。

        注意：go2rtc 的 xiaomi 源是「按需连接」，只有 RTSP 消费者出现才会去连
        小米云/P2P。拉流 8s 足以触发连接尝试；若 xiaomi 源无法连上，go2rtc 的
        RTSP 服务器会返回 404（Server returned 404 Not Found），ffmpeg stderr 里
        会带上这个错误；其底层原因（Token 无效 / IP 不可达 / 设备离线）则写在
        go2rtc.log 中，由 _diagnose_log 第 4 步读取。
        """
        _, port = GO2RTC_RTSP_LISTEN.split(":")
        results = []
        for cam in CAMERAS:
            url = f"rtsp://127.0.0.1:{port}/{cam['stream']}"
            cmd = [
                self.bin_mgr.ffmpeg_path, "-rtsp_transport", "tcp",
                "-i", url, "-t", "8", "-f", "null", "-",
            ]
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=20, cwd=self.base,
                )
                out = (proc.stderr or "") + (proc.stdout or "")
                lines_out = [l for l in out.splitlines() if l.strip()]
                # 优先抓带 frame= 的进度行（说明已出流）
                frames = [l for l in lines_out if "frame=" in l]
                # 其次抓关键错误行
                errs = [
                    l for l in lines_out
                    if any(k in l for k in ("404", "Error", "error", "refused",
                                            "timeout", "Connection", "Unauthorized",
                                            "Not Found", "No route", "Network"))
                ]
                if frames:
                    summary = frames[-1].strip()
                elif errs:
                    summary = " | ".join(errs[-3:])[:400]
                elif lines_out:
                    summary = " | ".join(lines_out[-3:])[:300]
                else:
                    summary = "(无输出)"
                results.append(f"{cam['stream']}: rc={proc.returncode} {summary}")
            except Exception as err:  # noqa: BLE001
                results.append(f"{cam['stream']}: 探测异常 {err}")
        return " || ".join(results)

    def _probe_reachability(self) -> str:
        """从 HA 进程侧 TCP 探测各摄像头 IP，验证是否跨网段摸不到。

        摄像头 IP 取自配置（CONF_IPS）。若 HA 在 192.168.3.5 而摄像头在
        192.168.0.x，且二者不在同一可路由网段，则这里全部超时——这就是 go2rtc
        连不上摄像头的根因。探测端口覆盖常见管理/媒体端口：23/80/554/9999/34567。
        """
        import socket
        ips = self.entry_data.get(CONF_IPS, {}) or {}
        if not ips:
            return "(未配置摄像头 IP，go2rtc 将走小米云中继，无需本机可达性)"
        ports = [23, 80, 554, 9999, 34567]
        out = []
        for cam in CAMERAS:
            ip = (ips.get(cam["key"], "") or "").strip()
            if not ip:
                out.append(f"{cam['stream']}: (无 IP)")
                continue
            hits = []
            for p in ports:
                try:
                    sock = socket.create_connection((ip, p), timeout=3)
                    sock.close()
                    hits.append(str(p))
                except OSError:
                    pass
            if hits:
                out.append(f"{cam['stream']}({ip}): 可达端口 {','.join(hits)}")
            else:
                out.append(
                    f"{cam['stream']}({ip}): 全端口不可达（疑似跨网段/离线/防火墙）"
                )
        return " || ".join(out)

    def _read_log_tail(self, n: int = 50) -> list:
        if not os.path.isfile(self.log_path):
            return ["(go2rtc.log 尚未生成)"]
        with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()[-n:]

    def _read_log_head(self, n: int = 40) -> list:
        """读取日志头部（启动阶段 + 任何 panic 堆栈）。

        每次启动都会截断重写日志，故头部即本次运行的启动顺序；若某模块 Init
        崩溃，Go 的 panic 堆栈会打印在 stderr（已重定向到本日志），据此可判定
        初始化循环是否在 xiaomi/webrtc 之前中断。
        """
        if not os.path.isfile(self.log_path):
            return ["(go2rtc.log 尚未生成)"]
        with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()[:n]

    def _probe_schemes(self) -> str:
        """查询 go2rtc 已注册的源 scheme 列表（GET /api/schemes）。

        这是定位 `unsupported scheme: xiaomi://` 的权威手段：返回的是 go2rtc
        当前真正注册了的源协议（handlers 表的键）。若列表里没有 `xiaomi`，说明
        xiaomi 模块根本没初始化（模块白名单排除 / 构建裁剪 / 初始化崩溃）；若包含
        `xiaomi` 却仍报 unsupported，则问题在源串格式而非模块注册。
        """
        url = f"http://{GO2RTC_API_LISTEN}/api/schemes"
        try:
            with urllib.request.urlopen(url, timeout=6) as resp:
                return resp.read().decode("utf-8", "replace")[:800]
        except Exception as err:  # noqa: BLE001
            return f"(请求异常: {err})"

    def _open_log_file(self):
        # 每次启动截断重写，保证日志对应本次运行
        self._log_fh = open(self.log_path, "wb")
        return self._log_fh

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
            if self._log_fh is not None:
                try:
                    self._log_fh.close()
                except OSError:
                    pass
                self._log_fh = None
