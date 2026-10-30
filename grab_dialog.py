# -*- coding: utf-8 -*-
"""自动挤服设置：勾选要挤的服 + 每台的人数阈值（留空 = 用该服人数上限）。

勾选的服务器可以来自服务器清单（打开时自动列出），也可以自行添加清单外的
地址——两边互不影响，删改这里不会动服务器清单。
开启自动挤服后每秒检测一次：任意勾选的服人数低于阈值，就按**表格顺序**
直连第一台，然后自动挤服自动关闭（每次开启只进一次）。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
                               QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

import config

COL_CHECK = 0
COL_ADDR = 1
COL_THR = 2


class GrabDialog(QDialog):
    def __init__(self, saved, main_servers, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自动挤服")
        self.resize(560, 360)

        self._main_names = {s["addr"]: s["name"] for s in main_servers or []}
        saved_by_addr = {s["addr"]: s for s in saved or []}

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["挤服", "地址（IP:端口）", "人数阈值"])
        self.table.horizontalHeader().setSectionResizeMode(COL_CHECK, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(COL_ADDR, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_THR, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        for s in main_servers or []:                     # 先列清单里的服
            prev = saved_by_addr.get(s["addr"], {})
            self._add_row(s["addr"], enabled=bool(prev.get("enabled", False)),
                          threshold=prev.get("threshold"), name=s.get("name", ""))
        for s in saved or []:                            # 再列之前添加的自定义服
            if s.get("addr") not in self._main_names and config.parse_addr(s.get("addr")):
                self._add_row(s["addr"], enabled=bool(s.get("enabled", False)),
                              threshold=s.get("threshold"))

        add_btn = QPushButton("添加自定义服务器")
        del_btn = QPushButton("删除选中")
        add_btn.clicked.connect(lambda: self._add_row(""))
        del_btn.clicked.connect(self._del_row)

        side = QVBoxLayout()
        side.addWidget(add_btn)
        side.addWidget(del_btn)
        side.addStretch(1)

        body = QHBoxLayout()
        body.addWidget(self.table, 1)
        body.addLayout(side)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("勾选要挤的服务器（可添加清单外的地址，与服务器清单互不影响）；"
                             "人数阈值留空 = 用该服人数上限。"))
        lay.addWidget(QLabel("开启后每秒检测一次：任意勾选的服人数低于阈值，"
                             "就按表格顺序直连第一台，然后自动挤服自动关闭。"))
        lay.addLayout(body, 1)
        lay.addWidget(box)

    def _add_row(self, addr, enabled=False, threshold=None, name=""):
        r = self.table.rowCount()
        self.table.insertRow(r)
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                       | Qt.ItemFlag.ItemIsSelectable)
        check.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        check.setToolTip("勾上：开启自动挤服时监控这台服")
        self.table.setItem(r, COL_CHECK, check)
        self.table.setItem(r, COL_ADDR, QTableWidgetItem(str(addr)))
        self.table.setItem(r, COL_THR,
                           QTableWidgetItem("" if threshold is None else str(threshold)))
        if name:
            self.table.item(r, COL_ADDR).setToolTip("清单服务器：%s" % name)
        self.table.setCurrentCell(r, COL_ADDR)

    def _del_row(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows and self.table.rowCount():
            rows = [self.table.rowCount() - 1]
        for r in rows:
            self.table.removeRow(r)

    def result_servers(self):
        """[{"addr","name","enabled","threshold"}]，threshold=None 表示用人数上限。"""
        rows = []
        for r in range(self.table.rowCount()):
            addr_item = self.table.item(r, COL_ADDR)
            thr_item = self.table.item(r, COL_THR)
            check = self.table.item(r, COL_CHECK)
            addr = (addr_item.text() if addr_item else "").strip()
            parsed = config.parse_addr(addr)
            addr = "%s:%d" % parsed if parsed else addr
            text = (thr_item.text() if thr_item else "").strip()
            rows.append({"addr": addr,
                         "name": self._main_names.get(addr, ""),
                         "enabled": bool(check and
                                         check.checkState() == Qt.CheckState.Checked),
                         "threshold": int(text) if text else None})
        return rows

    def accept(self):
        addrs, seen = [], set()
        for r in range(self.table.rowCount()):
            addr_item = self.table.item(r, COL_ADDR)
            thr_item = self.table.item(r, COL_THR)
            addr = (addr_item.text() if addr_item else "").strip()
            text = (thr_item.text() if thr_item else "").strip()
            if not addr:
                continue                                 # 空行跳过
            if not config.parse_addr(addr):
                QMessageBox.warning(self, "地址不合法",
                                    "第 %d 行的地址填得不对，请改成 IP:端口 的写法。" % (r + 1))
                return
            canonical = "%s:%d" % config.parse_addr(addr)
            addr_item.setText(canonical)
            if canonical in seen:
                QMessageBox.warning(self, "地址重复",
                                    "%s 填了不止一次，请删掉多余的行。" % canonical)
                return
            seen.add(canonical)
            if text and (not text.isdigit() or not 1 <= int(text) <= 999):
                QMessageBox.warning(self, "人数阈值不对",
                                    "第 %d 行的人数阈值要留空（用人数上限）或填 1-999 的整数。"
                                    % (r + 1))
                return
            addrs.append(canonical)
        super().accept()
