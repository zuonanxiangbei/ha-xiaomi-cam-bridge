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
        # 关键点：go2rtc 的日志文件配置键是 `outputs`（list），不是 `file:`。
        # 旧写法 `file: "path"` 会被静默忽略 → 日志只写 stdout（我们已丢弃）
        # → go2rtc.log 永不生成。必须用 outputs 列表。
        lines.append("log:")
        lines.append('  level: info')
        lines.append("  outputs:")
        lines.append("    - stdout")
        lines.append(f'    - file: "{self.log_path}"')
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
        """延迟拉起诊断：进程状态 + go2rtc.log 末尾 + 本地 API 流状态 + 强制拉流探测。

        强制用 ffmpeg 拉一次 RTSP，会触发 go2rtc 真正去连小米云/P2P，
        从而在 go2rtc.log 里暴露「云 Token 是否有效 / 设备是否连通」的结果。
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
        # 2) go2rtc.log 末尾
        try:
            lines = await self.hass.async_add_executor_job(self._read_log_tail)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("[xmb-go2rtc] 读取 go2rtc.log 失败：%s", err)
            lines = []
        _LOGGER.warning("[xmb-go2rtc] === go2rtc.log 末 %d 行 ===", len(lines))
        for ln in lines:
            _LOGGER.warning("[xmb-go2rtc] %s", ln)
        # 3) 本地 API：流是否已注册、producer 状态
        try:
            api = await self.hass.async_add_executor_job(self._probe_api)
            _LOGGER.warning("[xmb-go2rtc] API /api/streams: %s", api[:1500])
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "[xmb-go2rtc] 查询 go2rtc API 失败（进程未起/端口未监听）：%s", err
            )
        # 4) 强制拉流探测（触发真实连接）
        try:
            probe = await self.hass.async_add_executor_job(self._probe_streams)
            _LOGGER.warning("[xmb-go2rtc] 强制拉流探测：%s", probe)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("[xmb-go2rtc] 拉流探测异常：%s", err)

    def _probe_api(self) -> str:
        url = f"http://{GO2RTC_API_LISTEN}/api/streams"
        try:
            with urllib.request.urlopen(url, timeout=6) as resp:
                return resp.read().decode("utf-8", "replace")[:1500]
        except Exception as err:  # noqa: BLE001
            return f"(请求异常: {err})"

    def _probe_streams(self) -> str:
        """用自带 ffmpeg 对每个摄像头 RTSP 拉流 5s，触发 go2rtc 连小米云/P2P。"""
        _, port = GO2RTC_RTSP_LISTEN.split(":")
        results = []
        for cam in CAMERAS:
            url = f"rtsp://127.0.0.1:{port}/{cam['stream']}"
            cmd = [
                self.bin_mgr.ffmpeg_path, "-rtsp_transport", "tcp",
                "-i", url, "-t", "5", "-f", "null", "-",
            ]
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=12, cwd=self.base,
                )
                out = (proc.stderr or "") + (proc.stdout or "")
                lines_out = [l for l in out.splitlines() if l.strip()]
                frames = [l for l in lines_out if "frame=" in l]
                if frames:
                    summary = frames[-1].strip()
                elif lines_out:
                    summary = " | ".join(lines_out[-3:])[:300]
                else:
                    summary = "(无输出)"
                results.append(f"{cam['stream']}: rc={proc.returncode} {summary}")
            except Exception as err:  # noqa: BLE001
                results.append(f"{cam['stream']}: 探测异常 {err}")
        return " || ".join(results)

    def _read_log_tail(self, n: int = 50) -> list:
        if not os.path.isfile(self.log_path):
            return ["(go2rtc.log 尚未生成)"]
        with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()[-n:]

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
