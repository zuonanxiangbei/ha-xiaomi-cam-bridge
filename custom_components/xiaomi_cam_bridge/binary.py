"""二进制管理器：确保 go2rtc / ffmpeg 存在并具备可执行权限。

HACS 从 zip 解压不保留 Unix 执行位；部分 proot/Android 文件系统
（FUSE/sdcardfs）chmod 不生效。因此：
  1. 先原位 chmod 0o755；
  2. 若 os.access X_OK 仍为 False，则把二进制重定位到
     dirname(sys.executable)（Python 解释器自己从这里 exec，必然可执行），
     其次 /tmp；
  3. 每一步以 WARNING 级写诊断日志（[xmb-diag]），便于经 /api/error_log 远程排障。
"""

import logging
import os
import shutil
import stat as stat_mod
import sys

from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, GO2RTC_BIN, FFMPEG_BIN

_LOGGER = logging.getLogger(__name__)


class BinaryManager:
    """管理 custom_components/<domain>/bin 下的原生二进制，解析出可执行路径。"""

    def __init__(self, hass):
        self.hass = hass
        self.base = hass.config.path("custom_components", DOMAIN)
        self.bin_dir = os.path.join(self.base, "bin")
        self._go2rtc: str | None = None
        self._ffmpeg: str | None = None

    def _path(self, name: str) -> str:
        return os.path.join(self.bin_dir, name)

    @property
    def go2rtc_path(self) -> str:
        return self._go2rtc or self._path(GO2RTC_BIN)

    @property
    def ffmpeg_path(self) -> str:
        return self._ffmpeg or self._path(FFMPEG_BIN)

    async def ensure(self) -> None:
        """校验二进制存在，解析出真正可执行的路径（必要时重定位）。"""
        results = await self.hass.async_add_executor_job(self._fix)
        self._go2rtc = results[GO2RTC_BIN]
        self._ffmpeg = results[FFMPEG_BIN]

    def _fix(self) -> dict:
        diag: list[str] = []
        diag.append(f"uid={os.getuid()} sys.executable={sys.executable}")
        try:
            st = os.stat(sys.executable)
            diag.append(
                f"python3 mode={stat_mod.filemode(st.st_mode)}（这是可执行基准）"
            )
        except OSError as err:
            diag.append(f"python3 stat 失败：{err}")

        # 候选可执行目录：解释器同目录（必然可执行）> /tmp > 原位
        candidates = [os.path.dirname(os.path.abspath(sys.executable)), "/tmp"]

        results: dict[str, str] = {}
        for name in (GO2RTC_BIN, FFMPEG_BIN):
            src = self._path(name)
            if not os.path.isfile(src):
                raise ConfigEntryNotReady(
                    f"缺少二进制文件：{src}。请确认集成包 bin/ 目录完整。"
                )
            try:
                st = os.stat(src)
                diag.append(
                    f"{name} src mode={stat_mod.filemode(st.st_mode)} size={st.st_size}"
                )
            except OSError as err:
                raise ConfigEntryNotReady(f"无法 stat {src}：{err}")

            final = src
            try:
                os.chmod(src, 0o755)
            except OSError as err:
                diag.append(f"{name} 原位 chmod 失败：{err}")
            mode_after = stat_mod.filemode(os.stat(src).st_mode)
            x_ok_src = os.access(src, os.X_OK)
            diag.append(f"{name} chmod 后 mode={mode_after} x_ok={x_ok_src}")

            if not x_ok_src:
                # chmod 不生效（FUSE/sdcardfs 等）→ 重定位到必然可执行的目录
                for cand in candidates:
                    try:
                        dst = os.path.join(cand, f"xmb_{name}")
                        # 已存在且大小一致则跳过拷贝（避免重试循环反复拷 56MB）
                        need_copy = True
                        if os.path.isfile(dst):
                            need_copy = os.path.getsize(dst) != os.path.getsize(src)
                        if need_copy:
                            shutil.copyfile(src, dst)
                        os.chmod(dst, 0o755)
                        ok = os.access(dst, os.X_OK)
                        diag.append(
                            f"{name} 重定位到 {dst}（copy={need_copy}）x_ok={ok}"
                        )
                        if ok:
                            final = dst
                            break
                    except OSError as err:
                        diag.append(f"{name} 重定位到 {cand} 失败：{err}")
            results[name] = final

        for line in diag:
            _LOGGER.warning("[xmb-diag] %s", line)
        return results
