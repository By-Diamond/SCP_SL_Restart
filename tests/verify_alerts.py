# -*- coding: utf-8 -*-
"""离屏提醒测试：不播放声音，不发送系统通知，不联网。"""

import base64
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

import config
from alerts import RestartAlerts, notification_command
from alert_dialog import AlertDialog
from sl_restart import App


class AlertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_base = config._BASE
        config.set_base_dir(self.tmp.name)
        self.cfg = config.load()
        self.alerts = RestartAlerts()
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
        self.alerts.stop()
        config.set_base_dir(self.old_base)
        self.tmp.cleanup()

    def test_defaults_and_roundtrip(self):
        self.assertFalse(self.cfg["sound_enabled"])
        self.assertFalse(self.cfg["windows_notifications"])
        self.cfg.update(sound_enabled=True, sound_file="C:/声音/提醒.mp3", sound_volume=43,
                        windows_notifications=True)
        config.save(self.cfg)
        loaded = config.load()
        for key in ("sound_enabled", "sound_file", "sound_volume", "windows_notifications"):
            self.assertEqual(loaded[key], self.cfg[key])
        self.cfg.update(sound_volume="broken", sound_file=[], sound_enabled="false")
        config.save(self.cfg)
        loaded = config.load()
        self.assertEqual(loaded["sound_volume"], 70)
        self.assertEqual(loaded["sound_file"], "")
        self.assertFalse(loaded["sound_enabled"])

    def test_independent_switches_and_batching(self):
        with patch.object(self.alerts, "play_sound") as sound, \
                patch.object(self.alerts, "show_notification") as notify:
            self.alerts.notify_restart(["1服", "3服"], self.cfg)
            sound.assert_not_called()
            notify.assert_not_called()
            self.cfg["sound_enabled"] = True
            self.alerts.notify_restart(["1服", "3服"], self.cfg)
            sound.assert_called_once_with("", 70)
            notify.assert_not_called()
            sound.reset_mock()
            self.cfg.update(sound_enabled=False, windows_notifications=True)
            self.alerts.notify_restart(["1服", "3服"], self.cfg)
            sound.assert_not_called()
            notify.assert_called_once_with("SCP:SL 服务器重启", "[1服] [3服] 重启")

    def test_notification_encoding_and_xml_escape(self):
        title = "SCP:SL 重启"
        message = "[<服&名>] ' ; $(calc)\n新一行"
        args = notification_command(title, message)
        self.assertIn("Hidden", args)
        self.assertNotIn("-Command", args)
        script = base64.b64decode(args[-1]).decode("utf-16-le")
        self.assertNotIn(message, script)
        encoded_xml = script.split("FromBase64String('", 1)[1].split("')", 1)[0]
        root = ET.fromstring(base64.b64decode(encoded_xml))
        self.assertEqual([e.text for e in root.findall("./visual/binding/text")], [title, message])
        self.assertEqual(root.find("audio").attrib["silent"], "true")
        self.assertIn("CreateToastNotifier('SL Restart')", script)

    def test_notification_uses_async_process_and_cleans_up(self):
        with patch("alerts.sys.platform", "win32"), patch("alerts.os.path.isfile", return_value=True), \
                patch.object(QProcess, "start") as start:
            self.alerts.show_notification("标题", "内容")
            start.assert_called_once()
            process = next(iter(self.alerts._processes))
            self.assertTrue(process.program().endswith("powershell.exe"))
            self.assertEqual(process.arguments(), notification_command("标题", "内容"))
            process.finished.emit(0, QProcess.ExitStatus.NormalExit)
            self.assertFalse(self.alerts._processes)

    def test_settings_cancel_does_not_save(self):
        with patch("sl_restart.Monitor.start"), patch("sl_restart.Tray.show"), \
                patch("sl_restart.autostart.is_enabled", return_value=False):
            ui = App(self.cfg, self.app)
        ui.timer.stop()
        self.widgets.extend([ui.counter, ui.toast])
        try:
            with patch.object(AlertDialog, "exec", return_value=0):
                ui._edit_alerts()
            self.assertFalse(self.cfg["sound_enabled"])
            self.assertFalse(os.path.exists(config.path("settings.json")))
        finally:
            ui.monitor.stop()
            ui.alerts.stop()
            ui.tray.icon.hide()

    def test_decoder_failure_fallback_and_stop_cancel(self):
        self.alerts._custom_sound = True
        with patch.object(self.alerts, "play_sound") as sound:
            self.alerts._sound_error(None, "unsupported audio")
            self.app.processEvents()
            sound.assert_called_once_with("", 70)
            sound.reset_mock()
            self.alerts._custom_sound = True
            self.alerts._sound_error(None, "unsupported audio")
            self.alerts.stop_sound()
            self.app.processEvents()
            sound.assert_not_called()

    def test_audio_path_volume_and_missing_file_fallback(self):
        custom = os.path.join(self.tmp.name, "声音.mp3")
        with open(custom, "wb") as stream:
            stream.write(b"test")
        with patch("alerts.QMediaPlayer") as player_type, patch("alerts.QAudioOutput") as output_type:
            self.alerts.play_sound(custom, 43)
            output_type.return_value.setVolume.assert_called_with(0.43)
            source = player_type.return_value.setSource.call_args.args[0]
            self.assertEqual(os.path.normpath(source.toLocalFile()), os.path.normpath(custom))
            player_type.return_value.play.assert_called_once()
        other = RestartAlerts()
        with patch("alerts.default_sound_file", return_value=custom), \
                patch("alerts.QMediaPlayer") as player_type, patch("alerts.QAudioOutput"):
            other.play_sound(os.path.join(self.tmp.name, "missing.wav"), 70)
            self.assertEqual(player_type.return_value.setSource.call_args.args[0].toLocalFile(),
                             custom.replace("\\", "/"))
        other.stop()

    def test_dialog_cancel_and_accept(self):
        dlg = AlertDialog(self.cfg, self.alerts)
        self.widgets.append(dlg)
        dlg.sound_enabled.setChecked(True)
        dlg.notifications_enabled.setChecked(True)
        dlg.volume.setValue(31)
        dlg.reject()
        self.assertFalse(self.cfg["sound_enabled"])
        self.assertFalse(self.cfg["windows_notifications"])
        dlg.accept()
        self.assertEqual(dlg.result_settings(), {
            "sound_enabled": True, "sound_file": "", "sound_volume": 31,
            "windows_notifications": True})

    def test_dialog_browse_preview_and_notification_test(self):
        dlg = AlertDialog(self.cfg, self.alerts)
        self.widgets.append(dlg)
        custom = os.path.join(self.tmp.name, "自定义.wav")
        with patch("alert_dialog.QFileDialog.getOpenFileName", return_value=(custom, "")):
            dlg._choose_sound()
        self.assertEqual(dlg.sound_file.text(), custom)
        with patch.object(dlg._preview, "play_sound") as sound:
            dlg._test_sound()
            sound.assert_called_once_with(custom, 70)
        with patch.object(self.alerts, "show_notification") as notify:
            dlg._test_notification()
            notify.assert_called_once()
        with patch("alert_dialog.QMessageBox.warning") as warning:
            dlg.sound_enabled.setChecked(True)
            dlg.accept()
            warning.assert_called_once()
            self.assertEqual(dlg.result(), 0)

    def test_app_integration_and_settings_save(self):
        with patch("sl_restart.Monitor.start"), patch("sl_restart.Tray.show"), \
                patch("sl_restart.autostart.is_enabled", return_value=False):
            ui = App(self.cfg, self.app)
        ui.timer.stop()
        self.widgets.extend([ui.counter, ui.toast])
        try:
            with patch.object(ui.alerts, "notify_restart") as notify:
                ui._push_restart(["1服", "3服"])
                notify.assert_called_once_with(["1服", "3服"], self.cfg)
            def finish(dlg):
                self.widgets.append(dlg)
                dlg.sound_enabled.setChecked(True)
                dlg.volume.setValue(23)
                dlg.accept()
                return dlg.result()
            with patch.object(AlertDialog, "exec", finish):
                ui.tray.acts["alerts"].trigger()
            self.assertTrue(config.load()["sound_enabled"])
            self.assertEqual(config.load()["sound_volume"], 23)
        finally:
            ui.monitor.stop()
            ui.alerts.stop()
            ui.tray.icon.hide()


if __name__ == "__main__":
    unittest.main(verbosity=2)
