# -*- coding: utf-8 -*-
"""离屏验证拖动和保存；不启动采集线程，不联网，不写注册表。"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import config
from overlay import Overlay
from server_dialog import ServersDialog
from sl_restart import App


class DragTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_base = config._BASE
        config.set_base_dir(self.tmp.name)
        self.cfg = config.load()
        with patch("sl_restart.Monitor.start"), patch("sl_restart.Tray.show"), \
                patch("sl_restart.autostart.is_enabled", return_value=False):
            self.ui = App(self.cfg, self.app)
        self.ui.timer.stop()
        self.dialogs = []

    def tearDown(self):
        self.ui.monitor.stop()
        self.ui.tray.icon.hide()
        for widget in [self.ui.counter, self.ui.toast, *self.dialogs]:
            widget.close()
        config.set_base_dir(self.old_base)
        self.tmp.cleanup()

    def dialog(self):
        dlg = ServersDialog(self.cfg["servers"])
        self.dialogs.append(dlg)
        return dlg

    def test_move_save_reload_and_lock(self):
        counter = self.ui.counter
        transparent = Qt.WindowType.WindowTransparentForInput
        self.assertTrue(counter.windowFlags() & transparent)
        self.ui.tray.acts["move_counter"].trigger()
        self.assertFalse(counter.windowFlags() & transparent)
        self.assertTrue(self.ui.toast.windowFlags() & transparent)
        start = counter.pos()
        local = QPoint(8, 8)
        QTest.mousePress(counter, Qt.MouseButton.LeftButton, pos=local)
        target = start + QPoint(120, 70)
        for kind, button, buttons in [
                (QEvent.Type.MouseMove, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
                (QEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)]:
            event = QMouseEvent(kind, QPointF(local), QPointF(target + local),
                                button, buttons, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(counter, event)
            self.assertEqual(self.ui.toast.pos(), target + QPoint(0, 38))
        self.assertEqual(counter.pos(), target)
        self.assertEqual(config.load()["counter_pos"], [target.x(), target.y()])
        self.assertEqual(config.load()["restart_pos"], [target.x(), target.y() + 38])
        self.ui.toast.pop("[1服] 重启")
        self.assertEqual(self.ui.toast.pos(), target + QPoint(0, 38))
        counter.set_text("[更新后的服务器] 42人")
        self.ui._toggle_counter(False)
        self.ui._toggle_counter(True)
        self.ui.tray.acts["move_counter"].trigger()
        self.assertEqual(counter.pos(), target)
        self.assertTrue(counter.isVisible())
        self.assertTrue(counter.windowFlags() & transparent)
        reloaded = Overlay(config.load()["counter_pos"], 16)
        self.dialogs.append(reloaded)
        reloaded.set_visible(True)
        self.assertEqual(reloaded.pos(), target)

    def test_visible_toast_follows_and_old_position_is_repaired(self):
        self.ui.toast.pop("[3服] 重启")
        self.ui.counter.position_moved.emit(320, 180)
        self.assertEqual(self.ui.toast.pos(), QPoint(320, 218))
        self.ui._save_counter_position(320, 180)
        self.ui.toast.hide()
        self.ui.counter.set_position((320, 180))
        self.ui._push_restart(["1服"])
        self.assertEqual(self.ui.toast.pos(), QPoint(320, 218))
        cfg = config.load()
        cfg["counter_pos"] = [400, 250]
        cfg["restart_pos"] = [10, 48]
        with patch("sl_restart.Monitor.start"), patch("sl_restart.Tray.show"), \
                patch("sl_restart.autostart.is_enabled", return_value=False):
            restored = App(cfg, self.app)
        restored.timer.stop()
        self.dialogs.extend([restored.counter, restored.toast])
        try:
            self.assertEqual(restored.toast.pos(), QPoint(400, 288))
        finally:
            restored.monitor.stop()
            restored.alerts.stop()
            restored.tray.icon.hide()

    def test_unlock_hidden_counter(self):
        self.ui._toggle_counter(False)
        self.ui.tray.acts["move_counter"].trigger()
        self.assertTrue(self.ui.counter.isVisible())
        self.assertTrue(self.ui.tray.acts["counter"].isChecked())

    def test_drag_header_with_mouse(self):
        dlg = self.dialog()
        dlg.show()
        self.app.processEvents()
        header = dlg.table.verticalHeader()
        x = 10
        first = QPoint(x, header.sectionViewportPosition(0) + header.sectionSize(0) // 2)
        last = QPoint(x, header.sectionViewportPosition(2) + header.sectionSize(2) - 3)
        QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=first)
        for y in range(first.y(), last.y() + 1, 3):
            QTest.mouseMove(header.viewport(), QPoint(x, y), delay=5)
        QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=last)
        self.assertEqual([s["name"] for s in dlg.result_servers()], ["3服", "5服", "1服"])
        self.assertEqual([dlg.table.verticalHeaderItem(header.logicalIndex(i)).text()
                          for i in range(3)], ["≡  1", "≡  2", "≡  3"])

    def test_reorder_edit_add_delete_and_reverse(self):
        dlg = self.dialog()
        header = dlg.table.verticalHeader()
        header.moveSection(2, 0)
        dlg.table.item(2, 1).setText("改名5服")
        dlg._add_row("127.0.0.1:7783", "7服")
        self.assertEqual([s["name"] for s in dlg.result_servers()],
                         ["改名5服", "1服", "3服", "7服"])
        dlg.table.selectRow(header.logicalIndex(1))
        dlg._del_row()
        self.assertEqual([s["name"] for s in dlg.result_servers()], ["改名5服", "3服", "7服"])
        header.moveSection(0, 2)
        self.assertEqual([s["name"] for s in dlg.result_servers()], ["3服", "7服", "改名5服"])
        self.assertEqual(dlg.result_servers()[-1]["addr"], "180.188.21.179:7781")
        dlg.table.clearSelection()
        dlg._del_row()
        self.assertEqual([s["name"] for s in dlg.result_servers()], ["3服", "7服"])

    def test_confirm_and_cancel(self):
        original = list(self.cfg["servers"])

        def finish(dlg, accepted):
            self.dialogs.append(dlg)
            dlg.table.verticalHeader().moveSection(2, 0)
            dlg.accept() if accepted else dlg.reject()
            return dlg.result()

        with patch.object(ServersDialog, "exec", lambda dlg: finish(dlg, False)):
            self.ui._edit_servers()
        self.assertEqual(self.cfg["servers"], original)
        self.assertFalse(os.path.exists(config.path("settings.json")))
        with patch.object(ServersDialog, "exec", lambda dlg: finish(dlg, True)):
            self.ui._edit_servers()
        expected = [original[2], original[0], original[1]]
        self.assertEqual(config.load()["servers"], expected)
        self.assertEqual(self.ui.counter.text, "[5服] -人  |  [1服] -人  |  [3服] -人")
        self.assertEqual([s["name"] for s in ServersDialog(config.load()["servers"]).result_servers()],
                         ["5服", "1服", "3服"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
