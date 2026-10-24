# -*- coding: utf-8 -*-
"""离屏提醒测试：不播放声音，不发送系统通知，不联网。"""

import base64
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

import config
from alerts import (NOTIFICATION_APP_ID, RestartAlerts, build_toast_xml,
                    notification_command, register_notification_identity)
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
        events = [("1服", "1.1.1.1:7777"), ("3服", "1.1.1.1:7779")]
        with patch.object(self.alerts, "play_sound") as sound, \
                patch.object(self.alerts, "show_notification") as notify:
            self.alerts.notify_restart(events, self.cfg)
            sound.assert_not_called()
            notify.assert_not_called()
            self.cfg["sound_enabled"] = True
            self.alerts.notify_restart(events, self.cfg)
            sound.assert_called_once_with("", 70)
            notify.assert_not_called()
            sound.reset_mock()
            self.cfg.update(sound_enabled=False, windows_notifications=True)
            self.alerts.notify_restart(events, self.cfg)
            sound.assert_not_called()
            # 一台服一条通知，标题/正文维持原样式，各自带对应服的进服链接
            self.assertEqual(notify.call_count, 2)
            first = notify.call_args_list[0]
            self.assertEqual(first.args[0], "SCP:SL 服务器重启")
            self.assertEqual(first.args[1], "[1服] 重启")
            self.assertEqual(first.kwargs["join_url"], "slrestart://join/1.1.1.1:7777")
            second = notify.call_args_list[1]
            self.assertEqual(second.args[1], "[3服] 重启")
            self.assertEqual(second.kwargs["join_url"], "slrestart://join/1.1.1.1:7779")

    def test_notification_xml_escape_and_static_command(self):
        title = "SCP:SL 重启"
        message = "[<服&名>] ' ; $(calc)\n新一行"
        xml = build_toast_xml(title, message)
        root = ET.fromstring(xml)
        self.assertEqual([e.text for e in root.findall("./visual/binding/text")], [title, message])
        self.assertEqual(root.find("audio").attrib["silent"], "true")
        # 脚本静态：启动参数（含 EncodedCommand）每次完全相同，数据走环境变量
        args = notification_command()
        self.assertEqual(notification_command(), args)
        self.assertNotIn("-Command", args)
        self.assertIn("-EncodedCommand", args)
        script = base64.b64decode(args[-1]).decode("utf-16-le")
        self.assertNotIn(message, script)
        self.assertNotIn(title, script)
        self.assertIn("$xml.LoadXml($env:OPB_XML)", script)
        self.assertIn("CreateToastNotifier($env:OPB_AUMID)", script)
        self.assertNotIn(" ", NOTIFICATION_APP_ID)
        self.assertLessEqual(len(NOTIFICATION_APP_ID), 128)

    def test_toast_button_join_url(self):
        url = "slrestart://join/1.2.3.4:7777"
        root = ET.fromstring(build_toast_xml("[3服] 重启", "内容", url))
        self.assertEqual(root.attrib["activationType"], "protocol")
        self.assertEqual(root.attrib["activation"], url)      # 点通知本体 = 进服
        action = root.find("./actions/action")
        self.assertEqual(action.attrib["content"], "进服")
        self.assertEqual(action.attrib["arguments"], url)     # 点按钮 = 进服那一台
        # 不带链接的通知（提醒设置里的「测试通知」）没有按钮
        plain = ET.fromstring(build_toast_xml("标题", "内容"))
        self.assertNotIn("activationType", plain.attrib)
        self.assertIsNone(plain.find("./actions"))

    def test_join_protocol_registered_for_buttons(self):
        import winreg
        with patch("winreg.CreateKey") as create, patch("winreg.SetValueEx") as set_value:
            create.return_value.__enter__ = lambda self: create.return_value
            create.return_value.__exit__ = lambda self, *args: False
            register_notification_identity()
        paths = [call.args[1] for call in create.call_args_list]
        self.assertIn(r"Software\Classes\slrestart", paths)
        self.assertIn(r"Software\Classes\slrestart\shell\open\command", paths)
        values = [call.args[1] for call in set_value.call_args_list]
        self.assertIn("URL Protocol", values)

    def test_join_url_cli_joins_and_exits(self):
        import sl_restart
        with patch("sl_restart.joiner.join") as join:
            join.return_value = (True, "steam://connect/1.2.3.4:7777")
            rc = sl_restart.main(["sl_restart.py", "slrestart://join/1.2.3.4:7777"])
        self.assertEqual(rc, 0)
        join.assert_called_once_with("1.2.3.4:7777")
        with patch("sl_restart.joiner.join") as join:
            rc = sl_restart.main(["sl_restart.py", "slrestart://join/坏地址"])
            self.assertEqual(rc, 0)
            join.assert_not_called()

    def test_script_checks_setting_before_showing(self):
        script = base64.b64decode(notification_command()[-1]).decode("utf-16-le")
        self.assertIn("$notifier.Setting", script)
        self.assertIn("if ($setting -and $setting -ne 'Enabled')", script)
        self.assertNotIn("$notifier.Setting.ToString()", script)
        self.assertNotIn("-Name DisplayName", script)   # 注册表改由 winreg 写，脚本不再碰

    def test_aumid_written_via_winreg_with_display_name(self):
        import winreg
        with patch("winreg.CreateKey") as create, patch("winreg.SetValueEx") as set_value:
            create.return_value.__enter__ = lambda self: create.return_value
            create.return_value.__exit__ = lambda self, *args: False
            ok, _msg = register_notification_identity()
        self.assertTrue(ok)
        paths = [call.args[1] for call in create.call_args_list]
        self.assertTrue(any(NOTIFICATION_APP_ID in p for p in paths))
        names = [call.args[1] for call in set_value.call_args_list]
        self.assertIn("DisplayName", names)

    def test_blocked_notification_reports_blocked_instead_of_success(self):
        messages = []
        self.alerts.status.connect(messages.append)
        self.alerts._report_notification_result(
            2, "SL_RESTART_NOTIFICATION_BLOCKED:DisabledForApplication", "")
        self.assertIn("Windows 已关闭此应用的通知", messages[-1])

    def test_notification_uses_worker_thread_and_cleans_up(self):
        with patch("alerts.sys.platform", "win32"), \
                patch("alerts._powershell_exe", return_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"), \
                patch("alerts.subprocess.run") as run, \
                patch("alerts.register_notification_identity") as register:
            run.return_value = Mock(returncode=0,
                                    stdout="SL_RESTART_NOTIFICATION_SENT", stderr="")
            self.alerts.show_notification("标题", "内容")
            register.assert_called_once()
            self.alerts.stop()                       # join 后台线程后再断言
            self.assertTrue(run.called)
            kwargs = run.call_args.kwargs
            self.assertEqual(kwargs["creationflags"], 0x08000000)   # CREATE_NO_WINDOW，不闪黑窗
            self.assertEqual(kwargs["env"]["OPB_AUMID"], NOTIFICATION_APP_ID)
            self.assertIn("标题", kwargs["env"]["OPB_XML"])          # 数据走环境变量，不进脚本
            self.assertFalse([t for t in self.alerts._threads if t.is_alive()])

    def test_aumid_failure_does_not_block_sending(self):
        messages = []
        self.alerts.status.connect(messages.append)
        with patch("alerts.sys.platform", "win32"), \
                patch("alerts._powershell_exe", return_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"), \
                patch("alerts.subprocess.run") as run, \
                patch("alerts.register_notification_identity", side_effect=OSError("access denied")):
            run.return_value = Mock(returncode=0,
                                    stdout="SL_RESTART_NOTIFICATION_SENT", stderr="")
            self.alerts.show_notification("标题", "内容")
            self.alerts.stop()
            run.assert_called_once()                 # 注册失败只记日志，照样发通知

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
                ui._push_restart([("1服", "127.0.0.1:7777"), ("3服", "127.0.0.1:7779")])  # 本地差异：事件带地址
                notify.assert_called_once_with(
                    [("1服", "127.0.0.1:7777"), ("3服", "127.0.0.1:7779")], self.cfg)
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
