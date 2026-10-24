# -*- coding: utf-8 -*-
"""编辑服务器清单：两列（地址 / 服名），支持增删行。"""

from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
                               QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)
from PySide6.QtCore import Qt

import config


class ServersDialog(QDialog):
    def __init__(self, servers, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑服务器清单")
        self.resize(520, 320)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["地址（IP:端口）", "服名"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.verticalHeader()
        header.setSectionsMovable(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setFixedWidth(48)
        header.setCursor(Qt.CursorShape.OpenHandCursor)
        header.setToolTip("按住序号前的 ≡ 拖动，调整服务器顺序")
        header.sectionMoved.connect(self._update_row_numbers)
        for s in servers:
            self._add_row(s.get("addr", ""), s.get("name", ""))

        add_btn = QPushButton("添加")
        del_btn = QPushButton("删除选中")
        add_btn.clicked.connect(lambda: self._add_row("", ""))
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
        lay.addWidget(QLabel("地址格式：IP:端口，例如 180.188.21.179:7777\n"
                            "按住序号前的 ≡ 上下拖动排序，点确定保存。"))
        lay.addLayout(body, 1)
        lay.addWidget(box)

    def _add_row(self, addr, name):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(str(addr)))
        self.table.setItem(r, 1, QTableWidgetItem(str(name)))
        self.table.setCurrentCell(r, 0)
        self._update_row_numbers()

    def _update_row_numbers(self, *_args):
        header = self.table.verticalHeader()
        for visual in range(self.table.rowCount()):
            logical = header.logicalIndex(visual)
            self.table.setVerticalHeaderItem(logical, QTableWidgetItem("≡  %d" % (visual + 1)))

    def _del_row(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows and self.table.rowCount():
            rows = [self.table.verticalHeader().logicalIndex(self.table.rowCount() - 1)]
        for r in rows:
            self.table.removeRow(r)
        self._update_row_numbers()

    def result_servers(self):
        rows = []
        for visual in range(self.table.rowCount()):
            r = self.table.verticalHeader().logicalIndex(visual)
            addr_item = self.table.item(r, 0)
            name_item = self.table.item(r, 1)
            rows.append({"addr": addr_item.text() if addr_item else "",
                         "name": name_item.text() if name_item else ""})
        return config.normalize_servers(rows)

    def accept(self):
        raws = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            raws.append(item.text() if item else "")
        bad = [a for a in raws if a.strip() and not config.parse_addr(a)]
        if bad:
            QMessageBox.warning(self, "地址不合法",
                                "下面这些地址填得不对，请改成 IP:端口 的写法：\n\n"
                                + "\n".join(bad[:8]))
            return
        canon = []
        for a in raws:
            parsed = config.parse_addr(a)
            if parsed:
                canon.append("%s:%d" % parsed)
        dup = sorted({a for a in canon if canon.count(a) > 1})
        if dup:
            QMessageBox.warning(self, "地址重复", "这些地址填了不止一次：\n\n" + "\n".join(dup))
            return
        servers = self.result_servers()
        if not servers:
            QMessageBox.warning(self, "清单为空", "至少要留一台服务器。")
            return
        super().accept()
