# -*- coding: utf-8 -*-
"""SL Restart —— SCP:SL 服务器重启提示。

托盘常驻、无控制台窗口；
左上角常驻人数条，某台服人数 **>0 掉到 0** 时弹红色重启提示 10 秒。
"""

import argparse
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

import autostart
import config
from about import AboutDialog
from alert_dialog import AlertDialog
from alerts import RestartAlerts
from monitor import Monitor
from overlay import Overlay
from server_dialog import ServersDialog
from tray import Tray

SYNC_MS = 200


def single_instance(name="SL_Restart_instance"):
    """已有实例在跑就直接返回 None（本地 socket，进程挂掉后会自动释放）。"""
    try:
        from PySide6.QtNetwork import QLocalServer, QLocalSocket
    except ImportError:
        return None
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(300):
        return None
    QLocalServer.removeServer(name)
    srv = QLocalServer()
    srv.listen(name)
    return srv


class App:
    def __init__(self, cfg, app):
        self.cfg = cfg
        self.app = app
        self.monitor = Monitor(cfg)
        self.alerts = RestartAlerts(app)
        self.counter = Overlay(cfg["counter_pos"], cfg["counter_font"],
                               duration=None, color="#FFFFFF", bold=False,
                               alpha=cfg["alpha"])
        self.counter.position_changed.connect(self._save_counter_position)
        self.counter.position_moved.connect(self._position_toast)
        self.toast = Overlay(cfg["restart_pos"], cfg["restart_font"],
                             duration=cfg["restart_seconds"],
                             color=cfg["restart_color"], bold=True,
                             alpha=cfg["alpha"])
        self._toast_names = []          # 当前这条提示里已经并排显示的重启服名
        self.tray = Tray(app)
        self._build_menu()

        self.counter.set_text(self.monitor.snapshot["text"])
        self.counter.set_visible(True)
        self._position_toast(*cfg["counter_pos"])
        self.tray.tip("SL Restart · 启动中…")
        self.tray.show()
        self.tray.on_click(lambda: self._toggle_counter(not self.counter.isVisible()))

        self.monitor.start()
        self.timer = QTimer()
        self.timer.timeout.connect(self._sync)
        self.timer.start(SYNC_MS)

    # ------------------------------ 菜单 ------------------------------

    def _build_menu(self):
        t = self.tray
        t.add("counter", "显示人数条",
              lambda: self._toggle_counter(t.acts["counter"].isChecked()),
              checkable=True, checked=True)
        t.add("move_counter", "移动人数条", self._toggle_move_counter,
              checkable=True)
        t.add("pause", "暂停监控", lambda: self._toggle_pause())
        t.add("refresh", "立即刷新", self.monitor.refresh_now)
        t.sep()
        t.add("servers", "编辑服务器清单…", self._edit_servers)
        t.add("alerts", "提醒设置…", self._edit_alerts)
        t.add("autostart", "开机自启", self._toggle_autostart,
              checkable=True, checked=autostart.is_enabled())
        t.sep()
        t.add("about", "关于", self._about)
        t.add("quit", "退出程序", self.quit)

    def _toggle_counter(self, flag):
        self.counter.set_visible(flag)
        self.tray.set_checked("counter", flag)
        self.tray.tip("SL Restart · " + self.monitor.snapshot["text"])

    def _toggle_pause(self):
        paused = not self.monitor.paused
        self.monitor.set_paused(paused)
        self.tray.set_text("pause", "继续监控" if paused else "暂停监控")
        self.tray.tip("SL Restart · " + ("已暂停" if paused else self.monitor.snapshot["text"]))
        config.log("[ui] %s监控" % ("暂停" if paused else "继续"))

    def _toggle_move_counter(self):
        movable = self.tray.acts["move_counter"].isChecked()
        self.counter.set_movable(movable)
        if movable:
            self._toggle_counter(True)

    def _save_counter_position(self, x, y):
        self.cfg["counter_pos"] = [x, y]
        self._position_toast(x, y)
        self.cfg["restart_pos"] = [self.toast.x(), self.toast.y()]
        try:
            config.save(self.cfg)
        except Exception as e:
            config.log("[ui] 保存人数条位置失败：%r" % e)

    def _position_toast(self, x, y):
        gap = max(38, self.counter.height() + 8)
        self.toast.set_position((x, y + gap))

    def _edit_alerts(self):
        dlg = AlertDialog(self.cfg, self.alerts)
        if dlg.exec() != AlertDialog.DialogCode.Accepted:
            return
        updated = dict(self.cfg, **dlg.result_settings())
        try:
            config.save(updated)
        except Exception as e:
            config.log("[ui] 保存提醒设置失败：%r" % e)
            QMessageBox.warning(None, "保存失败", "提醒设置未保存，请检查程序目录是否可写。")
            return
        self.cfg.update(dlg.result_settings())
        if not self.cfg["sound_enabled"]:
            self.alerts.stop_sound()

    def _toggle_autostart(self):
        want = self.tray.acts["autostart"].isChecked()
        ok, msg = autostart.set_enabled(want)
        if not ok:
            self.tray.set_checked("autostart", autostart.is_enabled())
        self.tray.icon.showMessage("SL Restart", msg,
                                   QSystemTrayIcon.MessageIcon.Information, 3000)
        config.log("[ui] 开机自启=%s：%s" % (want, msg))

    def _edit_servers(self):
        dlg = ServersDialog(self.cfg["servers"])
        if dlg.exec() != ServersDialog.DialogCode.Accepted:
            return
        servers = dlg.result_servers()
        self.cfg["servers"] = servers
        try:
            config.save(self.cfg)
        except Exception as e:
            config.log("[ui] 保存 settings.json 失败：%r" % e)
        self.monitor.set_servers(servers)
        self.counter.set_text(self.monitor.snapshot["text"], ensure_shown=False)
        self._toggle_counter(True)
        config.log("[ui] 服务器清单已更新：%s" % [s["addr"] for s in servers])

    def _about(self):
        config.log("[ui] 打开关于窗口")
        AboutDialog(self.tray.icon.icon()).exec()

    def quit(self):
        self.monitor.stop()
        self.alerts.stop()
        self.app.quit()

    # ------------------------------ 同步 ------------------------------

    def _sync(self):
        snap = self.monitor.snapshot
        self.counter.set_text(snap["text"], ensure_shown=False)   # 显隐由菜单/托盘左键决定
        self.tray.tip("SL Restart · " + (snap["text"] or "…"))
        names = self.monitor.take_restarts()
        if names:
            self._push_restart(names)

    def _push_restart(self, names):
        """重启提示：同一轮里几台服一起重启，就把服名并排显示成一条。

        不能一台一条 pop——后一条会盖掉前一条（信息丢失）。所以攒成：
        `[1服] [3服] 重启`；提示还在显示时又来一台，就并进去并把 10 秒倒计时重置。
        """
        if not self.toast.isVisible():          # 上一条提示已经放完了，重新开始攒
            self._toast_names = []
        for n in names:
            if n not in self._toast_names:
                self._toast_names.append(n)
        self._position_toast(self.counter.x(), self.counter.y())
        self.toast.pop("%s 重启" % " ".join("[%s]" % n for n in self._toast_names))
        self.alerts.notify_restart(names, self.cfg)
        config.log("[ui] 弹重启提示：%s" % " ".join(names))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="SL Restart", description="SCP:SL 服务器重启提示")
    ap.add_argument("--smoke", type=float, default=0,
                    help="开发用：跑 N 秒后自动退出（验证用）")
    ap.add_argument("--about-grab", metavar="PNG", default="",
                    help="开发用：把关于窗口画成 PNG 后退出（验证打包后可用）")
    ap.add_argument("--alerts-grab", metavar="PNG", default="",
                    help="开发用：把提醒设置画成 PNG 后退出（不播放声音或发送通知）")
    args = ap.parse_args(argv)

    cfg = config.load()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("SL Restart")
    app.setApplicationVersion(config.VERSION)
    app.setQuitOnLastWindowClosed(False)

    if args.alerts_grab:
        return 0 if AlertDialog(cfg).grab().save(args.alerts_grab) else 1

    if args.about_grab:
        from about import AboutDialog
        from tray import make_icon
        return 0 if AboutDialog(make_icon()).grab().save(args.about_grab) else 1

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "SL Restart", "系统托盘不可用，程序无法常驻。")
        return 2

    guard = single_instance()
    if guard is None:
        config.log("[app] 已有实例在运行，本次启动退出")
        return 0

    ui = App(cfg, app)
    config.log("[app] 启动：%s" % [s["addr"] for s in cfg["servers"]])
    if args.smoke:
        QTimer.singleShot(int(args.smoke * 1000), ui.quit)
    try:
        return app.exec()
    finally:
        ui.monitor.stop()
        config.log("[app] 退出")


if __name__ == "__main__":
    sys.exit(main())
