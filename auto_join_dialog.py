# -*- coding: utf-8 -*-
"""重启自动进服：只从「编辑服务器清单…」里多选服务器。

开启后监控重启事件，名单里**谁先重启就进谁**；进服后本功能自动关闭
（一次开启只进一台，想再进就再勾上）。不提供自定义地址——要加服先去清单里加。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QListWidget,
                               QListWidgetItem, QVBoxLayout)


class AutoJoinDialog(QDialog):
    def __init__(self, servers, saved=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("重启自动进服")
        self.resize(420, 340)
        saved = {s for s in saved or ()}

        self.list = QListWidget(self)
        for s in servers or ():
            item = QListWidgetItem("[%s]  %s" % (s.get("name") or "?", s["addr"]))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if s["addr"] in saved
                               else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, s["addr"])
            self.list.addItem(item)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("勾选要自动进的服务器（只能选「编辑服务器清单…」里的）："))
        lay.addWidget(self.list, 1)
        lay.addWidget(QLabel("谁先重启就进谁；进服后本功能自动关闭，一次开启只进一台。"))
        lay.addWidget(box)

    def result_addrs(self):
        """按列表顺序返回勾选的地址。"""
        out = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out
