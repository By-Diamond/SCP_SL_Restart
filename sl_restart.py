# -*- coding: utf-8 -*-
"""SL Restart —— SCP:SL 服务器重启提示。

托盘常驻、无控制台窗口；
左上角常驻人数条，某台服人数 **>0 掉到 0** 时弹红色重启提示 10 秒。
提示**可点击**：点一下直接 steam://connect 进「晚重启的那台服」。
"""

import argparse
import os
import sys
import threading
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

import autostart
import config
from about import AboutDialog
from alert_dialog import AlertDialog
from alerts import JOIN_URL_PREFIX, RestartAlerts
from api_server import ApiServer
import joiner
from monitor import Monitor
from overlay import Overlay
from grab_dialog import GrabDialog
from server_dialog import ServersDialog
from tray import Tray
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
        self._counter_on = bool(cfg.get("show_counter", True))   # 人数条显隐，上次的选择
        self.toast = Overlay(cfg["restart_pos"], cfg["restart_font"],
                             duration=cfg["restart_seconds"],
                             color=cfg["restart_color"], bold=True,
                             alpha=cfg["alpha"], clickable=True)
        self._toast_items = []          # 当前这条提示里的 (服名, 地址)，最后一个 = 晚重启的
        self.tray = Tray(app)
        self._build_menu()

        self.counter.set_visible(self._counter_on)
        self._draw_counter(self.monitor.snapshot, ensure_shown=False)
        self._position_toast(*cfg["counter_pos"])
        self.tray.tip("SL Restart · 启动中…")
        self.tray.show()
        self.tray.on_click(lambda: self._toggle_counter(not self.counter.isVisible()))
        self.toast.on_click = self._join_latest

        self.monitor.start()
        self.api = ApiServer(self.monitor, lambda: self.cfg)
        if self.cfg.get("third_party_api"):
            self.api.start(int(self.cfg.get("api_port") or 17777))
        self.timer = QTimer()
        self.timer.timeout.connect(self._sync)
        self.timer.start(SYNC_MS)

    # ------------------------------ 菜单 ------------------------------

    def _build_menu(self):
        t = self.tray
        t.add("counter", "显示人数条",
              lambda: self._toggle_counter(t.acts["counter"].isChecked()),
              checkable=True, checked=self._counter_on)
        t.add("timer", "显示回合时长",
              lambda: self._toggle_timer(t.acts["timer"].isChecked()),
              checkable=True, checked=bool(self.cfg.get("show_timer", True)))
        t.add("move_counter", "移动人数条", self._toggle_move_counter,
              checkable=True)
        t.add("pause", "暂停监控", lambda: self._toggle_pause())
        t.add("refresh", "立即刷新", self.monitor.refresh_now)
        t.add("grab", "自动挤服", self._toggle_grab, checkable=True)
        t.sep()
        t.add("servers", "编辑服务器清单…", self._edit_servers)
        t.add("alerts", "提醒设置…", self._edit_alerts)
        t.add("api", "第三方适配", self._toggle_api,
              checkable=True, checked=bool(self.cfg.get("third_party_api")))
        t.add("autostart", "开机自启", self._toggle_autostart,
              checkable=True, checked=autostart.is_enabled())
        t.sep()
        t.add("about", "关于", self._about)
        t.add("quit", "退出程序", self.quit)

    def _toggle_counter(self, flag):
        """托盘菜单 / 左键切人数条，并记住选择（下次启动按这个状态来）。"""
        flag = bool(flag)
        self._counter_on = flag
        self.counter.set_visible(flag)
        self.tray.set_checked("counter", flag)
        self.tray.tip("SL Restart · " + self.monitor.snapshot["text"])
        self.cfg["show_counter"] = flag
        try:
            config.save(self.cfg)
        except Exception as e:
            config.log("[ui] 保存 settings.json 失败：%r" % e)
        config.log("[ui] 显示人数条=%s" % flag)

    def _toggle_timer(self, flag):
        """托盘菜单切「显示回合时长」，并记住选择。"""
        self.cfg["show_timer"] = bool(flag)
        self.tray.set_checked("timer", self.cfg["show_timer"])
        self.monitor.set_show_timer(self.cfg["show_timer"])
        self._draw_counter(self.monitor.snapshot, ensure_shown=False)   # 立刻重画，不用等下一轮
        try:
            config.save(self.cfg)
        except Exception as e:
            config.log("[ui] 保存 settings.json 失败：%r" % e)
        config.log("[ui] 显示回合时长=%s" % self.cfg["show_timer"])

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

    def _toggle_grab(self):
        """自动挤服：勾上弹配置框，确认后开启；取消勾选立即停止。

        命中一次（进服 + Toast）后监控端自动关闭，菜单勾选由 _sync 同步回去。
        """
        checked = self.tray.acts["grab"].isChecked()
        if not checked:
            self.monitor.set_grab([], False)
            self.tray.tip("SL Restart · 自动挤服已关闭")
            config.log("[ui] 自动挤服已关闭")
            return
        dlg = GrabDialog(self.cfg.get("grab_servers"), self.cfg["servers"])
        if dlg.exec() != GrabDialog.DialogCode.Accepted:
            self.tray.set_checked("grab", False)
            return
        servers = dlg.result_servers()
        enabled = [s for s in servers if s["enabled"]]
        if not enabled:
            QMessageBox.warning(None, "自动挤服", "至少要勾选一台要挤的服务器。")
            self.tray.set_checked("grab", False)
            return
        self.cfg["grab_servers"] = servers
        try:
            config.save(self.cfg)
        except Exception as e:
            config.log("[ui] 保存 settings.json 失败：%r" % e)
        self.monitor.set_grab(enabled, True)
        self.tray.tip("SL Restart · 自动挤服监控中（%d 台）" % len(enabled))
        config.log("[ui] 自动挤服已开启：%s"
                   % ["%s%s" % (s["addr"], "（阈值%d）" % s["threshold"] if s["threshold"] else "")
                      for s in enabled])

    def _handle_grab_hit(self, hit):
        """挤服命中：立即 steam 直连 + 弹 Windows Toast（无论通知开关是否打开）。"""
        ok, msg = joiner.join(hit["addr"])
        self.alerts.show_notification(
            "自动挤服",
            "[%s] 有空位：%d/%d（低于阈值 %s），已自动加入"
            % (hit["name"], hit["players"], hit["max"], hit["threshold"]))
        config.log("[ui] 自动挤服命中 %s(%s) %d/%d < %s：ok=%s %s"
                   % (hit["name"], hit["addr"], hit["players"], hit["max"],
                      hit["threshold"], ok, msg))

    def _save_counter_position(self, x, y):
        self.cfg["counter_pos"] = [x, y]
        self._position_toast(x, y)
        self.cfg["restart_pos"] = [self.toast.x(), self.toast.y()]
        try:
            config.save(self.cfg)
        except Exception as e:
            config.log("[ui] 保存人数条位置失败：%r" % e)

    def _position_toast(self, x, y):
        """重启提示始终跟在人数条正下方（间距 >= 38px），不独立摆放。"""
        gap = max(38, self.counter.height() + 8)
        self.toast.set_position((x, y + gap))

    def _edit_alerts(self):
        dlg = AlertDialog(self.cfg, self.alerts)
        if dlg.exec() != AlertDialog.DialogCode.Accepted:
            return
        settings = dlg.result_settings()
        updated = dict(self.cfg, **settings)
        timing_changed = any(updated.get(key) != self.cfg.get(key)
                             for key in ("round_min_players", "round_start_delay"))
        try:
            config.save(updated)
        except Exception as e:
            config.log("[ui] 保存提醒设置失败：%r" % e)
            QMessageBox.warning(None, "保存失败", "提醒设置未保存，请检查程序目录是否可写。")
            return
        self.cfg.update(settings)
        if timing_changed:
            self.monitor.reset_round_timers()               # 参数变了：重新观察，不沿用旧时长
            self._draw_counter(self.monitor.snapshot, ensure_shown=False)
        if not self.cfg.get("show_toast", True):            # 刚关掉：把还挂着的提示一起收掉
            self._toast_items = []
            self.toast.hide()
        if not self.cfg["sound_enabled"]:
            self.alerts.stop_sound()
        config.log("[ui] 提醒设置：提示框=%s 开局门槛=%s 等待=%s秒"
                   % (self.cfg.get("show_toast"), self.cfg.get("round_min_players"),
                      self.cfg.get("round_start_delay")))

    def _toggle_api(self):
        """第三方适配：开关本地数据端口（只绑 127.0.0.1），供灵动岛等第三方程序读快照。"""
        want = bool(self.tray.acts["api"].isChecked())
        ok = False
        if want:
            ok = self.api.start(int(self.cfg.get("api_port") or 17777))
        else:
            self.api.stop()
        self.cfg["third_party_api"] = bool(want and ok)
        self.tray.set_checked("api", self.cfg["third_party_api"])
        try:
            config.save(self.cfg)
        except Exception as e:
            config.log("[ui] 保存 settings.json 失败：%r" % e)
        self.tray.tip("SL Restart · " + (
            "第三方适配已开启（127.0.0.1:%d）" % self.cfg["api_port"]
            if self.cfg["third_party_api"]
            else ("端口 %s 被占用，开启失败" % self.cfg["api_port"] if want
                  else "第三方适配已关闭")))
        config.log("[ui] 第三方适配=%s" % self.cfg["third_party_api"])

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
        self._draw_counter(self.monitor.snapshot, ensure_shown=False)   # 显隐听菜单的，不硬弹回来
        config.log("[ui] 服务器清单已更新：%s" % [s["addr"] for s in servers])

    def _about(self):
        config.log("[ui] 打开关于窗口")
        AboutDialog(self.tray.icon.icon()).exec()

    def quit(self):
        """退出：每个环节都限时，绝不让「退出程序」卡在那里没反应。"""
        if getattr(self, "_quitting", False):
            return
        self._quitting = True
        t0 = time.time()
        config.log("[ui] 请求退出")
        try:
            self.timer.stop()                       # 先停 200ms 心跳
            self.tray.icon.hide()                   # 托盘图标立刻消失，不用等 Qt 收尾
            self.counter.hide()
            self.toast.hide()
        except Exception as e:
            config.log("[ui] 退出时隐藏界面失败（忽略）：%r" % e)
        # 兜底：清理阶段万一被 Qt / 子进程拖住，最多 5 秒就强制结束进程
        threading.Thread(target=self._force_exit_after, args=(5.0,),
                         name="quit-watchdog", daemon=True).start()
        self.monitor.stop()
        self.api.stop()
        self.alerts.stop()
        config.log("[ui] 清理完成：%.2fs" % (time.time() - t0))
        self.app.quit()

    @staticmethod
    def _force_exit_after(secs):
        time.sleep(secs)
        config.log("[ui] 退出超时，强制结束进程")
        os._exit(0)

    # ------------------------------ 同步 ------------------------------

    def _draw_counter(self, snap, ensure_shown=True):
        """把快照画到人数条上：默认白字，回合时长超阈值那一段用黄色。"""
        yellow = self.cfg.get("timer_color", "#FFD400")
        segs = [(text, yellow if kind else None) for text, kind in snap.get("segments") or []]
        self.counter.set_segments(segs, ensure_shown=ensure_shown)

    def _sync(self):
        snap = self.monitor.snapshot
        self._draw_counter(snap, ensure_shown=False)   # 显隐由菜单/托盘左键决定
        self.tray.tip("SL Restart · " + (snap["text"] or "…"))
        restarts = self.monitor.take_restarts()
        if restarts:
            self._push_restart(restarts)
        hits = self.monitor.take_grab_hits()
        if hits:
            for hit in hits:
                self._handle_grab_hit(hit)
            self.tray.set_checked("grab", False)       # 挤到服自动关闭，菜单同步
            self.tray.tip("SL Restart · 已自动挤进 %s" % hits[-1]["name"])

    def _push_restart(self, restarts):
        """重启提示：同一轮里几台服一起重启，就把服名并排显示成一条。

        不能一台一条 pop——后一条会盖掉前一条（信息丢失）。所以攒成：
        `[1服] [3服] 重启`；提示还在显示时又来一台，就并进去并把 10 秒倒计时重置。

        整条提示**可点击**：点一下用 steam://connect 直连**晚重启**的那台
        （事件顺序里的最后一个，最后重启 = 排队最靠前）；移动人数条期间不响应。
        「启用重启提示框」关掉时只记日志，不弹框（声音 / 系统通知照旧）。
        """
        if not self.cfg.get("show_toast", True):
            self._toast_items = []
            if self.toast.isVisible():
                self.toast.hide()
            config.log("[ui] 重启提示框已关闭，跳过弹框：%s"
                       % " ".join(n for n, _a in restarts))
            self.alerts.notify_restart(restarts, self.cfg)
            return
        if not self.toast.isVisible():          # 上一条提示已经放完了，重新开始攒
            self._toast_items = []
        for item in restarts:
            if item not in self._toast_items:
                self._toast_items.append(item)
        self._position_toast(self.counter.x(), self.counter.y())
        self.toast.pop("%s 重启" % " ".join("[%s]" % n for n, _a in self._toast_items))
        self.alerts.notify_restart(restarts, self.cfg)
        config.log("[ui] 弹重启提示：%s（点击将进 %s）"
                   % (" ".join(n for n, _a in self._toast_items),
                      "%s(%s)" % self._toast_items[-1]))

    def _join_latest(self):
        """点击重启提示 -> steam://connect 晚重启的那台服。成功不打扰，失败才提醒。"""
        if not self._toast_items or self.counter._movable:
            return                              # 没有可进的服；或正在拖人数条，拖动优先
        name, addr = self._toast_items[-1]
        ok, msg = joiner.join(addr)
        if not ok:
            self.tray.icon.showMessage("SL Restart", "加入 %s 失败：%s" % (name, msg),
                                       QSystemTrayIcon.MessageIcon.Warning, 5000)
        config.log("[ui] 点击重启提示进服 %s(%s)：ok=%s %s" % (name, addr, ok, msg))


def _handle_join_url(url):
    """Windows 通知按钮的协议回调：第二个实例只做一件事——steam 直连，然后退出。

    不碰 Qt、不抢单实例锁，主程序照常在托盘里跑。
    """
    addr = url[len(JOIN_URL_PREFIX):].strip("/")
    if config.parse_addr(addr):
        ok, msg = joiner.join(addr)
        config.log("[app] 通知按钮进服 %s：ok=%s %s" % (addr, ok, msg))
    else:
        config.log("[app] 通知按钮传来无效地址：%r" % url)
    return 0


def main(argv=None):
    args_list = list(sys.argv[1:] if argv is None else argv)
    for arg in args_list:                    # 通知按钮「进服」走这里，发完即退
        if str(arg).startswith(JOIN_URL_PREFIX):
            return _handle_join_url(str(arg))

    ap = argparse.ArgumentParser(prog="SL Restart", description="SCP:SL 服务器重启提示")
    ap.add_argument("--smoke", type=float, default=0,
                    help="开发用：跑 N 秒后自动退出（验证用）")
    ap.add_argument("--about-grab", metavar="PNG", default="",
                    help="开发用：把关于窗口画成 PNG 后退出（验证打包后可用）")
    ap.add_argument("--alerts-grab", metavar="PNG", default="",
                    help="开发用：把提醒设置画成 PNG 后退出（不播放声音或发送通知）")
    ap.add_argument("--instance", default="SL_Restart_instance",
                    help="开发用：单实例锁的名字（并行测试时不撞车）")
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

    guard = single_instance(args.instance)
    if guard is None:
        config.log("[app] 已有实例在运行，本次启动退出")
        return 0

    ui = App(cfg, app)
    config.log("[app] 启动 v%s：%s" % (config.VERSION, [s["addr"] for s in cfg["servers"]]))
    if args.smoke:
        QTimer.singleShot(int(args.smoke * 1000), ui.quit)
    try:
        return app.exec()
    finally:
        ui.monitor.stop()
        config.log("[app] 退出")


if __name__ == "__main__":
    sys.exit(main())
