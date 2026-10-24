# -*- coding: utf-8 -*-
"""重启提醒：异步音频播放和 Windows 原生通知，不阻塞界面或采集。"""

import base64
import os
import sys
import xml.etree.ElementTree as ET

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication

import config


def default_sound_file():
    media = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Media")
    for name in ("Windows Notify System Generic.wav", "Windows Notify.wav", "notify.wav"):
        path = os.path.join(media, name)
        if os.path.isfile(path):
            return path
    return ""


def notification_command(title, message):
    """文本只进入经 XML 转义、再 Base64 编码的数据，不插入 PowerShell 代码。"""
    toast = ET.Element("toast", duration="short")
    binding = ET.SubElement(ET.SubElement(toast, "visual"), "binding", template="ToastGeneric")
    ET.SubElement(binding, "text").text = str(title)
    ET.SubElement(binding, "text").text = str(message)
    ET.SubElement(toast, "audio", silent="true")
    encoded_xml = base64.b64encode(ET.tostring(toast, encoding="utf-8")).decode("ascii")
    script = """$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xmlText = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%s'))
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($xmlText)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$toast.Tag = 'restart'
$toast.Group = 'servers'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('SL Restart').Show($toast)
""" % encoded_xml
    return ["-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
            "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode("ascii")]


class RestartAlerts(QObject):
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._player = None
        self._audio = None
        self._processes = set()
        self._custom_sound = False
        self._volume = 70
        self._play_id = 0

    def notify_restart(self, names, cfg):
        if not names:
            return
        if cfg.get("sound_enabled", False):
            self.play_sound(cfg.get("sound_file", ""), cfg.get("sound_volume", 70))
        if cfg.get("windows_notifications", False):
            self.show_notification("SCP:SL 服务器重启",
                                   "%s 重启" % " ".join("[%s]" % name for name in names))

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

    def show_notification(self, title, message):
        if sys.platform != "win32":
            self.status.emit("Windows 通知仅支持 Windows 10/11。")
            return
        executable = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                  "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if not os.path.isfile(executable):
            config.log("[alerts] 找不到 Windows PowerShell")
            self.status.emit("找不到 Windows PowerShell，无法发送系统通知。")
            return
        if len(self._processes) >= 3:
            config.log("[alerts] 系统通知仍在发送，跳过本次通知")
            return
        process = QProcess(self)
        self._processes.add(process)
        process.setProgram(executable)
        process.setArguments(notification_command(title, message))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        timeout = QTimer(process)
        timeout.setSingleShot(True)
        timeout.timeout.connect(lambda: self._notification_timeout(process))
        process.finished.connect(lambda code, _status: self._notification_finished(process, code))
        process.errorOccurred.connect(lambda _error: self._notification_error(process))
        process.start()
        timeout.start(15000)

    def _notification_timeout(self, process):
        if process.state() != QProcess.ProcessState.NotRunning:
            config.log("[alerts] Windows 通知发送超时")
            self.status.emit("Windows 通知发送超时。")
            process.kill()

    def _notification_error(self, process):
        config.log("[alerts] Windows 通知进程失败：%s" % process.errorString())
        self.status.emit("Windows 通知发送失败，请查看运行日志。")
        if process.error() == QProcess.ProcessError.FailedToStart:
            self._processes.discard(process)
            process.deleteLater()

    def _notification_finished(self, process, code):
        if code:
            details = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
            config.log("[alerts] Windows 通知失败（%d）：%s" % (code, details[:2000]))
            self.status.emit("Windows 通知发送失败，请查看运行日志。")
        else:
            self.status.emit("通知已提交 Windows；显示方式由系统通知设置控制。")
        self._processes.discard(process)
        process.deleteLater()

    def stop(self):
        self.stop_sound()
        for process in list(self._processes):
            process.kill()
            process.waitForFinished(1000)
