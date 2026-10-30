# -*- coding: utf-8 -*-
"""开发用：离屏把界面逻辑跑一遍（托盘菜单回调 / 人数条显隐 / 编辑对话框 / 关于窗口 / 重启弹窗）。

不联网也能跑；不会写注册表（只读查询开机自启状态）。
用法（Windows Python）：python tools\\gui_check.py
"""

import os
import sys
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt                            # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox     # noqa: E402

import autostart                                          # noqa: E402
import config                                             # noqa: E402
import grab_dialog as grab_dialog_mod                     # noqa: E402
import joiner                                             # noqa: E402
import overlay as overlay_mod                             # noqa: E402
import sl_restart                                         # noqa: E402
from about import LINKS, AboutDialog                      # noqa: E402
from alert_dialog import AlertDialog                      # noqa: E402
from server_dialog import ServersDialog                   # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "   -> %s" % (extra,)))
    if not cond:
        FAILS.append(name)


def pump(app, seconds=0.0):
    end = time.time() + seconds
    while True:
        app.processEvents()
        if time.time() >= end:
            return
        time.sleep(0.02)


def main():
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    tmp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "build_shots", "gui_check_data")     # 别把测试数据写进项目根目录
    os.makedirs(tmp, exist_ok=True)
    config.set_base_dir(tmp)
    try:
        os.remove(config.path("settings.json"))      # 每次从默认配置开始，测试可重复
    except FileNotFoundError:
        pass
    cfg = config.load()
    ui = sl_restart.App(cfg, app)
    pump(app, 0.5)
    t = ui.tray

    keys = ("counter", "timer", "move_counter", "pause", "refresh", "grab",
            "servers", "alerts", "api", "autostart", "about", "quit")
    check("托盘菜单十二项齐全",
          set(t.acts) == set(keys),
          sorted(t.acts))
    check("菜单文案：显示人数条 / 显示回合时长 / 移动人数条 / 暂停监控 / 立即刷新 / 自动挤服 / "
          "编辑服务器清单… / 提醒设置… / 第三方适配 / 开机自启 / 关于 / 退出程序",
          [t.acts[k].text() for k in keys]
          == ["显示人数条", "显示回合时长", "移动人数条", "暂停监控", "立即刷新", "自动挤服",
              "编辑服务器清单…", "提醒设置…", "第三方适配", "开机自启", "关于", "退出程序"],
          [t.acts[k].text() for k in keys])
    check("「显示回合时长」默认勾选（默认全开）", t.acts["timer"].isChecked())
    check("「显示人数条」默认勾选（默认显示）", t.acts["counter"].isChecked())
    check("「第三方适配」默认关闭（默认不起本地端口）",
          not t.acts["api"].isChecked() and not ui.api.running)
    check("人数条默认显示、重启提示默认不显示",
          ui.counter.isVisible() and not ui.toast.isVisible())

    # ---- 第三方适配：开 / 关都能立刻生效，并且记住选择 ----
    import socket as socket_mod
    _s = socket_mod.socket()
    _s.bind(("127.0.0.1", 0))
    ui.cfg["api_port"] = _s.getsockname()[1]
    _s.close()
    t.acts["api"].trigger()
    pump(app, 0.3)
    body = open(config.path("settings.json"), encoding="utf-8").read()
    check("打开「第三方适配」：端口起来、菜单勾上、状态写进 settings.json",
          ui.api.running and t.acts["api"].isChecked()
          and '"third_party_api": true' in body, body[:200])
    t.acts["api"].trigger()
    pump(app, 0.3)
    body = open(config.path("settings.json"), encoding="utf-8").read()
    check("关闭「第三方适配」：端口停掉、菜单取消、状态写进 settings.json",
          (not ui.api.running) and (not t.acts["api"].isChecked())
          and '"third_party_api": false' in body, body[:200])

    ui.toast.pop("[3服] 重启")
    pump(app, 0.2)
    check("人数条在 (10,10)、重启提示在 (10,48)",
          (ui.counter.x(), ui.counter.y()) == (10, 10) and (ui.toast.x(), ui.toast.y()) == (10, 48),
          "人数条 %s 提示 %s" % ((ui.counter.x(), ui.counter.y()), (ui.toast.x(), ui.toast.y())))
    gap = ui.toast.y() - (ui.counter.y() + ui.counter.height())
    check("重启提示不会盖住人数条（间距 > 0）", gap > 0, "间距 %d px" % gap)
    check("重启提示是红色粗体",
          ui.toast.font().bold() and ui.toast.color.name().lower() == "#ff3b3b",
          "%s / bold=%s" % (ui.toast.color.name(), ui.toast.font().bold()))
    ui.toast.pop("[3服] 重启", seconds=0.4)
    pump(app, 1.3)
    check("提示到点自动消失（这里用 0.4 秒加速验证淡出+隐藏）", not ui.toast.isVisible())

    enabled = autostart.is_enabled()
    print("   （HKCU 开机自启项当前：%s；若勾选会写：%s）"
          % ("存在" if enabled else "不存在", autostart._target()))
    check("开机自启勾选状态 = 注册表真实状态（自检没有写过注册表）",
          t.acts["autostart"].isChecked() == enabled)

    t.acts["refresh"].trigger()
    pump(app, 0.4)
    check("‘立即刷新’能触发一轮采集（快照有更新时间）",
          bool(ui.monitor.snapshot["stamp"]), ui.monitor.snapshot["stamp"])

    t.acts["pause"].trigger()
    pump(app, 0.2)
    check("暂停：菜单变‘继续监控’且监控线程挂起",
          ui.monitor.paused and t.acts["pause"].text() == "继续监控", t.acts["pause"].text())
    t.acts["pause"].trigger()
    pump(app, 0.2)
    check("继续：菜单变回‘暂停监控’且恢复采集",
          (not ui.monitor.paused) and t.acts["pause"].text() == "暂停监控")

    t.acts["counter"].trigger()               # trigger() 自己会翻转勾选
    pump(app, 0.2)
    check("取消勾选 -> 人数条隐藏", not ui.counter.isVisible())
    for _ in range(5):
        ui._sync()
        pump(app, 0.05)
    check("连跑 5 次心跳也不会把隐藏的人数条弹回来", not ui.counter.isVisible())

    # 回归：服务器重启弹提示时，不许把用户关掉的人数条弹回来
    ui.toast.set_visible(False)
    with ui.monitor._lock:
        ui.monitor._pending.append(("3服", "127.0.0.1:7779"))
    ui._sync()
    pump(app, 0.2)
    check("重启提示弹出时人数条仍然隐藏（提示自己正常显示）",
          ui.toast.isVisible() and not ui.counter.isVisible(),
          "人数条=%s 提示=%s" % (ui.counter.isVisible(), ui.toast.isVisible()))
    ui.toast.set_visible(False)
    ui.toast._timer.stop()

    # 回归：编辑服务器清单（保存后重画）也不许硬把人数条打开
    real_exec_srv = ServersDialog.exec
    ServersDialog.exec = lambda self: ServersDialog.DialogCode.Accepted
    try:
        t.acts["servers"].trigger()
        pump(app, 0.2)
    finally:
        ServersDialog.exec = real_exec_srv
    check("保存服务器清单后人数条仍然隐藏", not ui.counter.isVisible())
    body = open(config.path("settings.json"), encoding="utf-8").read()
    check("「显示人数条」关掉的状态写进了 settings.json",
          '"show_counter": false' in body, body[:200])
    check("重新读配置：人数条是关的（下次启动不会自己冒出来）",
          config.load()["show_counter"] is False)

    t.acts["counter"].trigger()
    pump(app, 0.2)
    check("重新勾选 -> 人数条回来", ui.counter.isVisible())

    # 回归：数字变了、但像素宽度没变时，也必须请求重绘
    #（否则画面会一直停在旧数字上——就是"提示完重启后人数条不再刷新"那个 bug）
    ui.timer.stop()                                   # 停掉 200ms 心跳，避免干扰这段判定
    real_update = overlay_mod.Overlay.update
    calls = {"n": 0}

    def spy(self, *a, **k):
        calls["n"] += 1
        return real_update(self, *a, **k)

    overlay_mod.Overlay.update = spy
    try:
        ui.counter.set_text("[1服] 41人  |  [3服] 25人  |  [5服] 36人")
        w = ui.counter.width()
        n0 = calls["n"]
        ui.counter.set_text("[1服] 42人  |  [3服] 25人  |  [5服] 36人")   # 只换数字，宽度不变
        w2 = ui.counter.width()
        n1 = calls["n"]
    finally:
        overlay_mod.Overlay.update = real_update
        ui.timer.start(sl_restart.SYNC_MS)
    check("宽度不变、只换数字时也会重绘（旧版这里 0 次重绘）",
          w == w2 and n1 > n0, "宽度 %s->%s，update 次数 %s->%s" % (w, w2, n0, n1))

    # ---- 回合时长：显示格式 / 超 20 分钟变黄 / 菜单开关 ----
    ui.monitor.set_paused(True)
    pump(app, 0.4)                                    # 等在飞的那一轮跑完
    ui.monitor._servers = [{"addr": "1.1.1.1:7777", "name": "1服", "timer": True},
                           {"addr": "2.2.2.2:7779", "name": "3服", "timer": True}]
    ui.monitor._players = {"1.1.1.1:7777": 40, "2.2.2.2:7779": 2}
    ui.monitor._timer = {"1.1.1.1:7777": 1260.0, "2.2.2.2:7779": 120.0}
    ui.monitor._round_phase = {"1.1.1.1:7777": "running", "2.2.2.2:7779": "running"}
    ui.monitor._show_timer = True
    ui.monitor.snapshot = ui.monitor._make_snapshot()
    ui._draw_counter(ui.monitor.snapshot)
    check("人数条上带回合时长（M:SS），跟在人数后面",
          ui.counter.text == "[1服] 40人 21:00  |  [3服] 2人 2:00", ui.counter.text)
    cols = [c.name() if c else None for _, c in ui.counter.lines[0]]
    check("超过 20 分钟的时长变黄、没超的保持白色",
          cols.count("#ffd400") == 1 and cols.count(None) >= 3, cols)

    t.acts["timer"].trigger()                          # 关掉总开关
    pump(app, 0.2)
    ui._draw_counter(ui.monitor.snapshot)
    check("关掉「显示回合时长」后立刻不带时长",
          ui.counter.text == "[1服] 40人  |  [3服] 2人", ui.counter.text)
    body = open(config.path("settings.json"), encoding="utf-8").read()
    check("开关状态写进了 settings.json", '"show_timer": false' in body, body[:160])
    t.acts["timer"].trigger()                          # 再打开
    pump(app, 0.2)
    ui._draw_counter(ui.monitor.snapshot)
    check("重新打开后时长回来", "21:00" in ui.counter.text, ui.counter.text)

    ui.monitor.set_paused(False)
    ui.monitor.set_servers(cfg["servers"])              # 换回真实清单，后面的用例继续用

    with ui.monitor._lock:
        ui.monitor._pending.append(("3服", "127.0.0.1:7779"))
    ui._sync()
    pump(app, 0.2)
    check("重启事件 -> 弹窗显示「[3服] 重启」",
          ui.toast.isVisible() and ui.toast.text == "[3服] 重启",
          "%s / visible=%s" % (ui.toast.text, ui.toast.isVisible()))

    # ---- 多台服同时重启：服名并排、不丢信息 ----
    # 旧逻辑（一台一条 pop）会被后一条盖掉，这里顺手复现一下
    ui.toast.set_visible(False)
    ui.toast.pop("[1服] 重启")
    ui.toast.pop("[3服] 重启")
    check("（旧逻辑对照）俩服同时重启只剩后一个：[3服] 重启",
          ui.toast.text == "[3服] 重启", ui.toast.text)

    ui.toast.set_visible(False)
    ui.toast._timer.stop()
    with ui.monitor._lock:
        ui.monitor._pending.extend([("1服", "127.0.0.1:7777"), ("3服", "127.0.0.1:7779")])
    ui._sync()
    pump(app, 0.2)
    check("同一轮两台服重启 -> 并排显示「[1服] [3服] 重启」",
          ui.toast.isVisible() and ui.toast.text == "[1服] [3服] 重启", ui.toast.text)

    # ---- 点击重启提示 = steam://connect 晚重启的那台 ----
    sent = []
    old_join = joiner.join
    joiner.join = lambda addr: sent.append(addr) or (True, "fake")
    try:
        ui.toast.on_click()
    finally:
        joiner.join = old_join
    check("点击重启提示 -> 直连晚重启的那台（3服）",
          sent == ["127.0.0.1:7779"], sent)

    # ---- 移动人数条期间，点击提示不进服（拖动优先），解锁后恢复 ----
    t.acts["move_counter"].trigger()
    sent_move = []
    joiner.join = lambda addr: sent_move.append(addr) or (True, "fake")
    try:
        ui.toast.on_click()
    finally:
        joiner.join = old_join
    check("移动人数条期间点击提示不进服", sent_move == [], sent_move)
    t.acts["move_counter"].trigger()
    joiner.join = lambda addr: sent_move.append(addr) or (True, "fake")
    try:
        ui.toast.on_click()
    finally:
        joiner.join = old_join
    check("解锁后点击恢复进服", sent_move == ["127.0.0.1:7779"], sent_move)

    # ---- 自动挤服：配置对话框（预填 / 校验 / 结果） ----
    main_servers = [{"addr": "180.188.21.179:7777", "name": "1服", "timer": True}]
    saved = [{"addr": "180.188.21.179:7777", "name": "1服", "enabled": True, "threshold": 30},
             {"addr": "127.0.0.1:9000", "name": "", "enabled": False, "threshold": None}]
    gd = grab_dialog_mod.GrabDialog(saved, main_servers)
    gd.show()
    check("挤服对话框：清单服 + 自定义服都列出（行数/勾选/阈值）",
          gd.table.rowCount() == 2
          and gd.table.item(0, 0).checkState() == Qt.CheckState.Checked
          and gd.table.item(0, 2).text() == "30"
          and gd.table.item(1, 1).text() == "127.0.0.1:9000",
          gd.table.rowCount())
    got = gd.result_servers()
    check("挤服对话框读出结果（地址规范化 / 阈值留空 = None）",
          got == [{"addr": "180.188.21.179:7777", "name": "1服", "enabled": True,
                   "threshold": 30},
                  {"addr": "127.0.0.1:9000", "name": "", "enabled": False,
                   "threshold": None}], got)

    real_warning = QMessageBox.warning
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    try:
        bad = grab_dialog_mod.GrabDialog([], main_servers)
        bad._add_row("banana")
        bad.accept()
        check("挤服对话框：非法地址不放行", bad.result() == 0, bad.result())
        bad.table.item(0, 1).setText("127.0.0.1:9000")
        bad._add_row("127.0.0.1:9000")
        bad.accept()
        check("挤服对话框：重复地址不放行", bad.result() == 0, bad.result())
        bad.table.removeRow(1)
        bad.table.item(0, 2).setText("5000")
        bad.accept()
        check("挤服对话框：阈值超范围不放行", bad.result() == 0, bad.result())
    finally:
        QMessageBox.warning = real_warning

    # ---- 自动挤服：托盘开关 + 命中（进服 + Toast + 自动取消勾选） ----
    def accept_grab(dlg):
        dlg.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        return grab_dialog_mod.GrabDialog.DialogCode.Accepted

    with patch.object(grab_dialog_mod.GrabDialog, "exec", accept_grab), \
            patch.object(ui.monitor, "set_grab") as set_grab:
        ui.tray.acts["grab"].trigger()          # trigger() 自己会翻转勾选 -> 打开
        set_grab.assert_called_once()
        check("自动挤服开启：菜单保持勾选、监控已配置", ui.tray.acts["grab"].isChecked())
    ui.tray.acts["grab"].trigger()              # 再点一次 -> 关闭（不会弹对话框）
    check("自动挤服关闭：菜单取消勾选", not ui.tray.acts["grab"].isChecked())

    ui.monitor._grab_pending.append({"addr": "180.188.21.179:7779", "name": "3服",
                                     "players": 0, "max": 45, "threshold": 45})
    sent_hit = []
    old_join = joiner.join
    joiner.join = lambda addr: sent_hit.append(addr) or (True, "fake")
    notified = []
    with patch.object(ui.alerts, "show_notification",
                      lambda *a, **k: notified.append(a)):
        try:
            ui._sync()
            pump(app, 0.2)
        finally:
            joiner.join = old_join
    check("挤服命中 -> 直连那一台 + 弹 Toast + 菜单自动取消勾选",
          sent_hit == ["180.188.21.179:7779"] and notified
          and not ui.tray.acts["grab"].isChecked(),
          (sent_hit, bool(notified), ui.tray.acts["grab"].isChecked()))

    pump(app, 1.5)                                    # 让倒计时先走掉一部分
    left_before = ui.toast._timer.remainingTime()
    with ui.monitor._lock:
        ui.monitor._pending.append(("5服", "127.0.0.1:7781"))
    ui._sync()
    pump(app, 0.2)
    left_after = ui.toast._timer.remainingTime()
    check("提示期间又来一台 -> 三个服名并排",
          ui.toast.text == "[1服] [3服] [5服] 重启", ui.toast.text)
    check("新重启到达时倒计时重置为 10 秒",
          left_after > left_before + 500,
          "重置前剩 %dms -> 重置后剩 %dms" % (left_before, left_after))

    # 提示放完之后，旧服名不许残留
    ui.toast.duration = 0.4
    with ui.monitor._lock:
        ui.monitor._pending.append(("3服", "127.0.0.1:7779"))
    ui._sync()
    pump(app, 1.4)
    check("提示自动消失后旧服名被清掉", not ui.toast.isVisible())
    with ui.monitor._lock:
        ui.monitor._pending.append(("1服", "127.0.0.1:7777"))
    ui._sync()
    pump(app, 0.2)
    check("下一次重启只显示本次的服名「[1服] 重启」",
          ui.toast.text == "[1服] 重启", ui.toast.text)
    ui.toast.duration = cfg["restart_seconds"]
    ui.toast.set_visible(False)
    ui.toast._timer.stop()

    # ---- 提醒设置：提示框开关 + 开局估算参数 ----
    adlg = AlertDialog(ui.cfg, ui.alerts)
    check("提醒设置：默认启用重启提示框", adlg.toast_enabled.isChecked())
    check("提醒设置：开局估算默认 2 人 / 45 秒",
          (adlg.round_min_players.value(), adlg.round_start_delay.value()) == (2, 45),
          "%s / %s" % (adlg.round_min_players.value(), adlg.round_start_delay.value()))
    adlg.round_min_players.setValue(3)
    adlg.round_start_delay.setValue(10)
    adlg.toast_enabled.setChecked(False)
    st = adlg.result_settings()
    check("提醒设置：结果带回提示框与估算参数",
          st["show_toast"] is False and st["round_min_players"] == 3
          and st["round_start_delay"] == 10, st)
    adlg.deleteLater()

    ui.cfg["show_toast"] = False                       # 关掉提示框：重启只走日志/通知
    ui.toast.set_visible(False)
    with ui.monitor._lock:
        ui.monitor._pending.append(("1服", "127.0.0.1:7777"))
    ui._sync()
    pump(app, 0.3)
    check("关掉「启用重启提示框」后不再弹提示", not ui.toast.isVisible())
    ui.cfg["show_toast"] = True

    ui.monitor.set_servers([{"addr": "127.0.0.1:1", "name": "测试服"}])
    pump(app, 0.5)
    check("换清单后人数条立刻换成新清单（打不通显示 -人）",
          ui.counter.text == "[测试服] -人", ui.counter.text)

    # ---- 关于窗口 ----
    real_exec = AboutDialog.exec
    AboutDialog.exec = lambda self: 0                  # 别真弹模态框把自检卡住
    try:
        t.acts["about"].trigger()
        pump(app, 0.2)
        opened = True
    except Exception as e:                             # noqa: BLE001
        opened = False
        print("   打开关于窗口时异常：%r" % e)
    finally:
        AboutDialog.exec = real_exec
    check("点‘关于’能打开窗口（自检里换成不阻塞的 exec）", opened)

    dlg = AboutDialog(ui.tray.icon.icon())
    check("关于窗口：标题 / 名称 / 版本",
          dlg.windowTitle() == "关于 SL Restart"
          and dlg.name_label.text() == "SL Restart"
          and dlg.version_label.text() == "版本 %s" % config.VERSION,
          "%s | %s | %s" % (dlg.windowTitle(), dlg.name_label.text(), dlg.version_label.text()))
    urls = [l.toolTip() for l in dlg.link_labels]
    check("关于窗口：三行链接、URL 正确、点击走系统浏览器",
          urls == [u for _, u in LINKS]
          and all(l.openExternalLinks() for l in dlg.link_labels)
          and all('href="%s"' % u in l.text() for (_, u), l in zip(LINKS, dlg.link_labels)),
          urls)
    check("关于窗口：带程序图标", not dlg.windowIcon().isNull())
    check("关于窗口：精简（尺寸不过分大）",
          dlg.sizeHint().width() < 460 and dlg.sizeHint().height() < 320,
          (dlg.sizeHint().width(), dlg.sizeHint().height()))
    dlg.reject()
    check("关于窗口能关掉", not dlg.isVisible())

    real_warning = QMessageBox.warning
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    try:
        dlg = ServersDialog(cfg["servers"])
        check("编辑对话框读出 3 台服（地址 / 服名 / 显示时长 三列）",
              dlg.table.rowCount() == 3 and dlg.table.columnCount() == 3
              and dlg.table.horizontalHeaderItem(2).text() == "显示时长")
        check("「显示时长」默认全部勾选",
              all(dlg.table.item(r, 2).checkState() == Qt.CheckState.Checked for r in range(3)))
        dlg.table.item(0, 2).setCheckState(Qt.CheckState.Unchecked)      # 第一台不显示时长
        dlg._add_row("banana", "坏服")
        dlg.accept()
        check("填错地址不放行（对话框不关闭）", dlg.result() == 0, dlg.result())
        dlg.table.item(3, 0).setText("180.188.21.179:7783")
        dlg.table.item(3, 1).setText("7服")
        dlg.accept()
        got = dlg.result_servers()
        check("改对后放行，解析出 4 台（含新加的 7服）",
              dlg.result() == 1
              and got[-1] == {"addr": "180.188.21.179:7783", "name": "7服", "timer": True}, got)
        check("取消勾选的那台被记住（其余保持勾选）",
              got[0]["timer"] is False and all(g["timer"] for g in got[1:]), got)

        dup = ServersDialog([{"addr": "1.1.1.1:7777", "name": "a"},
                             {"addr": "1.1.1.1:7777", "name": "b"}])
        dup.accept()
        check("重复地址不放行", dup.result() == 0, dup.result())
    finally:
        QMessageBox.warning = real_warning

    # ---- 启动时按保存值来：上次关掉人数条，这次启动就不显示 ----
    cfg_off = config.load()
    cfg_off["show_counter"] = False
    ui2 = sl_restart.App(cfg_off, app)
    pump(app, 0.3)
    check("启动时按「显示人数条」的保存值来（保存为关 -> 启动就不显示）",
          (not ui2.counter.isVisible()) and (not ui2.tray.acts["counter"].isChecked()))
    ui2.monitor.stop()
    ui2.counter.hide()
    ui2.toast.hide()

    t0 = time.time()
    ui.quit()
    quit_dt = time.time() - t0
    check("退出（停监控 + 关端口 + 清理）在 1.5 秒内完成", quit_dt < 1.5, "%.2fs" % quit_dt)
    pump(app, 0.2)
    print("\n%s" % ("全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS)))
    sys.stdout.flush()
    os._exit(1 if FAILS else 0)          # 不跑 Qt 收尾：离屏窗口太多，退出码会乱


if __name__ == "__main__":
    sys.exit(main())
