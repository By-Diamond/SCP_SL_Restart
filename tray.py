# -*- coding: utf-8 -*-
"""托盘图标 + 右键菜单。只负责摆菜单，具体动作由调用方注入。

图标用 QPainter 现画（暗红底 + SL），不依赖外部文件，
这样打包成单文件 exe 也不用额外带资源。
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def make_icon(size=64):
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#8e1b1b"))
    r = size * 0.06
    p.drawRoundedRect(QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92), r, r)
    p.setPen(QColor("#ffffff"))
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(int(size * 0.5))
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), int(Qt.AlignmentFlag.AlignCenter), "SL")
    p.end()
    return QIcon(pm)


class Tray:
    def __init__(self, app, icon=None):
        self.app = app
        self.icon = QSystemTrayIcon(icon or make_icon(), app)
        self.menu = QMenu()
        self.icon.setContextMenu(self.menu)
        self.acts = {}

    def add(self, key, text, callback, checkable=False, checked=False):
        a = QAction(text, self.menu)
        a.setCheckable(bool(checkable))
        a.setChecked(bool(checked))
        a.triggered.connect(lambda _=False, cb=callback: cb())
        self.menu.addAction(a)
        self.acts[key] = a
        return a

    def sep(self):
        self.menu.addSeparator()

    def set_text(self, key, text):
        self.acts[key].setText(text)

    def set_checked(self, key, flag):
        self.acts[key].setChecked(bool(flag))

    def tip(self, text):
        self.icon.setToolTip(text)

    def show(self):
        self.icon.show()

    def on_click(self, callback):
        """左键单击托盘图标（右键由菜单处理）。"""
        self.icon.activated.connect(
            lambda reason: callback() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
