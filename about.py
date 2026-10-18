# -*- coding: utf-8 -*-
"""「关于」窗口（精简版）：程序图标 + 名称 + 版本 + 三个链接。

链接用富文本 <a>，点击直接交给系统默认浏览器打开。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
                               QLabel, QVBoxLayout)

import config

LINKS = [
    ("GitHub 仓库", "https://github.com/By-Diamond/SCP_SL_Restart"),
    ("GitHub 作者主页", "https://github.com/By-Diamond/"),
    ("Bilibili 主页", "https://space.bilibili.com/602152864"),
]


class AboutDialog(QDialog):
    def __init__(self, icon, parent=None):
        super().__init__(parent)
        self.setWindowTitle("关于 SL Restart")
        self.setWindowIcon(icon)

        logo = QLabel()
        logo.setPixmap(icon.pixmap(64, 64))
        logo.setFixedSize(64, 64)

        self.name_label = QLabel("SL Restart")
        f = self.name_label.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        self.name_label.setFont(f)

        self.version_label = QLabel("版本 %s" % config.VERSION)
        self.version_label.setStyleSheet("color: #666666;")

        head = QVBoxLayout()
        head.addWidget(self.name_label)
        head.addWidget(self.version_label)
        head.addStretch(1)

        top = QHBoxLayout()
        top.addWidget(logo)
        top.addSpacing(12)
        top.addLayout(head, 1)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)
        lay.addLayout(top)
        lay.addWidget(line)

        self.link_labels = []
        for text, url in LINKS:
            lab = QLabel('<a href="%s">%s</a>' % (url, text))
            lab.setOpenExternalLinks(True)                      # 交给系统默认浏览器
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            lab.setToolTip(url)
            self.link_labels.append(lab)
            lay.addWidget(lab)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

        # 精简版：按内容收身，别撑成一大片；但给个下限，太窄了链接挤成一坨
        hint = self.sizeHint()
        self.setFixedSize(max(hint.width(), 268), hint.height())
