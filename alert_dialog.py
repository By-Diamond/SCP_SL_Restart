# -*- coding: utf-8 -*-
"""重启提醒设置；确认后由 App 保存，试听不修改已有设置。"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSlider, QVBoxLayout)

from alerts import RestartAlerts


class AlertDialog(QDialog):
    def __init__(self, cfg, notifier=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("重启提醒设置")
        self.resize(520, 320)
        self._preview = RestartAlerts(self)
        self._notifier = notifier or self._preview
        self.finished.connect(lambda _result: self._preview.stop_sound())

        self.sound_enabled = QCheckBox("启用重启提示音")
        self.sound_enabled.setChecked(cfg.get("sound_enabled", False))
        self.sound_file = QLineEdit(cfg.get("sound_file", ""))
        self.sound_file.setReadOnly(True)
        self.sound_file.setPlaceholderText("系统默认提示音")
        choose = QPushButton("选择音频…")
        choose.clicked.connect(self._choose_sound)
        reset = QPushButton("恢复默认")
        reset.clicked.connect(self.sound_file.clear)
        files = QHBoxLayout()
        files.addWidget(self.sound_file, 1)
        files.addWidget(choose)
        files.addWidget(reset)

        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(cfg.get("sound_volume", 70))
        volume_label = QLabel("%d%%" % self.volume.value())
        self.volume.valueChanged.connect(lambda value: volume_label.setText("%d%%" % value))
        preview = QPushButton("试听")
        preview.clicked.connect(self._test_sound)
        stop = QPushButton("停止")
        stop.clicked.connect(self._preview.stop_sound)
        volume = QHBoxLayout()
        volume.addWidget(QLabel("音量"))
        volume.addWidget(self.volume, 1)
        volume.addWidget(volume_label)
        volume.addWidget(preview)
        volume.addWidget(stop)

        self.notifications_enabled = QCheckBox("启用 Windows 通知中心提醒")
        self.notifications_enabled.setChecked(cfg.get("windows_notifications", False))
        test_notification = QPushButton("测试通知")
        test_notification.clicked.connect(self._test_notification)
        notifications = QHBoxLayout()
        notifications.addWidget(self.notifications_enabled, 1)
        notifications.addWidget(test_notification)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self._preview.status.connect(self.status_label.setText)
        if self._notifier is not self._preview:
            self._notifier.status.connect(self.status_label.setText)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("仅检测到服务器重启时提醒；同一批多服重启会合并提醒。"))
        layout.addWidget(self.sound_enabled)
        layout.addLayout(files)
        layout.addLayout(volume)
        hint = QLabel("支持 MP3、WAV 等音频。音频文件移动或删除后需要重新选择。\n"
                      "留空使用系统提示音。Windows 通知本身静音，声音由上面的开关控制。\n"
                      "通知横幅和通知中心记录受 Windows 通知设置及勿扰模式影响。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addLayout(notifications)
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        layout.addWidget(box)

    def _choose_sound(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, "选择重启提示音", self.sound_file.text(),
            "音频文件 (*.mp3 *.wav *.ogg *.flac *.m4a);;所有文件 (*)")
        if path:
            self.sound_file.setText(path)

    def _test_sound(self):
        self.status_label.setText("正在试听；可点击‘停止’结束。")
        self._preview.play_sound(self.sound_file.text(), self.volume.value())

    def _test_notification(self):
        self.status_label.setText("正在发送测试通知…")
        self._notifier.show_notification("SL Restart · 测试通知", "服务器重启时会在此显示提醒。")

    def result_settings(self):
        return {"sound_enabled": self.sound_enabled.isChecked(),
                "sound_file": self.sound_file.text(),
                "sound_volume": self.volume.value(),
                "windows_notifications": self.notifications_enabled.isChecked()}

    def accept(self):
        if self.sound_enabled.isChecked() and self.sound_file.text() \
                and not os.path.isfile(self.sound_file.text()):
            QMessageBox.warning(self, "音频不存在", "请选择存在的音频文件，或点击‘恢复默认’。")
            return
        super().accept()
