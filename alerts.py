# -*- coding: utf-8 -*-
"""重启提醒：异步音频播放和 Windows 原生通知，不阻塞界面或采集。

Windows 通知与 scpsl_auto_player 的 toast_actions 同一套思路，**避免被杀软拦截**：
* PowerShell 脚本是一段**固定不变**的静态文本——启动参数（含 EncodedCommand）
  每次完全相同，杀软的启发式认得出这是良性固定行为；
  标题/正文拼成 toast XML 后走**环境变量**传入，绝不拼进脚本代码；
* 后台线程用 subprocess 启动，带 CREATE_NO_WINDOW，绝不闪黑窗；
* 通知显示名/图标用 winreg 写一条 HKCU 的 AppUserModelId（失败也不影响发送）；
* 只有 PowerShell 真正接受了请求（输出成功标记）才说成功，
  被系统屏蔽（专注助手/全屏）会给出对应的失败原因。
"""

import base64
import os
import shutil
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication

import config

NOTIFICATION_APP_ID = "ByDiamond.SLRestart"
NOTIFICATION_TIMEOUT_S = 15
CREATE_NO_WINDOW = 0x08000000
MAX_INFLIGHT_NOTIFICATIONS = 6
JOIN_PROTOCOL = "slrestart"                  # 通知按钮的协议：slrestart://join/IP:端口
JOIN_URL_PREFIX = JOIN_PROTOCOL + "://join/"


def join_url_for(addr):
    return JOIN_URL_PREFIX + addr


def default_sound_file():
    media = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Media")
    for name in ("Windows Notify System Generic.wav", "Windows Notify.wav", "notify.wav"):
        path = os.path.join(media, name)
        if os.path.isfile(path):
            return path
    return ""


def build_toast_xml(title, message, join_url=""):
    """拼 toast XML：ElementTree 负责转义，标题/正文里的 & < > 引号都不会破坏结构。

    带 join_url 时，**点通知本体或「进服」按钮**都会经协议激活直连对应服务器
    （协议要在 HKCU 注册过，见 register_notification_identity）。
    """
    toast = ET.Element("toast", duration="short")
    if join_url:
        toast.set("activationType", "protocol")
        toast.set("activation", join_url)
    binding = ET.SubElement(ET.SubElement(toast, "visual"), "binding", template="ToastGeneric")
    ET.SubElement(binding, "text").text = str(title)
    ET.SubElement(binding, "text").text = str(message)
    ET.SubElement(toast, "audio", silent="true")     # 声音由程序自己的提示音开关负责
    if join_url:
        actions = ET.SubElement(toast, "actions")
        ET.SubElement(actions, "action", content="进服", activationType="protocol",
                      arguments=join_url)
    return ET.tostring(toast, encoding="unicode")


# ----------------------------- 发给系统 -----------------------------

PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:OPB_XML)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
$toast.Tag = 'restart'
$toast.Group = 'servers'
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:OPB_AUMID)
$setting = [string]$notifier.Setting
if ($setting -and $setting -ne 'Enabled') {
    Write-Output ('SL_RESTART_NOTIFICATION_BLOCKED:' + $setting)
    exit 2
}
$notifier.Show($toast)
Write-Output 'SL_RESTART_NOTIFICATION_SENT'
"""

_POWERSHELL_ARGS = ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-EncodedCommand",
                    base64.b64encode(PS_SCRIPT.encode("utf-16-le")).decode("ascii")]


def notification_command():
    """固定的 PowerShell 启动参数（脚本静态、数据走环境变量）。"""
    return list(_POWERSHELL_ARGS)


def _powershell_exe():
    """系统自带 PowerShell 的真实路径（找不到就让系统按 PATH 找）。"""
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    for candidate in (os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
                      os.path.join(root, "SysWOW64", "WindowsPowerShell", "v1.0", "powershell.exe")):
        if os.path.isfile(candidate):
            return candidate
    try:
        return shutil.which("powershell.exe") or shutil.which("pwsh.exe") or "powershell.exe"
    except Exception:
        return "powershell.exe"


def register_notification_identity():
    """写当前用户的 HKCU，让通知显示成「SL Restart」+ 图标，并注册按钮用的协议。

    * `AppUserModelId\\ByDiamond.SLRestart`：通知的显示名 / 图标；
    * `Classes\\slrestart\\shell\\open\\command`：通知按钮点击后 Windows 会用
      这个命令拉起本程序并带上 `slrestart://join/IP:端口` 参数（免管理员）。
    打包后用 exe 自己的图标；源码运行用 assets/app.ico。
    写不了也**不拦发送**（顶多通知没名字、按钮点不动）。返回 (ok, 说明)。
    """
    import winreg
    key = r"Software\Classes\AppUserModelId\%s" % NOTIFICATION_APP_ID
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
        winreg.SetValueEx(handle, "DisplayName", 0, winreg.REG_SZ, "SL Restart")
        if getattr(sys, "frozen", False):
            icon = os.path.abspath(sys.executable)
        else:
            path = os.path.join(config.base_dir(), "assets", "app.ico")
            icon = path if os.path.isfile(path) else ""
        if icon:
            winreg.SetValueEx(handle, "IconUri", 0, winreg.REG_SZ, icon)
    proto = r"Software\Classes\%s" % JOIN_PROTOCOL
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, proto) as handle:
        winreg.SetValueEx(handle, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          proto + r"\shell\open\command") as handle:
        if getattr(sys, "frozen", False):
            command = '"%s" "%%1"' % os.path.abspath(sys.executable)
        else:
            pythonw = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                                   "pythonw.exe")
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "sl_restart.py")
            command = '"%s" "%s" "%%1"' % (pythonw, script)
        winreg.SetValueEx(handle, None, 0, winreg.REG_SZ, command)
    return True, "通知显示名与进服协议已注册"


class RestartAlerts(QObject):
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._player = None
        self._audio = None
        self._threads = []
        self._custom_sound = False
        self._volume = 70
        self._play_id = 0
        self._identity_registered = False

    def notify_restart(self, events, cfg):
        """events = [(服名, 地址)]：一台服一条通知，标题/正文维持原样，各带「进服」按钮。"""
        if not events:
            return
        if cfg.get("sound_enabled", False):
            self.play_sound(cfg.get("sound_file", ""), cfg.get("sound_volume", 70))
        if cfg.get("windows_notifications", False):
            for name, addr in events:
                self.show_notification("SCP:SL 服务器重启",
                                       "[%s] 重启" % name,
                                       join_url=join_url_for(addr))

    def play_sound(self, path="", volume=70):
        self.stop_sound()
        self._volume = max(0, min(100, int(volume)))
        self._custom_sound = bool(path and os.path.isfile(path))
        if path and not self._custom_sound:
            config.log("[alerts] 自定义音频不存在，使用系统提示音：%s" % path)
            self.status.emit("音频文件不存在，已改用系统提示音。")
        source = path if self._custom_sound else default_sound_file()
        if not source:
            if self._volume:
                QApplication.beep()
            return
        if self._player is None:
            self._audio = QAudioOutput(self)
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._audio)
            self._player.errorOccurred.connect(self._sound_error)
        self._audio.setVolume(self._volume / 100.0)
        self._player.setSource(QUrl.fromLocalFile(os.path.abspath(source)))
        self._player.play()

    def _sound_error(self, _error, message):
        config.log("[alerts] 音频播放失败：%s" % message)
        self.status.emit("音频播放失败：%s" % message)
        if self._custom_sound:
            self._custom_sound = False
            play_id = self._play_id
            QTimer.singleShot(0, lambda: self.play_sound("", self._volume)
                              if self._play_id == play_id else None)

    def stop_sound(self):
        self._play_id += 1
        if self._player is not None:
            self._player.stop()

    # ----------------------------- Windows 通知 -----------------------------

    def show_notification(self, title, message, join_url=""):
        """把通知交给后台线程去发，绝不阻塞 UI；结果经 status 信号回 UI 线程。"""
        if sys.platform != "win32":
            self.status.emit("Windows 通知仅支持 Windows 10/11。")
            return
        executable = _powershell_exe()
        if not os.path.isfile(executable):
            config.log("[alerts] 找不到 Windows PowerShell")
            self.status.emit("找不到 Windows PowerShell，无法发送系统通知。")
            return
        if sum(1 for t in self._threads if t.is_alive()) >= MAX_INFLIGHT_NOTIFICATIONS:
            config.log("[alerts] 系统通知仍在发送，跳过本次通知")
            return
        if not self._identity_registered:
            try:
                register_notification_identity()
            except Exception as e:
                config.log("[alerts] 注册通知显示名失败（不拦发送）：%r" % e)
            self._identity_registered = True
        xml = build_toast_xml(title, message, join_url)
        thread = threading.Thread(target=self._send_notification,
                                  args=(executable, xml), daemon=True,
                                  name="sl-restart-notification")
        self._threads.append(thread)
        config.log("[alerts] 请求 Windows 通知：%s" % title)
        thread.start()

    def _send_notification(self, executable, xml):
        """后台线程：跑固定脚本发通知，数据走环境变量。"""
        try:
            result = subprocess.run(
                [executable] + notification_command(),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "OPB_XML": xml, "OPB_AUMID": NOTIFICATION_APP_ID},
                timeout=NOTIFICATION_TIMEOUT_S,
                creationflags=CREATE_NO_WINDOW)      # noqa: S603
        except subprocess.TimeoutExpired:
            config.log("[alerts] Windows 通知发送超时")
            self.status.emit("Windows 通知发送超时。")
            return
        except Exception as e:
            config.log("[alerts] Windows 通知进程失败：%r" % e)
            self.status.emit("Windows 通知发送失败，请查看运行日志。")
            return
        self._report_notification_result(
            result.returncode, (result.stdout or ""), (result.stderr or ""))

    def _report_notification_result(self, returncode, out, err):
        """核对 PowerShell 的返回，如实上报（不谎报成功）。在后台线程里被调用。"""
        details = (out or "") + "\n" + (err or "")
        if returncode == 0 and "SL_RESTART_NOTIFICATION_SENT" in details:
            config.log("[alerts] Windows 已接受通知请求（不代表横幅已显示）")
            self.status.emit("通知已提交 Windows；显示方式由系统通知设置控制。")
            return
        config.log("[alerts] Windows 通知失败（%s）：%s" % (returncode, details.strip()[:2000]))
        reasons = {
            "DisabledForApplication": "Windows 已关闭此应用的通知，请在系统通知设置中开启 SL Restart。",
            "DisabledForUser": "Windows 已关闭当前用户的通知，请检查系统通知设置。",
            "DisabledByGroupPolicy": "Windows 通知被系统策略禁用。",
            "DisabledByManifest": "Windows 未识别应用的通知身份，请查看运行日志。",
        }
        reason = next((text for setting, text in reasons.items() if setting in details),
                      "Windows 通知发送失败，请查看运行日志。")
        self.status.emit(reason)

    def stop(self):
        self.stop_sound()
        for thread in self._threads:
            thread.join(timeout=2.0)
